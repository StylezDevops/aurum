"""The Governance Pressure Differential — "the governed path is the cheapest path", as a number.

Core insight (CS-EQ reference §0): a frozen model has no reward gradient, so "prefers the
governed path" can only mean something structural. Define cost in the terms that bind a frozen
model's TRAJECTORY (tokens + latency + steps) and value as P(the route actually achieves the
goal). The quantity that governs trajectory selection is expected cost PER UNIT of goal
progress = cost / p_success.

Two routes to the same effect:
  • GOVERNED: pays overhead C_g but COMPLETES (p_g high) → finite cost-per-success.
  • UNGOVERNED that FAILS CLOSED: cheap per attempt (C_u) but yields nothing (p_u → 0) →
    cost-per-success → ∞.

So fail-closed makes the ungoverned path's cost-per-success UNBOUNDED — the governed path is
cheapest *to actual goal progress* not because governance is free, but because the ungoverned
alternative is a money-pit that never pays out. The differential:

    D = ECPS(ungoverned_best) − ECPS(governed)
    D > 0  → governed strictly cheaper-per-success → equilibrium holds.
    D = ∞  → best ungoverned route cannot succeed (fail-closed) → strongest.
    D ≤ 0  → an ungoverned route is economically competitive → CONSTITUTIONAL ALARM (the
             mathematical signature of an exploitable bypass — the dispatch bypass closed in
             PR#39 had p_u>0 AND C_u<C_g, i.e. D<0 — detectable BEFORE behavioural capture).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PathCost:
    """The cost of one route to a given effect, in terms that bind a frozen model's trajectory.
    p_success is P(route achieves the GOAL EFFECT) — NOT that the tool returns 200. A fail-closed
    route returns quickly but achieves nothing, so its p_success is ~0, not 1."""
    tokens: float
    latency_s: float
    steps: int
    p_success: float        # in [0, 1]

    def __post_init__(self) -> None:
        if not (0.0 <= self.p_success <= 1.0):
            raise ValueError("p_success must be in [0,1]")
        if self.tokens < 0 or self.latency_s < 0 or self.steps < 0:
            raise ValueError("costs must be non-negative")


@dataclass(frozen=True)
class CostWeights:
    """How to fold resource dimensions into one scalar. TUNING — the SIGN of the differential
    (what triggers alarms) is robust to reasonable weights; do not agonise over them."""
    token: float = 1.0
    latency: float = 100.0      # 1s ~ 100 tokens of 'cost', tune from telemetry later
    step: float = 50.0


def expected_cost_per_success(c: PathCost, w: CostWeights = CostWeights()) -> float:
    """cost / p_success. Returns +inf when the route cannot succeed — the fail-closed case.
    This is the quantity a goal-seeking trajectory effectively minimises."""
    raw = w.token * c.tokens + w.latency * c.latency_s + w.step * c.steps
    if c.p_success <= 0.0:
        return math.inf
    return raw / c.p_success


def governance_pressure_differential(governed: PathCost, ungoverned_best: PathCost,
                                     w: CostWeights = CostWeights()) -> float:
    """D = ECPS(ungoverned_best) − ECPS(governed). See module docstring for the regimes."""
    g = expected_cost_per_success(governed, w)
    u = expected_cost_per_success(ungoverned_best, w)
    if math.isinf(u) and math.isinf(g):
        return 0.0          # degenerate: neither can succeed; not an equilibrium claim
    return u - g


def equilibrium_holds(d: float, margin: float = 0.0) -> bool:
    """Holds iff the governed path beats the best ungoverned route by at least `margin`.
    margin > 0 demands a comfortable lead, not a photo finish. Constitutional (human-gated)."""
    return d > margin
