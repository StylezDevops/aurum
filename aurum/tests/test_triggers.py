"""GovernanceScheduler — the configurable trigger layer.

Every maintenance task is optionally on/off and fired by the operator-chosen mechanism
(turn / schedule / operator / event); SCHEDULE feeds RS; a failing task is isolated; the kernel
factory wires the read-only maintenance organs with GOOD secure defaults and policy can override.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.kernel import GovernanceKernel
from aurum.observability.triggers import (
    DAY, EVENT, OPERATOR, SCHEDULE, TURN, GovernanceScheduler, default_governance_scheduler,
)
from aurum.support.resource_scheduler import ResourceScheduler


def _sched(**kw):
    return GovernanceScheduler(**kw)


# ── registration / configuration ──────────────────────────────────────────────
def test_register_requires_interval_for_schedule():
    s = _sched()
    with pytest.raises(ValueError):
        s.register("x", lambda: 1, triggers=[SCHEDULE])          # SCHEDULE w/o interval
    with pytest.raises(ValueError):
        s.register("y", lambda: 1, triggers=["bogus"])           # unknown trigger


def test_configure_overrides_and_cannot_grant_unsupported_trigger():
    s = _sched()
    s.register("t", lambda: 1, triggers=[SCHEDULE, OPERATOR], interval_seconds=DAY, enabled=False)
    s.configure({"t": {"enabled": True, "triggers": [SCHEDULE, OPERATOR, TURN], "interval_seconds": 5}})
    got = s.tasks()["t"]
    assert got["enabled"] is True
    assert TURN not in got["triggers"]                           # policy can't GRANT unsupported TURN
    assert got["interval_seconds"] == 5
    s.configure({"unknown_task": {"enabled": True}})             # unknown name ignored, no raise


# ── the four trigger entrypoints ───────────────────────────────────────────────
def test_on_turn_runs_only_enabled_turn_tasks_and_is_fail_safe():
    s = _sched()
    s.register("a", lambda: "ok", triggers=[TURN])
    s.register("b", lambda: 1 / 0, triggers=[TURN])              # raises → isolated
    s.register("c", lambda: "no", triggers=[OPERATOR])           # not a TURN task
    s.register("d", lambda: "off", triggers=[TURN], enabled=False)
    res = s.on_turn()
    assert res["a"] == {"name": "a", "ok": True, "result": "ok"}
    assert res["b"]["ok"] is False and "ZeroDivisionError" in res["b"]["error"]
    assert "c" not in res and "d" not in res


def test_tick_submits_due_schedule_tasks_to_rs_then_drain_runs_them():
    rs = ResourceScheduler()
    ran = []
    s = GovernanceScheduler(rs=rs)
    s.register("m", lambda: ran.append("m") or "done", triggers=[SCHEDULE], interval_seconds=100)
    assert s.tick(now=1000.0) == ["m"]                           # due (never run) → submitted
    assert ran == []                                             # submitted, not yet executed
    out = s.drain()
    assert ran == ["m"] and out[0]["ok"] is True
    # not due again until the interval elapses
    assert s.tick(now=1050.0) == []                              # 50s < 100s interval
    assert s.tick(now=1100.0) == ["m"]                           # 100s elapsed → due again


def test_no_rs_runs_inline_and_drain_returns_results():
    # FIX (review): with no RS, tick() runs SCHEDULE tasks inline; drain() must return those
    # results (not [] — otherwise inline work runs but is reported as nothing).
    s = _sched()                                          # rs=None
    s.register("m", lambda: "done", triggers=[SCHEDULE], interval_seconds=100)
    assert s.tick(now=1000.0) == ["m"]
    out = s.drain()
    assert len(out) == 1 and out[0]["result"] == "done"


def test_operator_run_refuses_disabled_or_non_operator():
    s = _sched()
    s.register("ok", lambda: "ran", triggers=[OPERATOR])
    s.register("turnonly", lambda: "x", triggers=[TURN])
    s.register("off", lambda: "x", triggers=[OPERATOR], enabled=False)
    assert s.operator_run("ok") == {"name": "ok", "ok": True, "result": "ran"}
    with pytest.raises(PermissionError):
        s.operator_run("turnonly")                               # not operator-triggerable
    with pytest.raises(PermissionError):
        s.operator_run("off")                                    # disabled
    with pytest.raises(KeyError):
        s.operator_run("ghost")


def test_dispatch_event_runs_only_matching_event_tasks():
    s = _sched()
    s.register("breach", lambda: "scanned", triggers=[EVENT],
               event_match=lambda e: e.get("severity") == "governance")
    s.register("any", lambda: "any", triggers=[EVENT])           # no matcher → matches anything
    hit = s.dispatch_event({"severity": "governance"})
    assert hit["breach"]["result"] == "scanned" and "any" in hit
    miss = s.dispatch_event({"severity": "task"})
    assert "breach" not in miss and "any" in miss                # only the unconditional one


# ── kernel factory: GOOD defaults + policy override + RS feed end-to-end ───────
@pytest.mark.skipif(not is_built("PK", "EL", "AG", "CA", "BB"),
                    reason="governance organs not all built")
def test_default_scheduler_wires_kernel_tasks_with_good_defaults(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    sched = default_governance_scheduler(k, now_fn=lambda: 0.0)
    tasks = sched.tasks()
    for name in ("memory_integrity", "concentration", "oi_calibration"):
        assert tasks[name]["enabled"] is True                    # read-only organs ON by default
        assert SCHEDULE in tasks[name]["triggers"] and OPERATOR in tasks[name]["triggers"]


@pytest.mark.skipif(not is_built("PK", "EL", "AG", "CA", "BB"),
                    reason="governance organs not all built")
def test_policy_can_disable_a_task(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    sched = k.maintenance(policy={"concentration": {"enabled": False}})
    assert sched.tasks()["concentration"]["enabled"] is False
    with pytest.raises(PermissionError):
        sched.operator_run("concentration")                      # disabled → refused


@pytest.mark.skipif(not is_built("PK", "EL", "AG", "CA", "BB"),
                    reason="governance organs not all built")
def test_kernel_maintenance_feeds_rs_and_runs(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    sched = default_governance_scheduler(k, now_fn=lambda: 1e9)
    fired = sched.tick(now=1e9)                                  # all three due on first tick
    assert set(fired) == {"memory_integrity", "concentration", "oi_calibration"}
    results = sched.drain()                                      # RS-fed jobs execute
    assert len(results) == 3 and all(r["ok"] for r in results)
    # operator can also run one directly
    assert sched.operator_run("memory_integrity")["ok"] is True
