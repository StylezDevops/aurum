"""Phase G(d) — SM + SDG wired into the self-improvement pre-promotion gate: a proposal is scoped
to the domain's substrate (cross-/unmapped rejected pre-gate), then a skill promotion re-runs the
transitive dependents' goldens (blocked on any red). A cleared proposal still faces the HUMAN_GATE."""
from aurum.action_map import to_action
from aurum.extensions.skill_dependency_graph import SkillDependencyGraph
from aurum.extensions.substrate_mapper import SubstrateMapper
from aurum.kernel import GovernanceKernel


def test_kernel_owns_sm_and_sdg(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    assert isinstance(k.sm, SubstrateMapper) and isinstance(k.sdg, SkillDependencyGraph)


def test_cross_substrate_blocked_pre_gate(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    res = k.gate_self_improvement({"target": "tool", "skill": "x"}, "label")  # tool ∉ label substrate
    assert res["allowed"] is False and res["stage"] == "substrate"


def test_unmapped_domain_blocked(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    res = k.gate_self_improvement({"target": "skill"}, "marketing")
    assert res["allowed"] is False and res["stage"] == "substrate"


def test_skill_promotion_blocked_on_transitive_regression(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.sdg.add("A", ["C"])
    k.sdg.add("C", ["B"])                                   # A -> C -> B
    res = k.gate_self_improvement({"target": "skill", "skill": "B"}, "label",
                                  golden_runner=lambda s: s != "C")   # C regresses
    assert res["allowed"] is False and res["stage"] == "regression" and "C" in res["red"]


def test_skill_promotion_allowed_when_all_green(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.sdg.add("A", ["C"])
    k.sdg.add("C", ["B"])
    res = k.gate_self_improvement({"target": "skill", "skill": "B"}, "label",
                                  golden_runner=lambda s: True)
    assert res["allowed"] is True and res["stage"] == "cleared"


def test_cleared_proposal_still_faces_human_gate(tmp_path):
    """A self-improvement that clears SM+SDG is still promoted via a tool_lifecycle action, which
    govern() routes to needs_gate (HUMAN_GATE) — capability growth never bypasses the human."""
    k = GovernanceKernel(home=str(tmp_path))
    decision = k.govern(to_action("skill_manage", {"op": "promote", "skill": "B"}))
    assert decision.allow is False and decision.gate is not None
