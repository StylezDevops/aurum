"""LS — rules as evidenced entities + the second-order governance-gap loop.

A rule's standing is DERIVED from the evidence behind it (not asserted); a rule whose
justifying evidence was contradicted (an MPD-quarantined success) erodes to a retirement
candidate. The second-order loop surfaces a 'must never' breach class that RECURS despite
governance — "why isn't the pre-hoc gate stopping this?" — for owner review (never auto-creates
a rule, since v1 LS is subtraction-only).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os
import tempfile

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.novel.living_specification import LivingSpecification

DAY = 24 * 3600.0


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _ls(**kw):
    return LivingSpecification(os.path.join(tempfile.mkdtemp(), "ls.db"), **kw)


def _demote(el, severity_class, severity="governance"):
    """Append an EL event shaped like the kernel's post-hoc outcome_demote."""
    el.append({"event_id": "", "timestamp": "", "source_organ": "GOV",
               "action_type": "GOVERNANCE_DECISION", "object_ids": ["a"],
               "payload": {"outcome": "outcome_demote", "severity": severity,
                           "severity_class": severity_class, "capability_class": "x"},
               "evidence_confidence": 1.0, "evidence_source": "GOV",
               "prev_hash": "", "hash": ""})


# ── rules as evidenced entities ───────────────────────────────────────────────
def test_rule_evidence_classifies_standing():
    now = 200 * DAY                                # past the 180-day aging window
    ls = _ls(aging_days=180)
    grounded = ls.add_rule({"region": "adaptive", "text": "g", "last_used": now,
                            "supporting_evidence": ["bb:1"], "now": now})
    unevidenced = ls.add_rule({"region": "adaptive", "text": "u", "last_used": now,
                               "supporting_evidence": [], "now": now})
    dormant = ls.add_rule({"region": "adaptive", "text": "d", "last_used": 0.0,
                           "supporting_evidence": ["bb:2"], "now": now})
    protected = ls.add_rule({"region": "adaptive", "text": "p", "last_used": 0.0,
                             "protected": 1, "now": now})
    eroded = ls.add_rule({"region": "adaptive", "text": "e", "last_used": now,
                          "supporting_evidence": ["bb:poison"], "now": now})
    standing = {r["rule_id"]: r["standing"]
                for r in ls.rule_evidence(now=now, contradicted_ids=["bb:poison"])}
    assert standing[grounded] == "grounded"
    assert standing[unevidenced] == "unevidenced"
    assert standing[dormant] == "dormant"
    assert standing[protected] == "protected"
    assert standing[eroded] == "eroded"             # its justifying evidence was contradicted


def test_score_folds_in_evidence_erosion():
    now = 1_000_000.0
    ls = _ls(aging_days=180)
    rid = ls.add_rule({"region": "adaptive", "text": "fresh-but-eroded", "last_used": now,
                       "supporting_evidence": ["bb:poison"], "now": now})
    # used recently → not disused; but its evidence was contradicted → weak (evidenced entity).
    assert ls.score(now=now)["weak_rules"] == []                       # back-compat: no erosion set
    assert ls.score(now=now, contradicted_ids=["bb:poison"])["weak_rules"] == [rid]


def test_score_backcompat_unchanged_without_evidence():
    now = 1_000_000.0
    ls = _ls(aging_days=180)
    ls.add_rule({"region": "adaptive", "text": "fresh", "last_used": now,
                 "supporting_evidence": ["bb:1"], "now": now})
    assert ls.score(now=now)["weak_rules"] == []   # fresh + evidenced + no contradiction → strong


# ── the second-order loop: governance gaps ────────────────────────────────────
def test_governance_gaps_surfaces_recurring_unstopped_breach():
    el = _el()
    ls = _ls(el=el)
    for _ in range(3):
        _demote(el, "secret_capability_misdirection")  # a 'must never' that keeps happening post-hoc
    _demote(el, "destructive_data_loss")           # only once → below the recurrence threshold
    gaps = ls.governance_gaps(min_recurrence=3)
    assert [g["class"] for g in gaps] == ["secret_capability_misdirection"]
    assert gaps[0]["occurrences"] == 3
    assert "pre-hoc gate" in gaps[0]["question"]
    # the gap is audited to EL for owner review
    assert el.query({"source_organ": "LS", "action_type": "GOVERNANCE_GAP"})


def test_governance_gaps_ignores_non_governance_demotes():
    el = _el()
    ls = _ls(el=el)
    for _ in range(5):
        _demote(el, "task_failure", severity="task")   # task failures are not a governance gap
    assert ls.governance_gaps(min_recurrence=3) == []


def test_governance_gaps_no_el_is_empty():
    assert _ls().governance_gaps() == []
