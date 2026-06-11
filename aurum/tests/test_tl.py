"""TL (Trust Ladder) — earned autonomy: grounded-only tier-up capped at the ceiling, immediate
tier-down, HUMAN_GATE past the ceiling, and can() as a pure tier check (scope, not authority)."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
import pytest

from aurum.build_state import is_built
from aurum.support.trust_ladder import TrustLadder


def test_grounded_success_tiers_up_capped_at_ceiling():
    tl = TrustLadder(default_ceiling=2)
    assert tl.tier("deploy") == 0
    for _ in range(3):
        tl.ingest({"capability": "deploy", "outcome": "success", "grounded": True})
    assert tl.tier("deploy") == 2                       # 0→1→2, then capped at ceiling


def test_proxy_success_does_not_raise_tier():
    tl = TrustLadder()
    for _ in range(5):
        tl.ingest({"capability": "deploy", "outcome": "success", "grounded": False})
    assert tl.tier("deploy") == 0                       # proxy-only never earns autonomy


def test_failure_tiers_down_immediately_any_source():
    tl = TrustLadder(default_ceiling=3)
    for _ in range(3):
        tl.ingest({"capability": "deploy", "outcome": "success", "grounded": True})
    assert tl.tier("deploy") == 3
    tl.ingest({"capability": "deploy", "outcome": "failure", "grounded": False})  # proxy negative still demotes
    assert tl.tier("deploy") == 2
    for _ in range(5):
        tl.ingest({"capability": "deploy", "outcome": "error"})
    assert tl.tier("deploy") == 0                       # floors at 0


def test_can_is_pure_tier_check():
    tl = TrustLadder()
    tl.ingest({"capability": "deploy", "outcome": "success", "grounded": True})  # tier 1
    assert tl.can({"capability": "deploy", "required_tier": 1}) is True
    assert tl.can({"capability": "deploy", "required_tier": 2}) is False
    assert tl.can({"capability": "unknown", "required_tier": 1}) is False


def test_grant_is_human_gated_and_crosses_ceiling():
    tl = TrustLadder(default_ceiling=2, max_tier=3)
    with pytest.raises(PermissionError):
        tl.grant("deploy", {"tier": 3})                 # no approver → blocked
    tl.grant("deploy", {"tier": 3}, approved_by="dan")  # HUMAN_GATE crosses the ceiling
    assert tl.tier("deploy") == 3
    # auto-ingest cannot exceed the ceiling on its own
    tl2 = TrustLadder(default_ceiling=1)
    for _ in range(5):
        tl2.ingest({"capability": "x", "outcome": "success", "grounded": True})
    assert tl2.tier("x") == 1


def test_grant_caps_at_max_tier():
    tl = TrustLadder(default_ceiling=2, max_tier=3)
    tl.grant("deploy", {"tier": 99}, approved_by="dan")
    assert tl.tier("deploy") == 3


def test_tl_is_built():
    assert is_built("TL")
