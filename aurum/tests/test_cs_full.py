"""CS-full — deterministic core completion: whatif policy-conflict detection.

The reference CS shipped whatif/lease but left `conflicts` empty. Per spec accept (a),
whatif(deprecate skill_X) must return the referencing skills/tools PLUS the policy rules
the change brushes. CS.project (deep risk-projection) stays DEFERRED per spec.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.base import Unbuilt
from aurum.novel.cs import CausalSimulator


def _cs():
    return CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))


def test_whatif_separates_policy_conflicts_from_affected():
    cs = _cs()
    cs.add_node("skill_x", "skill")
    cs.add_node("skill_b", "skill")        # references skill_x
    cs.add_node("tool_t", "tool")          # references skill_x
    cs.add_node("rule_r", "policy_rule")   # references skill_x
    for src in ("skill_b", "tool_t", "rule_r"):
        cs.add_edge(src, "skill_x")
    out = cs.whatif({"op": "remove", "node_id": "skill_x"})
    assert set(out["affected"]) == {"skill_b", "tool_t"}   # skills/tools
    assert out["conflicts"] == ["rule_r"]                  # policy rule, not in affected
    assert "rule_r" not in out["affected"]


def test_whatif_transitive_conflicts():
    cs = _cs()
    cs.add_node("skill_x", "skill")
    cs.add_node("skill_mid", "skill")
    cs.add_node("rule_top", "policy_rule")
    cs.add_edge("skill_mid", "skill_x")     # mid -> x
    cs.add_edge("rule_top", "skill_mid")    # rule -> mid -> x (transitive)
    out = cs.whatif({"op": "remove", "node_id": "skill_x"})
    assert "skill_mid" in out["affected"]
    assert "rule_top" in out["conflicts"]


def test_whatif_no_conflicts_when_no_policy_rules():
    cs = _cs()
    cs.add_node("skill_x", "skill")
    cs.add_node("skill_b", "skill")
    cs.add_edge("skill_b", "skill_x")
    out = cs.whatif({"op": "remove", "node_id": "skill_x"})
    assert out["conflicts"] == []
    assert out["affected"] == ["skill_b"]


def test_project_stays_deferred():
    # spec: CS deep projection is DEFERRED/experimental — must NOT be silently built.
    with pytest.raises(Unbuilt):
        _cs().project({"plan": "x"})
