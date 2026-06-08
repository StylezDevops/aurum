"""Familiarity Factor with atrophy — effective authority is base authority × familiarity.

A fluent model reports high confidence on a domain it has NO track record in (great at
D365/Azure, suddenly asked to touch k8s kernel internals); confidence is high, familiarity is
low, and authority should FALL. So authority is gated by FAMILIARITY at the point of use:

    effective_authority = base_authority × familiarity_factor(domain)
    familiarity_factor   = floor + (1 - floor) · (n / (n + k))          # saturating in [floor, 1]
    effective_n(domain)  = validity_now · Σ over grounded-good outcomes of e^(−λ · age)

This is `earned_in` provenance generalized from ENVIRONMENT to DOMAIN. THREE invariants make it
safe rather than a backdoor (runtime-integrity reference PART 2; MAA/familiarity reference PART C):

  • GROUNDED-ONLY (the forbidden-loop guard): familiarity is built ONLY from human-grounded-good
    outcomes. Proxy "it worked" NEVER counts — otherwise the agent inflates its own familiarity.
    Enforced structurally: only the grounded outcome path calls `record`.
  • ATROPHY via KVE validity (AURUM_ERR_029): each outcome is weighted by the CURRENT KVE validity
    of the domain's knowledge. When KVE invalidates a domain (validity → 0), the familiarity those
    successes conferred collapses — high trust on stale knowledge is exactly the state to prevent.
  • TIME-DECAY of experience (AURUM_ERR_038): each outcome is discounted by e^(−λ·age), λ per
    volatility class (clock.LAMBDA). Experience fades without practice; the agent re-certifies by
    doing. Recovery (AURUM_ERR_032): even at the floor a fresh grounded-good outcome (age 0) adds
    full weight, so a domain is never permanently bricked — and proxy still counts for nothing.

Fail-safe directions (never optimistic): unknown validity → treat as DECAYED (0.0, → floor);
unknown volatility → the FASTEST decay (FAST, least experience credit); an unfamiliar domain has
n = 0 → factor = floor (never 0 — a heavily-gated band is still permitted, never a hard block on
competence the agent might have).

`floor`, `k`, and `max_outcomes` are CONSTITUTIONAL (human-gated): the agent cannot tune its own
familiarity curve to dodge the penalty. This module is PURE + a thread-safe in-memory projection;
the durable source of truth is the grounded-outcome stream in the EL (rehydrated on boot).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import math
import threading
from collections import deque
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from ..durability.clock import LAMBDA

# CONSTITUTIONAL (human-gated) — not agent-tunable.
_FLOOR = 0.25       # min familiarity factor (never 0: don't hard-block possible competence)
_K = 10.0           # evidence half-saturation: at effective_n=k, factor is halfway to 1.0
_MAX_OUTCOMES = 512  # per-domain retained outcomes — bounds memory/compute over years of run.
#                     The factor saturates by ~k=10 grounded outcomes, so retaining the most
#                     recent 512 changes effective_n by <<1% vs an unbounded sum (a dropped
#                     outcome is the oldest, hence the most time-decayed). This is the longevity
#                     guard: a multi-year ledger cannot grow the hot-path sum without bound.

# Fastest decay, used when a domain's volatility class is unknown (fail-safe: least credit).
_FALLBACK_VOLATILITY = "FAST"


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def familiarity_factor(effective_n: float, *, floor: float = _FLOOR, k: float = _K) -> float:
    """Saturating map from a decay/validity-weighted experience count → factor in [floor, 1.0].
    Monotonic non-decreasing in effective_n; never exceeds 1.0; floor at n=0."""
    n = max(0.0, float(effective_n))
    if k <= 0.0:                      # defensive: a zero/negative k would divide-by-zero
        return 1.0 if n > 0 else floor
    return floor + (1.0 - floor) * (n / (n + k))


def _lam_for(volatility: Optional[str]) -> float:
    """λ for the experience time-decay, per volatility class. Unknown → FAST (fastest fade)."""
    return LAMBDA.get(volatility or "", LAMBDA[_FALLBACK_VOLATILITY])


def effective_n(observed_ats: Iterable[float], *, now: float, validity: float,
                volatility: Optional[str]) -> float:
    """validity_now · Σ e^(−λ·age). Each grounded-good outcome at domain-time `observed_at`
    is discounted by its age (now − observed_at, never negative) and the whole sum by the
    domain's current KVE validity. validity ≤ 0 (stale/invalidated/unknown) → 0.0."""
    v = _clamp01(validity)
    if v <= 0.0:
        return 0.0
    lam = _lam_for(volatility)
    total = 0.0
    for t in observed_ats:
        age = now - t
        if age < 0.0:
            age = 0.0          # an outcome cannot be in the future; clamp (fail-safe)
        total += math.exp(-lam * age)
    return v * total


class FamiliarityLedger:
    """Thread-safe, bounded, in-memory projection of human-grounded-good outcomes per DOMAIN.

    Keyed by domain (the subject area: 'd365', 'azure', 'k8s'), NOT by capability_class —
    familiarity is "evidence of operating in this domain", and effective_authority multiplies
    it against the per-capability_class base authority. The durable source of truth is the EL
    grounded-outcome stream; `replay()` rebuilds this projection on boot (Phase-B discipline:
    a projection of the append-only ledger, never a separately-stored mutable scalar)."""

    def __init__(self, *, floor: float = _FLOOR, k: float = _K,
                 max_outcomes: int = _MAX_OUTCOMES) -> None:
        self._floor = float(floor)
        self._k = float(k)
        self._max = int(max_outcomes)
        self._lock = threading.Lock()
        # domain -> bounded deque of observed_at (DOMAIN time). maxlen auto-evicts the oldest
        # (most time-decayed, least significant) outcome — the longevity bound.
        self._obs: Dict[str, Deque[float]] = {}

    def record(self, domain: str, observed_at: float) -> None:
        """Add one grounded-good outcome. Call ONLY from the human-grounded path (never proxy)."""
        with self._lock:
            dq = self._obs.get(domain)
            if dq is None:
                dq = deque(maxlen=self._max)
                self._obs[domain] = dq
            dq.append(float(observed_at))

    def replay(self, records: Iterable[Tuple[str, float]]) -> None:
        """Rebuild the projection from (domain, observed_at) pairs in chronological order
        (so the bounded deque keeps the most-recent window). Used by kernel rehydration."""
        for domain, observed_at in sorted(records, key=lambda r: r[1]):
            self.record(domain, observed_at)

    def effective_n(self, domain: str, *, now: float, validity: float,
                    volatility: Optional[str]) -> float:
        with self._lock:
            snapshot = tuple(self._obs.get(domain, ()))   # snapshot under lock, compute outside
        return effective_n(snapshot, now=now, validity=validity, volatility=volatility)

    def factor(self, domain: str, *, now: float, validity: float,
               volatility: Optional[str]) -> float:
        n = self.effective_n(domain, now=now, validity=validity, volatility=volatility)
        return familiarity_factor(n, floor=self._floor, k=self._k)

    def domains(self) -> List[str]:
        with self._lock:
            return list(self._obs.keys())

    def count(self, domain: str) -> int:
        """Raw (un-decayed, un-weighted) retained outcome count — for introspection/tests."""
        with self._lock:
            return len(self._obs.get(domain, ()))
