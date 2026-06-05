"""MGC — Memory Garbage Collector. Lease/reference-aware sweep, archive!=delete, compress."""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.durability.el import EvidenceLedger
from aurum.durability.mgc import MemoryGarbageCollector
from aurum.durability.tcm import ToolCatalogManager
from aurum.novel.cs import CausalSimulator


def _mgc(**kw):
    return MemoryGarbageCollector(os.path.join(tempfile.mkdtemp(), "mgc.db"), **kw)


def _cs():
    return CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))


# -- accept (a): archive != delete; restore intact -------------------------
def test_archive_then_restore_intact():
    mgc = _mgc()
    mgc.archive("tool_x", content={"spec": "v1"}, kind="tool")
    assert mgc.restore("tool_x") == {"spec": "v1"}


def test_restore_missing_raises():
    with pytest.raises(KeyError):
        _mgc().restore("nope")


# -- accept (b): duplicate lessons merge, lineage preserved ----------------
def test_scan_groups_duplicate_lessons_and_merge_preserves_lineage():
    mgc = _mgc()
    out = mgc.scan({"lessons": [{"id": "l1", "signature": "s"},
                                {"id": "l2", "signature": "s"},
                                {"id": "l3", "signature": "other"}]})
    assert out["mergeable"] == [["l1", "l2"]]
    merged = mgc.merge(["l1", "l2"])
    assert mgc.lineage(merged) == ["l1", "l2"]


# -- accept (c): never archive a still-referenced artifact -----------------
def test_referenced_skill_not_archivable():
    cs = _cs()
    cs.add_node("skill_a", "skill")
    cs.add_node("skill_b", "skill")
    cs.add_edge("skill_b", "skill_a")  # skill_b references skill_a
    mgc = _mgc(cs=cs)
    out = mgc.scan({"skills": ["skill_a"]})
    assert "skill_a" not in out["archivable"]  # still referenced


def test_orphaned_skill_is_archivable():
    cs = _cs()
    cs.add_node("skill_a", "skill")  # no referrers
    mgc = _mgc(cs=cs)
    assert "skill_a" in mgc.scan({"skills": ["skill_a"]})["archivable"]


# -- accept (d) / AURUM_ERR_003: leased artifact skipped -------------------
def test_leased_artifact_skipped_even_if_orphaned():
    cs = _cs()
    cs.add_node("tool_alpha", "tool")
    cs.lease("tool_alpha", ttl=300)
    mgc = _mgc(cs=cs)
    out = mgc.scan({"skills": ["tool_alpha"], "tools": ["tool_alpha"]})
    assert "tool_alpha" not in out["archivable"]
    assert "tool_alpha" not in out["retirable"]


# -- retirable from TCM ----------------------------------------------------
def test_retirable_from_tcm():
    tcm = ToolCatalogManager(os.path.join(tempfile.mkdtemp(), "tcm.db"))
    tcm.register({"tool_id": "unused", "capabilities": ["x"]})  # usage 0 -> candidate
    mgc = _mgc(tcm=tcm)
    assert "unused" in mgc.scan()["retirable"]


# -- AURUM_ERR_002: lossless structural compression ------------------------
def test_compress_is_lossless_structural():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    ev = {"event_id": "", "timestamp": "", "source_organ": "TS",
          "action_type": "PROMOTION", "object_ids": ["o"], "payload": {"k": 1},
          "evidence_confidence": 0.9, "evidence_source": "t",
          "prev_hash": "", "hash": ""}
    el.append(ev); el.append(dict(ev))
    mgc = _mgc(el=el)
    before = len(el.lineage("o"))
    n = mgc.compress({"object_id": "o"})
    assert n == 2 and len(el.lineage("o")) == before  # live ledger untouched
    assert el.verify_chain() is True
    # rows landed in the archive table
    archived = el._db.execute("SELECT COUNT(*) FROM evidence_ledger_archive").fetchone()[0]
    assert archived == 2
