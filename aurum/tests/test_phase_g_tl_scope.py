"""Phase G(c) — TL wired into govern() as the action-vs-scope split: an action that declares a
required_tier is scope-gated by TL's EARNED tier ALONGSIDE AG's authority. A high tier can't
rescue low authority (AG still gates); sufficient authority can't act below the earned scope.
TL is fed from the grounded outcome loop."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from aurum.action_map import to_action
from aurum.kernel import GovernanceKernel
from aurum.support.tl import TrustLadder

_IRREV = {"capability_class": "network", "action_class": "commit_outward",
          "risk_tier": "consequential", "irreversible": True}


def test_kernel_owns_tl(tmp_path):
    assert isinstance(GovernanceKernel(home=str(tmp_path)).tl, TrustLadder)


def test_tl_scope_gates_even_when_ag_permits(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    action = to_action("write_file", {"path": "x"})   # file_write/code_edit → AG permits at baseline
    action["required_tier"] = 2
    d = k.govern(action)
    assert d.allow is False and d.rule_id == "tl:tier"   # authority ok, earned scope (tier 0) < 2


def test_tl_scope_opens_after_earned_tier(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    for i in range(3):                                  # grounded outcomes earn the TL tier
        k.record_outcome_verdict(f"t{i}", "file_write", satisfied=True)
    assert k.tl.tier("file_write") == 2
    action = to_action("write_file", {"path": "x"})
    action["required_tier"] = 2
    assert k.govern(action).allow is True               # AG authority AND TL scope both satisfied


def test_ag_authority_still_gates_regardless_of_tier(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.tl.grant("network", {"tier": 3}, approved_by="dan")   # high SCOPE granted
    action = to_action("http_post", {"url": "x"}, classification=_IRREV)
    action["required_tier"] = 1
    d = k.govern(action)
    assert d.allow is False and d.rule_id == "ag:ceiling"   # high tier can't rescue low authority


def test_action_without_required_tier_skips_tl(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    assert k.govern(to_action("write_file", {"path": "x"})).allow is True   # no required_tier → no TL gate


def test_grounded_failure_tiers_down(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    for i in range(3):
        k.record_outcome_verdict(f"t{i}", "file_write", satisfied=True)
    assert k.tl.tier("file_write") == 2
    k.record_outcome_verdict("bad", "file_write", satisfied=False)   # grounded failure
    assert k.tl.tier("file_write") == 1                              # tier-down
