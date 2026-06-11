"""SM (Substrate Mapper) — scopes self-improvement to the right substrate per domain; rejects
cross-substrate and unmapped-domain proposals pre-gate."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from aurum.build_state import is_built
from aurum.extensions.substrate_mapper import SubstrateMapper


def test_substrate_per_domain():
    sm = SubstrateMapper()
    assert sm.substrate("label") == ["skill", "prompt"]
    assert sm.substrate("infra") == ["tool", "policy"]
    assert sm.substrate("unknown") == []


def test_label_work_scopes_to_skill_prompt():
    sm = SubstrateMapper()
    res = sm.scope({"target": "skill", "edit": "tighten jungle-tag prompt"}, "label")
    assert res["scoped"] is True and res["substrate"] == ["skill", "prompt"]


def test_cross_substrate_proposal_rejected():
    sm = SubstrateMapper()
    res = sm.scope({"target": "tool", "edit": "rewrite submitter"}, "label")  # tool not in label
    assert res["scoped"] is False and "cross-substrate" in res["reason"]


def test_same_proposal_accepted_in_its_own_domain():
    sm = SubstrateMapper()
    assert sm.scope({"target": "tool"}, "infra")["scoped"] is True
    assert sm.scope({"target": "policy"}, "infra")["scoped"] is True


def test_unmapped_domain_rejected_pre_gate():
    sm = SubstrateMapper()
    res = sm.scope({"target": "skill"}, "marketing")
    assert res["scoped"] is False and "unmapped" in res["reason"]


def test_proposal_without_target_is_confined():
    sm = SubstrateMapper()
    res = sm.scope({"edit": "something"}, "label")
    assert res["scoped"] is True and res["substrate"] == ["skill", "prompt"]


def test_custom_mapping():
    sm = SubstrateMapper({"social": ["caption"]})
    assert sm.substrate("social") == ["caption"]
    assert sm.scope({"target": "caption"}, "social")["scoped"] is True
    assert sm.scope({"target": "skill"}, "social")["scoped"] is False


def test_sm_is_built():
    assert is_built("SM")
