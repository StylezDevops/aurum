"""Unit tests for the familiarity core (pure functions + the thread-safe bounded ledger).

The integration / AURUM_ERR_029/032/038 assertions live in test_phase_c_familiarity.py; this
file pins the maths and the concurrency/longevity properties in isolation.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import math
import threading

from aurum.durability.clock import DAY, LAMBDA
from aurum.novel.familiarity import (
    FamiliarityLedger, _FLOOR, _K, _MAX_OUTCOMES, effective_n, familiarity_factor,
)


def test_familiarity_factor_floor_monotone_bounded():
    assert familiarity_factor(0.0) == _FLOOR                       # brand-new domain → floor
    assert familiarity_factor(_K) == _FLOOR + (1 - _FLOOR) * 0.5   # half-saturation at n=k
    # Monotonic non-decreasing, strictly < 1, approaching 1.
    prev = -1.0
    for n in (0, 1, 5, 10, 50, 100, 10_000):
        f = familiarity_factor(n)
        assert prev <= f < 1.0
        prev = f
    assert familiarity_factor(1e9) > 0.99


def test_effective_n_validity_zero_is_zero():
    ts = [0.0, 0.0, 0.0]
    assert effective_n(ts, now=0.0, validity=0.0, volatility="FAST") == 0.0     # invalidated
    assert effective_n(ts, now=0.0, validity=1.0, volatility="FAST") == 3.0     # fresh, valid


def test_effective_n_time_decay_per_volatility():
    # 100 outcomes at t0; one half-life later each contributes 0.5 (per its class half-life).
    ts = [0.0] * 100
    fast = effective_n(ts, now=14 * DAY, validity=1.0, volatility="FAST")     # FAST h-l = 14d
    assert abs(fast - 50.0) < 1e-6
    static = effective_n(ts, now=14 * DAY, validity=1.0, volatility="STATIC")  # 365d h-l
    assert static > 95.0 and static > fast   # STATIC barely decays over 14d, FAST is half gone
    # A FULL year later: FAST collapses toward 0; STATIC (365d half-life) is ~half.
    fast_yr = effective_n(ts, now=365 * DAY, validity=1.0, volatility="FAST")
    static_yr = effective_n(ts, now=365 * DAY, validity=1.0, volatility="STATIC")
    assert fast_yr < 1e-3
    assert abs(static_yr - 50.0) < 1.0


def test_effective_n_unknown_volatility_fails_to_fastest_decay():
    ts = [0.0] * 10
    unknown = effective_n(ts, now=14 * DAY, validity=1.0, volatility="bogus")
    fast = effective_n(ts, now=14 * DAY, validity=1.0, volatility="FAST")
    assert unknown == fast      # unknown → FAST (least experience credit, fail-safe)


def test_effective_n_future_outcome_clamped():
    # An outcome timestamped in the future cannot earn MORE than a fresh one (age clamped ≥ 0).
    assert effective_n([100.0], now=0.0, validity=1.0, volatility="FAST") == 1.0


def test_ledger_record_and_factor():
    led = FamiliarityLedger()
    for _ in range(15):
        led.record("d365", observed_at=0.0)
    # 15 fresh valid outcomes → the reference's verified recovery point (~0.70).
    assert abs(led.factor("d365", now=0.0, validity=1.0, volatility="FAST") - 0.70) < 1e-9
    # An untouched domain is at the floor.
    assert led.factor("azure", now=0.0, validity=1.0, volatility="FAST") == _FLOOR


def test_ledger_bounded_for_longevity():
    led = FamiliarityLedger(max_outcomes=64)
    for i in range(1000):
        led.record("d", observed_at=float(i))
    assert led.count("d") == 64                  # bounded — a multi-year ledger cannot grow it
    # The retained window is the most RECENT (least-decayed) outcomes.
    n = led.effective_n("d", now=1000.0, validity=1.0, volatility="STATIC")
    assert n > 0


def test_ledger_replay_rebuilds_projection():
    led = FamiliarityLedger()
    records = [("d365", float(t)) for t in range(20)]
    led.replay(records)
    assert led.count("d365") == 20
    # Replay is order-independent in result (sorted internally); same effective_n as live record.
    live = FamiliarityLedger()
    for _, t in sorted(records, key=lambda r: r[1]):
        live.record("d365", t)
    assert (led.effective_n("d365", now=100.0, validity=1.0, volatility="SLOW")
            == live.effective_n("d365", now=100.0, validity=1.0, volatility="SLOW"))


def test_ledger_thread_safe_no_lost_updates():
    led = FamiliarityLedger(max_outcomes=10_000)
    threads, per = 8, 50

    def worker():
        for _ in range(per):
            led.record("d", observed_at=0.0)

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert led.count("d") == threads * per       # no lost updates under concurrent record()
