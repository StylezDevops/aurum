"""CG — Cost Governor. Cost-aware routing under budget + graceful degradation."""
from __future__ import annotations

import pytest

from aurum.support.cg import CostGovernor, DEFAULT_TIERS

CHEAP = DEFAULT_TIERS[0]["model"]
MID = DEFAULT_TIERS[1]["model"]
TOP = DEFAULT_TIERS[2]["model"]


def test_routes_by_complexity_with_full_budget():
    cg = CostGovernor(budget=100.0)
    assert cg.route("trivial") == CHEAP
    assert cg.route("moderate") == MID
    assert cg.route("hard") == TOP


def test_dict_step_complexity_and_difficulty():
    cg = CostGovernor(budget=100.0)
    assert cg.route({"complexity": "trivial"}) == CHEAP
    assert cg.route({"difficulty": 0.1}) == CHEAP
    assert cg.route({"difficulty": 0.5}) == MID
    assert cg.route({"difficulty": 0.9}) == TOP


def test_unknown_step_defaults_to_mid():
    assert CostGovernor(budget=100.0).route({"foo": "bar"}) == MID
    assert CostGovernor(budget=100.0).route(object()) == MID


def test_budget_property_and_spend():
    cg = CostGovernor(budget=50.0)
    assert cg.budget == 50.0
    cg.spend(20.0)
    assert cg.budget == 30.0


def test_spend_clamps_at_zero_and_rejects_negative():
    cg = CostGovernor(budget=10.0)
    cg.spend(25.0)  # overspend clamps, never goes negative
    assert cg.budget == 0.0
    with pytest.raises(ValueError):
        cg.spend(-1.0)


def test_budget_pressure_downgrades_routing():
    cg = CostGovernor(budget=100.0)
    assert cg.route("hard") == TOP          # full budget -> top
    cg.spend(75.0)                          # ratio 0.25 -> cap at mid
    assert cg.route("hard") == MID
    cg.spend(20.0)                          # ratio 0.05 -> cap at cheap
    assert cg.route("hard") == CHEAP
    # a trivial step is always cheap regardless of pressure
    assert cg.route("trivial") == CHEAP


def test_pressure_never_upgrades_a_cheap_step():
    cg = CostGovernor(budget=100.0)
    assert cg.route("trivial") == CHEAP  # cap is top but desired is cheap -> cheap
