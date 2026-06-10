"""Maintenance driver — kernel.run_maintenance (tick+drain, cached scheduler) + the host loop.

The long-lived deployment drives scheduled governance work with run_maintenance_loop (fully
testable without real waiting via injected now/sleep/stop); the cage calls kernel.run_maintenance
opportunistically. Interval gating must hold ACROSS calls (the scheduler is cached), and every
path is fail-safe.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.kernel import GovernanceKernel
from aurum.observability.triggers import run_maintenance_loop

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


# ── kernel.run_maintenance: tick + drain, cached scheduler, interval-gated ─────
def test_run_maintenance_runs_due_tasks_then_gates_by_interval(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    first = k.run_maintenance(now=1_000_000.0)           # all default tasks due on first pass
    assert len(first) == 3 and all(r["ok"] for r in first)
    # cached scheduler → not due again until the interval elapses (concentration=HOUR is shortest)
    assert k.run_maintenance(now=1_000_000.0 + 60) == []  # 60s later → nothing due
    later = k.run_maintenance(now=1_000_000.0 + 100_000)  # well past a day → all due again
    assert len(later) == 3


def test_run_maintenance_is_fail_safe(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    # even if the scheduler/RS misbehaves, run_maintenance must not raise (it returns []).
    k._maintenance_scheduler = object()                  # a broken scheduler (no tick/drain)
    assert k.run_maintenance() == []


# ── the host loop: injectable, bounded, fail-safe ──────────────────────────────
def test_loop_runs_bounded_iterations_without_real_sleep():
    calls, slept = [], []
    run_maintenance_loop(lambda now: calls.append(now), interval_seconds=30,
                         now_fn=lambda: 42.0, sleep_fn=slept.append, max_iterations=3)
    assert calls == [42.0, 42.0, 42.0]
    assert slept == [30, 30]                             # sleeps BETWEEN passes, not after the last


def test_loop_stops_when_should_continue_false():
    # `should_continue` is a flag check (idempotent — the loop calls it more than once per
    # iteration), so stop based on work DONE, not call count.
    ran = []
    iters = run_maintenance_loop(lambda now: ran.append(now), interval_seconds=1,
                                 now_fn=lambda: 0.0, sleep_fn=lambda _s: None,
                                 should_continue=lambda: len(ran) < 2)
    assert iters == 2 and len(ran) == 2


def test_loop_survives_a_failing_pass():
    ran = []

    def boom(now):
        ran.append(now)
        raise RuntimeError("transient")

    iters = run_maintenance_loop(boom, interval_seconds=1, now_fn=lambda: 0.0,
                                 sleep_fn=lambda _s: None, max_iterations=3)
    assert iters == 3 and len(ran) == 3                  # fault swallowed; loop survives
