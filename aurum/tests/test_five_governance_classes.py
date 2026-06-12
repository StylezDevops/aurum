"""The five must-never governance classes (spec-owner declaration, v1): each floors authority from
a STRUCTURAL governance_class tag on the action — never from proxy result text — and the set ships
in the SIGNED constitutional surface (the agent can read, not shorten/extend).
"""
from __future__ import annotations

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.kernel import _GOVERNANCE_FAILURE_CLASSES, GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

_FIVE = ["destructive_data_loss", "wrong_blast_radius_comms", "secret_capability_misdirection",
         "ledger_tamper_or_provenance_forge", "governance_enact_without_signature"]


def test_the_five_are_the_signed_surface(tmp_path):
    assert set(_GOVERNANCE_FAILURE_CLASSES) == set(_FIVE)
    surface = GovernanceKernel(home=str(tmp_path)).constitutional_surface()["governance_failure_classes"]
    assert sorted(surface) == sorted(_FIVE)            # signed → agent can't shorten/extend
    assert "tenant_boundary" not in surface            # v2 gap, deliberately absent (not parchment)


@pytest.mark.parametrize("gclass", _FIVE)
def test_each_class_floors_from_structural_tag(tmp_path, gclass):
    k = GovernanceKernel(home=str(tmp_path / gclass))
    cc = "file_write"
    action = to_action("x", {}, classification={
        "capability_class": cc, "action_class": "commit_outward",
        "risk_tier": "consequential", "governance_class": gclass})
    # a must-never that "succeeded" by the proxy measure is the worst case — it STILL floors.
    k.observe_outcome(action, {"completed": True, "proxy_satisfied": True, "quality": 1.0})
    assert k.ag.authority(cc) == k.ag.kinetics()["floor"]
    assert k.why_authority(cc)["cause"]["severity_class"] == gclass


def test_floor_is_structural_not_proxy_text(tmp_path):
    # the masquerade refusal: the class names in the RESULT TEXT must not floor.
    k = GovernanceKernel(home=str(tmp_path))
    cc = "file_write"
    base = k.ag.authority(cc)
    k.observe_outcome(to_action("write_file", {"path": "/x"}),     # NO governance_class tag
                      {"completed": True, "proxy_satisfied": True, "quality": 1.0,
                       "error": "destructive_data_loss secret_capability_misdirection"})
    assert k.ag.authority(cc) == base                  # scary text, no structural tag → no floor
