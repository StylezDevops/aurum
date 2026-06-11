"""Encapsulation invariant (foundation hardening).

No organ may touch another organ's `_underscore`-private attributes; every cross-organ
access goes through a public method (see organ_dependencies.md). This test codifies the
acceptance grep so the invariant can't silently regress.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from aurum.durability.evidence_ledger import EvidenceLedger

_PKG = pathlib.Path(__file__).resolve().parent.parent / "aurum"
# self._<attr>._<priv> — an organ reaching into another object's private member.
# (self._db.execute is self._<priv>.<public> — a single private — and does NOT match.)
_VIOLATION = re.compile(r"self\._[a-z_]+\._[a-z]")


def test_no_cross_organ_private_access():
    hits = []
    for f in _PKG.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _VIOLATION.search(line):
                hits.append(f"{f.relative_to(_PKG)}:{i}: {line.strip()}")
    assert not hits, "cross-organ private access found:\n" + "\n".join(hits)


# -- EL public read surface (consumers depend on these existing) -----------
def _el(tmp_path):
    return EvidenceLedger(str(tmp_path / "el.db"))


def _ev(oid="o", cc="synth"):
    return {"event_id": "", "timestamp": "", "source_organ": "TS",
            "action_type": "PROMOTION", "object_ids": [oid],
            "payload": {"capability_class": cc}, "evidence_confidence": 0.9,
            "evidence_source": "t", "prev_hash": "", "hash": ""}


def test_el_read_surface_exists_and_works(tmp_path):
    el = _el(tmp_path)
    el.append(_ev("a"))
    seq = el.tip_seq()
    eid = el.iter_events()[0]["event_id"]
    # get_event returns the row incl. seq
    assert el.get_event(eid)["seq"] == seq
    assert el.get_event("nope") is None
    # iter_events ordered + carries seq/payload
    assert [e["seq"] for e in el.iter_events()] == [seq]
    # events_for_object via the object index
    assert el.events_for_object("a")[0]["event_id"] == eid
    # snapshots + decision linkage
    el.log_decision({"action_requested": "x", "final_decision": "allow"},
                    {"trust": 0.8, "authority": 0.7, "active_rules": ["r"],
                     "active_goals": ["g"], "knowledge_state_hash": "k"}, el_seq=seq)
    assert el.snapshots()[0]["active_rules"] == ["r"]
    assert el.decision_for_el_seq(seq)["final_decision"] == "allow"
    assert el.decision_for_el_seq(999999) is None
    # counts (all-time + since)
    assert el.count_decisions() == 1
    assert el.count_decisions(since="2999-01-01T00:00:00+00:00") == 0
    assert el.count_conflicts() == 0


def test_el_archive_region_is_lossless(tmp_path):
    el = _el(tmp_path)
    el.append(_ev("a")); el.append(_ev("a"))
    before = len(el.lineage("a"))
    assert el.archive_region(object_id="a") == 2     # rows copied to archive
    assert len(el.lineage("a")) == before            # live ledger untouched
    assert el.verify_chain() is True
