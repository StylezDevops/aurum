"""RR — Reproducibility Runner. Decision replay over EL, side-effect-free.

Mirrors the spec accept criteria: (a) replay reconstructs the tool/policy/spec versions
and verifier votes live at the time; (b) diff shows which versioned components changed;
(c) replay produces no side effects; (d) a replay whose external API is gone reports
environment_fidelity 'unavailable' yet still reproduces the decision reasoning.
"""
from __future__ import annotations

import os
import tempfile

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.durability.reproducibility_runner import ReproducibilityRunner
from aurum.novel.causal_simulator import CausalSimulator


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _cs():
    return CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))


def _promo(oid="toolX", **payload):
    base = {"capability_class": "synth"}
    base.update(payload)
    return {"event_id": "", "timestamp": "", "source_organ": "TS",
            "action_type": "PROMOTION", "object_ids": [oid], "payload": base,
            "evidence_confidence": 0.9, "evidence_source": "test",
            "prev_hash": "", "hash": ""}


def _last_event_id(el, action="PROMOTION"):
    return el.query({"action_type": action})[0]["event_id"]


# -- accept (a): reconstruct versions + votes ------------------------------
def test_replay_reconstructs_context():
    el = _el()
    el.append(_promo(tool_versions={"scrape": "1.4.2"}, pk_version="pk-7",
                     ls_version="ls-3", votes=[{"verifier": "gpt", "vote": "pass"}],
                     inputs={"url": "x"}))
    rr = ReproducibilityRunner(el)
    eid = _last_event_id(el)
    out = rr.replay(eid)
    ctx = out["run"]["context"]
    assert ctx["tool_versions"] == {"scrape": "1.4.2"}
    assert ctx["pk_version"] == "pk-7" and ctx["ls_version"] == "ls-3"
    assert ctx["votes"] == [{"verifier": "gpt", "vote": "pass"}]
    assert out["run"]["reproduced"] is True
    assert out["environment_fidelity"] == "full"  # no external deps recorded


def test_context_enriched_from_linked_decision():
    el = _el()
    el.append(_promo("artifactZ"))
    seq = el.tip_seq()
    el.log_decision(
        {"action_requested": "promote", "final_decision": "allow",
         "authority_score": 0.66}, {"trust": 0.8, "authority": 0.66,
         "active_rules": ["r1"], "active_goals": ["g1"],
         "knowledge_state_hash": "kh"}, el_seq=seq)
    ctx = ReproducibilityRunner(el).context(_last_event_id(el))
    assert ctx["decision"]["final_decision"] == "allow"
    assert ctx["decision"]["active_rules"] == ["r1"]


# -- accept (b): diff shows changed versioned components -------------------
def test_diff_shows_changed_versions():
    el = _el()
    el.append(_promo("t1", tool_versions={"scrape": "1.0"}, pk_version="pk-1"))
    old = ReproducibilityRunner(el).replay(_last_event_id(el))
    el.append(_promo("t1", tool_versions={"scrape": "2.0"}, pk_version="pk-2"))
    new = ReproducibilityRunner(el).replay(el.query({"action_type": "PROMOTION"})[0]["event_id"])
    changes = ReproducibilityRunner(el).diff(old, new)
    assert changes["tool_versions"] == {"a": {"scrape": "1.0"}, "b": {"scrape": "2.0"}}
    assert changes["pk_version"] == {"a": "pk-1", "b": "pk-2"}


def test_diff_empty_when_identical():
    el = _el()
    el.append(_promo("t1", pk_version="pk-1"))
    run = ReproducibilityRunner(el).replay(_last_event_id(el))
    assert ReproducibilityRunner(el).diff(run, run) == {}


# -- accept (c): replay is side-effect-free --------------------------------
def test_replay_has_no_side_effects():
    el = _el()
    el.append(_promo("t1"))
    before = el._db.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0]
    assert el.verify_chain() is True
    ReproducibilityRunner(el).replay(_last_event_id(el))
    after = el._db.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0]
    assert after == before, "replay mutated the ledger (must be side-effect-free)"
    assert el.verify_chain() is True


# -- accept (d): dead external API -> unavailable, reasoning still reproduced
def test_replay_dead_external_api_is_unavailable_but_reproduces():
    el = _el()
    el.append(_promo("t1", external_apis=["weather.example/v1"], decision="allow"))
    out = ReproducibilityRunner(el).replay(_last_event_id(el))  # no api_checker
    assert out["environment_fidelity"] == "unavailable"
    assert out["run"]["reproduced"] is True
    assert out["run"]["decision"] == "allow"


def test_replay_partial_fidelity_when_some_apis_live():
    el = _el()
    el.append(_promo("t1", external_apis=["a", "b"]))
    rr = ReproducibilityRunner(el, api_checker=lambda a: a == "a")
    assert rr.replay(_last_event_id(el))["environment_fidelity"] == "partial"


def test_explicit_fidelity_label_is_respected():
    el = _el()
    el.append(_promo("t1", environment_fidelity="partial"))
    assert ReproducibilityRunner(el).replay(_last_event_id(el))["environment_fidelity"] == "partial"


# -- verify_pointer (stale-pointer guard) ----------------------------------
def test_verify_pointer_true_when_node_survives():
    cs = _cs()
    cs.add_node("toolX", "tool")
    assert ReproducibilityRunner(_el()).verify_pointer("toolX", cs) is True


def test_verify_pointer_false_when_node_gone_or_no_cs():
    cs = _cs()
    rr = ReproducibilityRunner(_el())
    assert rr.verify_pointer("ghost", cs) is False
    assert rr.verify_pointer("toolX", None) is False  # can't confirm -> fail safe


def test_unknown_event_raises():
    import pytest
    with pytest.raises(KeyError):
        ReproducibilityRunner(_el()).replay("does-not-exist")
