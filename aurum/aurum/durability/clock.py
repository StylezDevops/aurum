"""Domain Time vs Execution Time — the two clocks must never be conflated.

AURUM_ERR_044 (clock injection) + AURUM_ERR_054 (Domain vs Execution time). Corrects the
naive "inject the clock" rule, which conflated two genuinely different clocks:

  - DOMAIN TIME  — decay (e^(-lam*age)), familiarity, canary decision-windows. This is what
    tests fast-forward. It is INJECTABLE: production passes a clock wrapping time.time; tests
    pass an advanceable fake and simulate a year passing with no real waiting.
  - EXECUTION TIME — asyncio/queue timeouts, the EL backpressure timeout. This runs on the
    REAL monotonic clock and is NEVER faked. Faking it would desync the async machinery (a
    fake year of decay while a real queue waits 2 ms).

Repo-wide convention: time-dependent LOGIC takes `now` as input (Domain Time) and never calls
time.time()/datetime.now() inside the logic; anything that BLOCKS or times out uses
execution_now() (real monotonic). Clock injection applies ONLY to Domain Time.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import math
import time

DAY = 86400.0

# Per-volatility decay constants (lam = ln(2) / half_life). STATIC fades over ~a year,
# SLOW over ~a quarter, FAST over ~two weeks. CONSTITUTIONAL (human-gated) per class —
# the agent cannot retune its own decay to dodge gates.
LAMBDA = {
    "STATIC": math.log(2) / (365 * DAY),
    "SLOW": math.log(2) / (90 * DAY),
    "FAST": math.log(2) / (14 * DAY),
}


class DomainClock:
    """Injectable Domain-Time source. Production wraps time.time; tests advance it freely.

    NEVER used for blocking/timeouts — that is execution time (execution_now). This clock
    only feeds time-dependent LOGIC (decay/familiarity/canary windows) so those become
    deterministic and instantly fast-forwardable in tests.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        """Simulate the passage of Domain Time (no real waiting)."""
        if seconds < 0:
            raise ValueError("domain time advances forward only")
        self._t += float(seconds)

    def set(self, t: float) -> None:
        self._t = float(t)


class RealDomainClock:
    """Production Domain clock: reads the real wall clock. Distinct type from DomainClock so
    a test that swaps in a fake is explicit. Still NOT for timeouts (that is execution time)."""

    def now(self) -> float:
        return time.time()


def execution_now() -> float:
    """EXECUTION TIME — the real monotonic clock, used for timeouts/queue blocking. NEVER
    faked or injected. Distinct from Domain Time so a fast-forwarded decay test cannot alter
    real timeout behaviour (AURUM_ERR_054)."""
    return time.monotonic()


def half_life_lambda(half_life_seconds: float) -> float:
    return math.log(2) / half_life_seconds


def decay(t0: float, *, now: float, lam: float) -> float:
    """Time-decay factor in (0, 1]: e^(-lam * age), age = now - t0 in Domain Time.

    `now` is supplied by the caller (a DomainClock) — the function never reads a clock
    itself, so the same call is deterministic across runs and fast-forwardable in tests.
    Fresh (age 0) -> 1.0; one half-life later -> 0.5; long age -> ~0.
    """
    age = now - t0
    if age <= 0.0:
        return 1.0
    return math.exp(-lam * age)
