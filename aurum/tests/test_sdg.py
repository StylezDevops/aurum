"""SDG (Skill Dependency Graph) — patching a skill re-runs the goldens of its transitive
dependents; promotion is blocked on any red (incl. transitive)."""
import pytest

from aurum.build_state import is_built
from aurum.extensions.skill_dependency_graph import SkillDependencyGraph


def _graph():
    # A -> C -> B  (A depends on C, C depends on B)
    return SkillDependencyGraph({"A": ["C"], "C": ["B"]})


def test_deps():
    g = _graph()
    assert g.deps("A") == ["C"] and g.deps("C") == ["B"]
    assert g.deps("B") == [] and g.deps("unknown") == []


def test_affected_is_transitive_dependents():
    g = _graph()
    assert g.affected("B") == ["A", "B", "C"]    # patching B affects C (direct) + A (transitive)
    assert g.affected("C") == ["A", "C"]
    assert g.affected("A") == ["A"]              # nothing depends on A


def test_regress_runs_each_affected_skills_goldens():
    g = _graph()
    res = g.regress(["A", "B", "C"], runner=lambda s: s != "C")   # C's goldens go red
    assert res == {"A": True, "B": True, "C": False}


def test_promotion_blocked_on_any_red():
    g = _graph()
    assert g.promotion_allowed({"A": True, "B": True}) is True
    assert g.promotion_allowed({"A": True, "C": False}) is False


def test_accept_patching_b_blocks_on_transitive_regression():
    """The spec's accept: patching B (which C depends on) re-runs C's goldens; if C regresses,
    B's promotion is blocked."""
    g = _graph()
    affected = g.affected("B")
    assert "C" in affected
    results = g.regress(affected, runner=lambda s: s != "C")
    assert results["C"] is False
    assert g.promotion_allowed(results) is False


def test_regress_needs_runner():
    with pytest.raises(RuntimeError):
        SkillDependencyGraph({"A": ["B"]}).regress(["A"])


def test_sdg_is_built():
    assert is_built("SDG")
