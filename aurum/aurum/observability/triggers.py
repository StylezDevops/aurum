"""GovernanceScheduler — the configurable TRIGGER LAYER for governance maintenance.

Every governance maintenance operation (a between-turn scan, a periodic calibration, an
owner-review pass) should be (a) optionally on/off and (b) fired by whatever mechanism the
DEPLOYER chooses — not hardcoded. This is that layer: a registry of named tasks, each with an
`enabled` flag + a set of TRIGGERS, fully operator-configurable, fed to RS for background
execution, fail-safe per task.

Four trigger kinds (a task may declare several; the operator narrows them):
  • TURN     — run at a turn boundary (cheap, must complete the turn) → `on_turn()`.
  • SCHEDULE — run periodically (interval_seconds) → `tick(now)` submits the due ones to RS.
  • OPERATOR — run on an explicit operator command → `operator_run(name)`.
  • EVENT    — run when a matching event arrives → `dispatch_event(event)` (per-task event_match).

The deployer wires WHICH entrypoint fires WHEN (a host loop calls tick(); a turn boundary calls
on_turn(); a channel command calls operator_run(); an ingestion/alarm calls dispatch_event()).
SCHEDULE feeds RS (foreground work preempts; `drain()` executes the queued maintenance jobs).

This is NOT the Agent Orchestrator (AO, deferred): no multi-agent coordination, no authority, no
gate writes. It only decides WHEN the already-built, already-gated maintenance organs run, and a
task that raises is isolated (logged into its result, never crashes the loop).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..durability.clock import DAY

HOUR = 3600.0

# Trigger kinds (module constants, repo idiom).
TURN = "turn"
SCHEDULE = "schedule"
OPERATOR = "operator"
EVENT = "event"
_ALL_TRIGGERS = frozenset({TURN, SCHEDULE, OPERATOR, EVENT})


def _norm_triggers(triggers: Any) -> frozenset:
    ts = frozenset(str(t) for t in (triggers or ()))
    bad = ts - _ALL_TRIGGERS
    if bad:
        raise ValueError(f"unknown trigger(s) {sorted(bad)}; allowed: {sorted(_ALL_TRIGGERS)}")
    return ts


@dataclass
class _Task:
    name: str
    fn: Callable[[], Any]
    triggers: frozenset
    enabled: bool
    interval_seconds: Optional[float]
    rs_weight: Any
    event_match: Optional[Callable[[Dict[str, Any]], bool]] = None
    last_run: Optional[float] = field(default=None)


class GovernanceScheduler:
    """Registry of named maintenance tasks + the four trigger entrypoints. `rs` (a
    ResourceScheduler) receives SCHEDULE work as background jobs; `now_fn` is the wall-clock source
    (injectable for tests). Pure orchestration — it never gates, never widens authority."""

    def __init__(self, *, rs: Any = None, now_fn: Optional[Callable[[], float]] = None) -> None:
        self._tasks: Dict[str, _Task] = {}
        self._rs = rs
        self._now_fn = now_fn or time.time

    # -- registration / configuration --------------------------------------
    def register(self, name: str, fn: Callable[[], Any], *, triggers: Any,
                 enabled: bool = True, interval_seconds: Optional[float] = None,
                 rs_weight: Any = None, event_match: Optional[Callable] = None) -> None:
        """Declare a task and the triggers it SUPPORTS (its capability). `register` is the
        capability surface; `configure` (operator policy) sets what is actually on. A SCHEDULE-
        capable task needs an interval; an EVENT task may pass `event_match(event)->bool`."""
        ts = _norm_triggers(triggers)
        if SCHEDULE in ts and interval_seconds is None:
            raise ValueError(f"task {name!r} supports SCHEDULE but has no interval_seconds")
        self._tasks[name] = _Task(name=name, fn=fn, triggers=ts, enabled=bool(enabled),
                                   interval_seconds=interval_seconds,
                                   rs_weight=rs_weight if rs_weight is not None
                                   else {"priority": 0.2, "foreground": False},
                                   event_match=event_match)

    def configure(self, policy: Optional[Dict[str, Dict[str, Any]]]) -> None:
        """Operator policy overrides per task: {name: {enabled?, triggers?, interval_seconds?}}.
        Unknown task names are ignored (an operator may pre-declare a task a future build adds).
        A policy's triggers must be a subset of what the task REGISTERED as supported."""
        for name, cfg in (policy or {}).items():
            task = self._tasks.get(name)
            if task is None or not isinstance(cfg, dict):
                continue
            if "enabled" in cfg:
                task.enabled = bool(cfg["enabled"])
            if "triggers" in cfg:
                requested = _norm_triggers(cfg["triggers"])
                # never let policy GRANT a trigger the task did not register as supported
                task.triggers = requested & task.triggers if requested else task.triggers
            if "interval_seconds" in cfg and cfg["interval_seconds"] is not None:
                task.interval_seconds = float(cfg["interval_seconds"])

    def tasks(self) -> Dict[str, Dict[str, Any]]:
        """Introspection: {name: {enabled, triggers, interval_seconds, last_run}}."""
        return {n: {"enabled": t.enabled, "triggers": sorted(t.triggers),
                    "interval_seconds": t.interval_seconds, "last_run": t.last_run}
                for n, t in self._tasks.items()}

    # -- trigger entrypoints ------------------------------------------------
    def on_turn(self) -> Dict[str, Dict[str, Any]]:
        """Run every enabled TURN-triggered task NOW (synchronous — turn work must complete the
        turn). Returns {name: {ok, result|error}}."""
        return {t.name: self._run(t) for t in self._tasks.values()
                if t.enabled and TURN in t.triggers}

    def tick(self, now: Optional[float] = None) -> List[str]:
        """Feed RS: submit every enabled SCHEDULE task whose interval has elapsed as a background
        job (foreground preempts via RS). With no RS, runs them inline. Returns the names fired."""
        now = self._now_fn() if now is None else now
        fired: List[str] = []
        for task in self._tasks.values():
            if task.enabled and SCHEDULE in task.triggers and self._due(task, now):
                task.last_run = now            # gate resubmission until the next interval
                if self._rs is not None:
                    self._rs.submit(self._job_for(task), task.rs_weight)
                else:
                    self._run(task)
                fired.append(task.name)
        return fired

    def operator_run(self, name: str) -> Dict[str, Any]:
        """Run one task on an explicit OPERATOR command. Refuses a disabled or
        non-operator-triggerable task (no silent escalation)."""
        task = self._tasks.get(name)
        if task is None:
            raise KeyError(f"no governance task {name!r}")
        if not task.enabled:
            raise PermissionError(f"task {name!r} is disabled")
        if OPERATOR not in task.triggers:
            raise PermissionError(f"task {name!r} is not operator-triggerable")
        return self._run(task)

    def dispatch_event(self, event: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Run every enabled EVENT task whose `event_match(event)` is truthy (a task with no
        matcher runs on any event). A matcher that raises is treated as no-match (fail-safe)."""
        out: Dict[str, Dict[str, Any]] = {}
        for t in self._tasks.values():
            if t.enabled and EVENT in t.triggers and self._matches(t, event):
                out[t.name] = self._run(t)
        return out

    def drain(self, max_jobs: Optional[int] = None) -> List[Dict[str, Any]]:
        """Execute RS-queued maintenance jobs (callables) until the queue is empty or `max_jobs`
        is reached. Foreground work, if any, is dispatched first by RS. Fail-safe per job."""
        if self._rs is None:
            return []
        ran: List[Dict[str, Any]] = []
        while max_jobs is None or len(ran) < max_jobs:
            job = self._rs.next()
            if job is None:
                break
            if callable(job):
                try:
                    ran.append(job())
                except Exception as e:  # noqa: BLE001 — a bad job must not stop the drain
                    ran.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
            else:
                ran.append({"ok": False, "error": "non-callable job skipped"})
        return ran

    # -- internals ----------------------------------------------------------
    def _due(self, task: _Task, now: float) -> bool:
        if task.interval_seconds is None:
            return True
        return task.last_run is None or (now - task.last_run) >= task.interval_seconds

    def _job_for(self, task: _Task) -> Callable[[], Dict[str, Any]]:
        return lambda: self._run(task)

    @staticmethod
    def _matches(task: _Task, event: Dict[str, Any]) -> bool:
        if task.event_match is None:
            return True
        try:
            return bool(task.event_match(event))
        except Exception:
            return False

    def _run(self, task: _Task) -> Dict[str, Any]:
        # NB: `last_run` is the SCHEDULE marker (set by tick at submit time, so the interval gate is
        # stable even when a drained job executes later); _run must NOT re-stamp it with exec time.
        try:
            return {"name": task.name, "ok": True, "result": task.fn()}
        except Exception as e:  # noqa: BLE001 — isolate a failing task; never crash the loop
            return {"name": task.name, "ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# GOOD secure defaults — the kernel-resident maintenance tasks.
# ---------------------------------------------------------------------------
# All are READ-ONLY / never-block (so safe to enable), conservative cadence, SCHEDULE+OPERATOR by
# default. A deployer may add TURN or EVENT via policy (subset of each task's registered support).
# Cost/latency/external-dependency capabilities (model screener, HVP panel) are toggled at THEIR
# layers and default OFF — they are not scheduler tasks.
DEFAULT_POLICY: Dict[str, Dict[str, Any]] = {
    "memory_integrity": {"enabled": True, "triggers": [SCHEDULE, OPERATOR], "interval_seconds": DAY},
    "concentration":    {"enabled": True, "triggers": [SCHEDULE, OPERATOR], "interval_seconds": HOUR},
    "oi_calibration":   {"enabled": True, "triggers": [SCHEDULE, OPERATOR], "interval_seconds": DAY},
}


def default_governance_scheduler(kernel: Any, *, policy: Optional[Dict[str, Dict[str, Any]]] = None,
                                 now_fn: Optional[Callable[[], float]] = None) -> GovernanceScheduler:
    """Build a scheduler over a kernel with the GOOD defaults wired (read-only maintenance organs),
    then apply DEFAULT_POLICY, then the operator's `policy` overrides (last wins). Tasks are
    REGISTERED with their full supported trigger set (capability) but start disabled — DEFAULT_POLICY
    turns the good ones on. Organ-level tasks the kernel doesn't hold (LS.governance_gaps,
    FC.evaluate, CS-EQ scans) are registered by the deployer with `sched.register(...)`."""
    sched = GovernanceScheduler(rs=getattr(kernel, "rs", None), now_fn=now_fn)
    bg = {"priority": 0.2, "foreground": False}
    sched.register("memory_integrity", kernel.scan_memory_integrity,
                   triggers=[TURN, SCHEDULE, OPERATOR, EVENT], enabled=False,
                   interval_seconds=DAY, rs_weight=bg)
    sched.register("concentration", kernel.concentration_risks,
                   triggers=[TURN, SCHEDULE, OPERATOR], enabled=False,
                   interval_seconds=HOUR, rs_weight=bg)
    sched.register("oi_calibration", kernel.oi_calibration,
                   triggers=[SCHEDULE, OPERATOR], enabled=False,
                   interval_seconds=DAY, rs_weight=bg)
    sched.configure(DEFAULT_POLICY)
    if policy:
        sched.configure(policy)
    return sched


def run_maintenance_loop(run_once: Callable[[float], Any], *, interval_seconds: float,
                         now_fn: Optional[Callable[[], float]] = None,
                         sleep_fn: Optional[Callable[[float], None]] = None,
                         should_continue: Optional[Callable[[], bool]] = None,
                         max_iterations: Optional[int] = None) -> int:
    """Drive `run_once(now)` every `interval_seconds` — the LONG-LIVED HOST driver for SCHEDULE
    work (the ephemeral cage runs maintenance opportunistically at message time instead). Every
    knob is injectable so the loop is fully testable WITHOUT real waiting: `now_fn` (default
    time.time), `sleep_fn` (default time.sleep), `should_continue` (default forever — pass a flag
    to stop), `max_iterations` (default unbounded). Each iteration is FAIL-SAFE: an exception in
    `run_once` is swallowed so the loop survives a transient fault. Returns the iteration count;
    it does not sleep after the final iteration."""
    now = now_fn or time.time
    sleep = sleep_fn or time.sleep
    keep_going = should_continue or (lambda: True)
    n = 0
    while keep_going() and (max_iterations is None or n < max_iterations):
        try:
            run_once(now())
        except Exception:  # noqa: BLE001 — a transient maintenance fault must not kill the loop
            pass
        n += 1
        if keep_going() and (max_iterations is None or n < max_iterations):
            sleep(interval_seconds)
    return n
