"""AA — API Archaeologist. Governance control-flow of the gap->tool closed loop.

Tests the deterministic invariants (the I/O steps are injected seams): extension-first
bundling, bounded-discovery clean abort, minimal-surface synthesis that never auto-
activates, capped reactive expansion, and the GR active-goal refusal.
"""
from __future__ import annotations

import os
import tempfile

from aurum.durability.el import EvidenceLedger
from aurum.durability.gr import GoalRegistry
from aurum.durability.tcm import ToolCatalogManager
from aurum.novel.aa import APIArchaeologist


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


# -- detect_gap ------------------------------------------------------------
def test_no_gap_when_capability_available():
    aa = APIArchaeologist()
    assert aa.detect_gap({"required_capability": "scrape",
                          "available_tools": ["scrape"]}) is None


def test_gap_detected_when_missing():
    aa = APIArchaeologist()
    gap = aa.detect_gap({"required_capability": "query_d365", "domain": "dynamics"})
    assert gap["capability"] == "query_d365" and gap["domain"] == "dynamics"


def test_gap_refused_for_inactive_goal():
    # GR accept (a): no tool generation for a gap tied to no active goal.
    gr = GoalRegistry(os.path.join(tempfile.mkdtemp(), "gr.db"))
    gid = gr.add({"goal": "dead", "priority": 5})
    gr.expire(gid)
    aa = APIArchaeologist(gr=gr)
    assert aa.detect_gap({"required_capability": "x", "goal_id": gid}) is None
    # an active goal does NOT block
    gid2 = gr.add({"goal": "live", "priority": 5})
    assert aa.detect_gap({"required_capability": "x", "goal_id": gid2}) is not None


# -- should_bundle (extension-first via TCM) -------------------------------
def test_should_bundle_extends_existing_domain_tool():
    tcm = ToolCatalogManager(os.path.join(tempfile.mkdtemp(), "tcm.db"))
    tcm.register({"tool_id": "dyn1", "domain": "dynamics", "capabilities": ["contacts"]})
    aa = APIArchaeologist(tcm=tcm)
    out = aa.should_bundle({"domain": "dynamics", "capabilities": ["contacts"]})
    assert out == {"extend": "dyn1", "recommend": "extend"}


def test_should_bundle_creates_when_no_domain_tool():
    aa = APIArchaeologist(tcm=ToolCatalogManager(os.path.join(tempfile.mkdtemp(), "t.db")))
    out = aa.should_bundle({"domain": "newdomain", "capabilities": ["x"]})
    assert out == {"extend": None, "recommend": "create"}


# -- discovery budget (accept b) -------------------------------------------
def test_discover_aborts_on_budget_and_logs():
    el = _el()
    # searcher yields many costly candidates that never carry a spec
    def searcher(gap, b):
        for i in range(100):
            yield {"cost_tokens": 9999, "depth": 1}  # spec absent
    aa = APIArchaeologist(el=el, searcher=searcher)
    assert aa.discover({"capability": "x"}, {"max_tokens": 100, "max_depth": 3,
                                             "timeout_sec": 30}) is None
    notes = [e["payload"]["note"] for e in el.query({"source_organ": "AA"})]
    assert "discovery_budget_exceeded" in notes


def test_discover_returns_first_matching_spec():
    def searcher(gap, b):
        yield {"cost_tokens": 1, "depth": 1}            # miss
        yield {"cost_tokens": 1, "depth": 1, "spec": {"name": "d365"}}  # hit
    aa = APIArchaeologist(searcher=searcher)
    assert aa.discover({"capability": "x"})["name"] == "d365"


def test_discover_no_searcher_is_unresolved_not_crash():
    el = _el()
    assert APIArchaeologist(el=el).discover({"capability": "x"}) is None
    notes = [e["payload"]["note"] for e in el.query({"source_organ": "AA"})]
    assert "gap_unresolved_needs_human" in notes


# -- synthesize: minimal surface, never active -----------------------------
def test_synthesize_scopes_surface_and_never_activates():
    aa = APIArchaeologist()
    spec = {"name": "crm", "endpoints": [
        {"capability": "read_contact"}, {"capability": "update_contact"},
        {"capability": "unrelated_invoices"}]}
    server = aa.synthesize(spec, ["read_contact", "update_contact"])
    caps = {e["capability"] for e in server["endpoints"]}
    assert caps == {"read_contact", "update_contact"}  # unrelated dropped (minimal depth)
    assert server["caged"] is True and server["active"] is False  # HUMAN_GATE to promote


# -- cage_test: reactive expansion, capped ---------------------------------
def test_cage_test_expands_then_passes():
    calls = {"n": 0}
    def tester(server):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "missing_dependency": "owner_fk"}
        return {"ok": True}
    aa = APIArchaeologist(tester=tester)
    rep = aa.cage_test({"server_id": "s", "endpoints": []})
    assert rep["ok"] is True and rep["expansion_signatures"] == ["owner_fk"]


def test_cage_test_aborts_on_identical_repeat_and_records_bb():
    recorded = []
    def tester(server):
        return {"ok": False, "missing_dependency": "same_fk"}  # never resolves
    aa = APIArchaeologist(tester=tester, bb_record=recorded.append,
                          max_expansion_attempts=5)
    rep = aa.cage_test({"server_id": "s", "endpoints": []})
    assert rep["ok"] is False and rep["needs_human"] is True
    assert rep["expansion_signatures"] == ["same_fk"]  # identical repeat -> stop
    assert recorded and recorded[0]["constraint"] == "same_fk"  # learned to BB


def test_cage_test_expansion_capped():
    def tester(server):
        # a new distinct constraint every call -> would loop forever without the cap
        tester.k = getattr(tester, "k", 0) + 1
        return {"ok": False, "missing_dependency": f"fk_{tester.k}"}
    aa = APIArchaeologist(tester=tester, max_expansion_attempts=2)
    rep = aa.cage_test({"server_id": "s", "endpoints": []})
    assert rep["ok"] is False
    assert len(rep["expansion_signatures"]) <= 3  # bounded by the attempt cap
