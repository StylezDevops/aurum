"""Governance-evidence tables on the EL store — append-only parity with the ledger.

Covers the task's acceptance points:
  (a) UPDATE/DELETE on decisions / evidence_snapshots / conflicts raises AURUM_ERR_001
  (b) verify_chain() still passes once the new tables exist and are written
  (c) a decision write fails CLOSED if its evidence snapshot can't be written
  (d) is covered by running the existing reference suite alongside this one
Plus the decision->snapshot FK link, the el_seq ledger link, and happy paths.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from aurum.durability.evidence_ledger import EvidenceLedger


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _ev(oid="tool_a", cc="synth"):
    return {"event_id": "", "timestamp": "", "source_organ": "TS",
            "action_type": "PROMOTION", "object_ids": [oid],
            "payload": {"capability_class": cc},
            "evidence_confidence": 0.9, "evidence_source": "test",
            "prev_hash": "", "hash": ""}


def _snapshot(**over):
    snap = {"trust": 0.8, "authority": 0.7,
            "active_rules": ["r1", "r2"], "active_goals": ["g1"],
            "knowledge_state_hash": "kh-123", "environment_hash": "env-1"}
    snap.update(over)
    return snap


def _decision(**over):
    d = {"action_requested": "edit_doc", "final_decision": "allow",
         "authority_score": 0.72, "reason": {"rule_id": "AG-band", "why": "cleared"}}
    d.update(over)
    return d


def _conflict(sid, **over):
    c = {"action": "edit_doc", "winner": "AG", "loser": "OI",
         "winner_position": "contract", "loser_position": "proceed",
         "risk_snapshot_id": sid, "risk_signals_live": 1}
    c.update(over)
    return c


def test_governance_happy_path_writes():
    el = _el()
    sid = el.write_evidence_snapshot(_snapshot())
    assert sid
    did = el.log_decision(_decision(), _snapshot())
    assert did
    cid = el.log_conflict(_conflict(sid))
    assert cid
    assert el._db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert el._db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 1
    # decision references a real snapshot
    snap_id = el._db.execute(
        "SELECT evidence_snapshot_id FROM decisions WHERE decision_id=?", (did,)
    ).fetchone()[0]
    assert el._db.execute(
        "SELECT COUNT(*) FROM evidence_snapshots WHERE snapshot_id=?", (snap_id,)
    ).fetchone()[0] == 1


def test_a_append_only_triggers_on_all_three_tables():
    el = _el()
    sid = el.write_evidence_snapshot(_snapshot())
    el.log_decision(_decision(), _snapshot())
    el.log_conflict(_conflict(sid, risk_signals_live=0))
    mutations = [
        "UPDATE evidence_snapshots SET trust=0.1",
        "DELETE FROM evidence_snapshots",
        "UPDATE decisions SET final_decision='deny'",
        "DELETE FROM decisions",
        "UPDATE conflicts SET winner='OI'",
        "DELETE FROM conflicts",
    ]
    for stmt in mutations:
        raised = False
        try:
            el._db.execute(stmt)
        except sqlite3.Error as e:
            raised = True
            assert "AURUM_ERR_001" in str(e), f"wrong error for {stmt!r}: {e}"
        assert raised, f"append-only trigger failed to block: {stmt!r}"


def test_b_verify_chain_still_passes_with_governance_tables():
    el = _el()
    el.append(_ev("tool_a"))
    el.append(_ev("tool_a"))
    el.write_evidence_snapshot(_snapshot())
    el.log_decision(_decision(), _snapshot())
    el.log_conflict(_conflict(None))
    el.append(_ev("tool_b", "infra"))
    assert el.verify_chain() is True


def test_c_decision_fails_closed_when_snapshot_cannot_be_written():
    el = _el()
    # Snapshot missing the NOT-NULL knowledge_state_hash -> snapshot insert fails ->
    # the whole decision transaction must roll back (fail-closed).
    bad_snap = _snapshot()
    bad_snap.pop("knowledge_state_hash")
    raised = False
    try:
        el.log_decision(_decision(), bad_snap)
    except RuntimeError:
        raised = True
    assert raised, "log_decision did not fail when its snapshot could not be written"
    # Neither the decision NOR a partial snapshot may persist.
    assert el._db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert el._db.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 0


def test_decision_requires_existing_snapshot_via_fk():
    el = _el()
    # A decision referencing a non-existent snapshot is rejected by the FK — a
    # decision-without-snapshot is unreplayable and must not exist, even out-of-band.
    raised = False
    try:
        el._db.execute(
            "INSERT INTO decisions(decision_id,ts,action_requested,final_decision,"
            "reason_json,evidence_snapshot_id) VALUES (?,?,?,?,?,?)",
            ("d1", "t", "a", "allow", "{}", "no-such-snapshot"))
    except sqlite3.Error:
        raised = True
    assert raised, "FK did not enforce the decision->snapshot link"


def test_el_seq_links_decision_to_ledger_event():
    el = _el()
    el.append(_ev("tool_a"))
    seq = el.tip_seq()
    assert seq is not None
    did = el.log_decision(_decision(), _snapshot(), el_seq=seq)
    row = el._db.execute(
        "SELECT el_seq FROM decisions WHERE decision_id=?", (did,)).fetchone()
    assert row[0] == seq
