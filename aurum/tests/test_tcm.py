"""TCM — Tool Catalog Manager. Overlap, domain recommendation, selection, retirement."""
from __future__ import annotations

import os
import tempfile

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.durability.tool_catalog_manager import ToolCatalogManager


def _tcm(**kw):
    return ToolCatalogManager(os.path.join(tempfile.mkdtemp(), "tcm.db"), **kw)


# -- accept (a): reuse prevents a duplicate --------------------------------
def test_check_overlap_recommends_reuse_when_covered():
    tcm = _tcm()
    tcm.register({"tool_id": "weather", "domain": "ops",
                  "capabilities": ["forecast", "current"], "resource": "weather.api"})
    out = tcm.check_overlap({"capabilities": ["forecast"], "resource": "weather.api"})
    assert out["recommend"] == "reuse"
    assert "weather" in out["overlaps"]


# -- accept (b): overlap on same resource -> merge -------------------------
def test_check_overlap_recommends_merge_same_resource():
    tcm = _tcm()
    tcm.register({"tool_id": "t1", "domain": "ops",
                  "capabilities": ["read"], "resource": "db.api"})
    # new spec adds a capability but hits the SAME resource and overlaps on 'read'
    out = tcm.check_overlap({"capabilities": ["read", "write"], "resource": "db.api"})
    assert out["recommend"] == "merge"
    assert out["overlaps"] == ["t1"]


def test_check_overlap_build_when_no_overlap_or_diff_resource():
    tcm = _tcm()
    tcm.register({"tool_id": "t1", "capabilities": ["read"], "resource": "db.api"})
    # disjoint capabilities -> build
    assert tcm.check_overlap({"capabilities": ["render"], "resource": "x"})["recommend"] == "build"
    # overlapping capability but DIFFERENT resource -> still build (legit separate tool)
    out = tcm.check_overlap({"capabilities": ["read"], "resource": "other.api"})
    assert out["recommend"] == "build" and out["overlaps"] == ["t1"]


# -- accept (c): selection is a ranked shortlist ---------------------------
def test_select_returns_ranked_shortlist():
    tcm = _tcm()
    tcm.register({"tool_id": "a", "capabilities": ["scrape"]})
    tcm.register({"tool_id": "b", "capabilities": ["scrape"]})
    tcm.register({"tool_id": "c", "capabilities": ["other"]})
    for _ in range(3):
        tcm.record_use("b")
    tcm.record_use("a")
    shortlist = tcm.select("scrape")
    assert shortlist == ["b", "a"]   # ranked by usage, 'c' excluded (no capability)


def test_recommend_domain_picks_best_extend():
    tcm = _tcm()
    tcm.register({"tool_id": "crm1", "domain": "crm", "capabilities": ["contacts"]})
    tcm.register({"tool_id": "crm2", "domain": "crm",
                  "capabilities": ["contacts", "deals"]})
    tcm.register({"tool_id": "ops1", "domain": "ops", "capabilities": ["contacts"]})
    out = tcm.recommend_domain({"domain": "crm", "capabilities": ["contacts", "deals"]})
    assert out["domain"] == "crm" and out["extend"] == "crm2"  # most overlap, in-domain


def test_taxonomy_groups_by_domain():
    tcm = _tcm()
    tcm.register({"tool_id": "a", "domain": "crm", "capabilities": []})
    tcm.register({"tool_id": "b", "domain": "ops", "capabilities": []})
    tax = tcm.taxonomy()
    assert tax["crm"] == ["a"] and tax["ops"] == ["b"]


def test_retirement_candidates_are_unused_or_deprecated():
    tcm = _tcm()
    tcm.register({"tool_id": "used", "capabilities": ["x"]})
    tcm.register({"tool_id": "unused", "capabilities": ["y"]})
    tcm.register({"tool_id": "dep", "capabilities": ["z"], "status": "deprecated"})
    tcm.record_use("used")
    cands = set(tcm.retirement_candidates())
    assert cands == {"unused", "dep"}


def test_register_logged_to_el():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    tcm = _tcm(el=el)
    tcm.register({"tool_id": "t1", "capabilities": ["x"]})
    rows = el.query({"source_organ": "TCM"})
    assert len(rows) == 1 and rows[0]["action_type"] == "PROMOTION"
