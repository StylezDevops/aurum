"""Reference-implementation smoke tests for the two built organs (EL, CS).

These are real tests (not skip-until-built): EL and CS have concrete bodies, so
their core invariants are exercised directly. They double as worked examples of
how the later organs' tests should look.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.novel.causal_simulator import CausalSimulator


def _ev(oid, cc, organ="TS", at="PROMOTION"):
    return {"event_id": "", "timestamp": "", "source_organ": organ,
            "action_type": at, "object_ids": [oid],
            "payload": {"capability_class": cc},
            "evidence_confidence": 0.9, "evidence_source": "test",
            "prev_hash": "", "hash": ""}


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def test_el_appends_and_chains():
    el = _el()
    el.append(_ev("tool_a", "synth"))
    el.append(_ev("tool_a", "synth"))
    el.append(_ev("tool_b", "infra"))
    assert el.verify_chain() is True


def test_el_lineage_walks_via_cte():
    el = _el()
    el.append(_ev("tool_a", "synth"))
    el.append(_ev("tool_a", "synth"))
    assert len(el.lineage("tool_a")) == 2


def test_el_query_uses_capability_class():
    el = _el()
    el.append(_ev("tool_a", "synth"))
    el.append(_ev("tool_b", "infra"))
    assert len(el.query({"capability_class": "synth"})) == 1


def test_el_append_only_triggers_block_mutation():
    el = _el()
    el.append(_ev("tool_a", "synth"))
    for stmt in ("UPDATE evidence_ledger SET payload='{}' WHERE seq=1",
                 "DELETE FROM evidence_ledger WHERE seq=1"):
        raised = False
        try:
            el._db.execute(stmt)
        except sqlite3.Error as e:
            raised = True
            assert "AURUM_ERR_001" in str(e)
        assert raised, f"trigger failed to block: {stmt}"


def test_cs_whatif_blast_radius():
    cs = CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))
    cs.add_node("skill_a", "skill")
    cs.add_node("skill_b", "skill")
    cs.add_edge("skill_b", "skill_a")
    assert "skill_b" in cs.whatif({"op": "remove", "node_id": "skill_a"})["affected"]


def test_cs_lease_crash_safety():
    cs = CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))
    cs.add_node("tool_x", "tool")
    cs.lease("tool_x", ttl=0.2, holder="job1")
    assert cs.is_leased("tool_x") is True
    cs.heartbeat("tool_x")
    assert cs.is_leased("tool_x") is True
    time.sleep(0.25)  # holder "crashes": stops heart-beating
    assert cs.is_leased("tool_x") is False  # lease lapses, MGC may reclaim
