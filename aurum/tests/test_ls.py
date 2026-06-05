"""LS — Living Specification. Subtraction-only v1: retire/reweight, gated, versioned."""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.durability.pm import PreferenceModel
from aurum.novel.cs import CausalSimulator
from aurum.novel.ls import LivingSpecification

DAY = 24 * 3600.0


def _ls(**kw):
    return LivingSpecification(os.path.join(tempfile.mkdtemp(), "ls.db"), **kw)


# -- accept (b) / AURUM_ERR_004: core unproposable -------------------------
def test_core_proposal_auto_rejected():
    ls = _ls()
    cid = ls.add_rule({"region": "core", "text": "safety floor"})
    rev = ls.propose_revision(rule_id=cid)
    assert rev["rejected"] and rev["reason"] == "core_unproposable"


# -- accept (a): emits a retire proposal with evidence ---------------------
def test_proposes_retirement_of_disused_rule():
    ls = _ls(aging_days=180)
    old = ls.add_rule({"region": "adaptive", "text": "stale", "last_used": 0.0,
                       "supporting_evidence": ["bb:1"]})
    rev = ls.propose_revision()  # auto-pick weakest
    assert rev["diff"] == {"op": "retire", "rule_id": old, "new_weight": None}
    assert rev["evidence_ids"] == ["bb:1"] and rev["complexity_delta"] < 0


def test_protected_rule_not_weak_on_disuse():
    ls = _ls(aging_days=180)
    ls.add_rule({"region": "adaptive", "text": "rare-critical", "last_used": 0.0,
                 "protected": 1})
    assert ls.score()["weak_rules"] == []


# -- accept (f): preference redirected to PM -------------------------------
def test_preference_redirected_to_pm():
    pm = PreferenceModel(os.path.join(tempfile.mkdtemp(), "pm.db"))
    ls = _ls(pm=pm)
    rid = ls.add_rule({"region": "adaptive", "text": "use powershell"})
    rev = ls.propose_revision(rule_id=rid, as_preference=True)
    assert rev["rejected"] and rev["route"] == "PM"
    assert len(pm.get()) == 1  # landed in PM, not the constitution


# -- accept (d): apply is gated; rollback byte-identical -------------------
def test_apply_is_gated():
    ls = _ls()
    rid = ls.add_rule({"region": "adaptive", "text": "x", "last_used": 0.0,
                       "supporting_evidence": ["e"]})
    rev = ls.propose_revision(rule_id=rid)
    with pytest.raises(PermissionError):
        ls.apply_revision(rev)  # no approver -> HUMAN_GATE blocks


def test_rollback_restores_byte_identical():
    ls = _ls()
    rid = ls.add_rule({"region": "adaptive", "text": "x", "last_used": 0.0,
                       "supporting_evidence": ["e"]})
    before = ls.current()
    out = ls.apply_revision(ls.propose_revision(rule_id=rid), approved_by="owner")
    assert ls.current() == []                   # retired
    ls.rollback(out["version"])
    assert ls.current() == before               # byte-identical restore


def test_rollback_old_statute_runs_cs_whatif():
    cs = CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))
    calls = {"n": 0}
    orig = cs.whatif
    cs.whatif = lambda change: (calls.__setitem__("n", calls["n"] + 1), orig(change))[1]
    ls = _ls()
    rid = ls.add_rule({"region": "adaptive", "text": "x", "last_used": 0.0,
                       "supporting_evidence": ["e"]})
    out = ls.apply_revision(ls.propose_revision(rule_id=rid), approved_by="owner")
    # rollback far in the future -> statute is "old" -> CS.whatif consulted first
    ls.rollback(out["version"], cs=cs, now=9_999_999_999.0)
    assert calls["n"] == 1


# -- accept (e): entropy limit ---------------------------------------------
def test_entropy_limit_blocks_unjustified_growth():
    ls = _ls()
    rid = ls.add_rule({"region": "adaptive", "text": "x"})
    bad = {"region": "adaptive", "complexity_delta": 50,
           "diff": {"op": "reweight", "rule_id": rid, "new_weight": 2.0}}
    with pytest.raises(ValueError, match="entropy"):
        ls.apply_revision(bad, approved_by="owner")


# -- action reversibility (Phase-5 eligibility) ----------------------------
def test_action_reversibility_classifier():
    ls = _ls()
    assert ls.action_reversibility({"enabled_actions": ["read", "draft"]}) is True
    assert ls.action_reversibility({"enabled_actions": ["read", "auto_send"]}) is False


# -- experimental auto-expiry ----------------------------------------------
def test_experimental_auto_expires():
    ls = _ls()
    ls.add_rule({"region": "experimental", "text": "trial", "expires_at": 100.0})
    assert ls.expire_experimental(now=50.0) == []      # not yet
    expired = ls.expire_experimental(now=200.0)
    assert len(expired) == 1 and ls.current("experimental") == []


# -- validate (LS-V grounded evidence) -------------------------------------
def test_validate_grounds_on_strong_evidence_only():
    ls = _ls()
    strong = {"evidence_strength": "strong", "evidence_ids": ["a", "b"], "confounders": []}
    weak = {"evidence_strength": "weak", "evidence_ids": [], "confounders": ["x"]}
    assert ls.validate(strong)["grounded"] is True
    assert ls.validate(weak)["grounded"] is False


def test_complexity_reports_budget():
    ls = _ls(max_adaptive_tokens=100)
    ls.add_rule({"region": "adaptive", "text": "a", "tokens": 30})
    c = ls.complexity()
    assert c["adaptive_tokens"] == 30 and c["budget"] == 100
