"""RS — Resource Scheduler. Coordinates background-organ competition (priority).

Sits above CG (cost) deciding what runs now vs later. Foreground preempts
background; starvation guard escalates perpetually-deferred jobs. Background
organs must register here rather than spinning raw unmanaged threads.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

_GOAL_W = 2.0           # goal-relevance weight (GR health steers ordering)
_STARVE_BOOST = 1.0e6   # decisive escalation ONCE a job waits past starvation_ticks (L3) — so
#                         priority is respected normally and aging is a safety net, not a force
#                         that overrides priority within a few ticks.


class ResourceScheduler:
    """Priority queue over BACKGROUND work. Foreground (user-facing) work always preempts
    background. Score = priority + urgency + 2*goal_relevance - cost; a job that waits past
    `starvation_ticks` gets a DECISIVE escalation so it runs rather than starving — but aging does
    NOT override priority before then (the starvation guard is a safety net, not a priority
    inverter). Scope is small: it orders/admits
    background jobs against a shared budget — it does NOT replace CG's per-step routing or EG's
    branch bounding. Scheduling decisions are surfaced for EL logging at the integration layer."""

    ORGAN = "RS"

    def __init__(self, *, starvation_ticks: int = 10) -> None:
        self._queue: List[Dict[str, Any]] = []
        self._running: Optional[Dict[str, Any]] = None
        self._seq = itertools.count()
        self._tick = 0
        self.starvation_ticks = int(starvation_ticks)

    @staticmethod
    def _norm(weight: Any) -> Dict[str, Any]:
        if isinstance(weight, dict):
            return {"priority": float(weight.get("priority", 0)),
                    "urgency": float(weight.get("urgency", 0)),
                    "goal_relevance": float(weight.get("goal_relevance", 0)),
                    "cost": float(weight.get("cost", 0)),
                    "foreground": bool(weight.get("foreground", False))}
        return {"priority": float(weight or 0), "urgency": 0.0, "goal_relevance": 0.0,
                "cost": 0.0, "foreground": False}

    def submit(self, job: Any, weight: Any) -> None:
        self._queue.append({"job": job, "w": self._norm(weight),
                            "submit_tick": self._tick, "seq": next(self._seq)})

    def _score(self, e: Dict[str, Any]) -> float:
        w = e["w"]
        base = w["priority"] + w["urgency"] + _GOAL_W * w["goal_relevance"] - w["cost"]
        # Starvation guard: aging has NO influence until a job has waited past the threshold, then
        # it escalates DECISIVELY — priority is honoured normally; aging is a safety net (L3).
        age = self._tick - e["submit_tick"]
        if age >= self.starvation_ticks:
            return base + _STARVE_BOOST * (age - self.starvation_ticks + 1)
        return base

    def next(self) -> Any:
        """Dispatch the next job: any foreground job preempts all background; else the highest-
        scoring background job (aging ensures nothing starves). None if the queue is empty."""
        self._tick += 1
        if not self._queue:
            return None
        fg = [e for e in self._queue if e["w"]["foreground"]]
        pool = fg if fg else self._queue
        best = max(pool, key=lambda e: (self._score(e), -e["seq"]))
        self._queue.remove(best)
        self._running = best
        return best["job"]

    def preempt(self, reason: str) -> Any:
        """Foreground demand arrived: a running BACKGROUND job yields and is requeued (keeping its
        original submit tick, so it keeps aging and won't starve). Returns the requeued job, or
        None if there is nothing to preempt (idle, or the running job is itself foreground)."""
        running = self._running
        if running is None or running["w"]["foreground"]:
            return None
        self._queue.append(running)
        self._running = None
        return running["job"]

    def backlog(self) -> List[Any]:
        """Pending jobs in the order they would be dispatched (highest score first)."""
        return [e["job"] for e in sorted(self._queue, key=lambda e: (-self._score(e), e["seq"]))]

    def starving(self) -> List[Any]:
        """Pending jobs that have waited past the starvation threshold — escalated by the aging
        boost so they run rather than starve."""
        return [e["job"] for e in self._queue
                if (self._tick - e["submit_tick"]) >= self.starvation_ticks]
