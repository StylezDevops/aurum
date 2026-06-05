"""OI — Outcome Interpreter. Tiered satisfaction oracle + effectiveness ratio."""
from __future__ import annotations

import os
import tempfile

from aurum.durability.el import EvidenceLedger
from aurum.durability.pm import PreferenceModel
from aurum.novel.oi import OutcomeInterpreter


def _oi(**kw):
    return OutcomeInterpreter(os.path.join(tempfile.mkdtemp(), "oi.db"), **kw)


# -- accept (a): completed-but-poor -> satisfied=false, learning case ------
def test_completed_but_redone_is_unsatisfied_and_recorded_to_bb():
    learned = []
    oi = _oi(bb_record=learned.append)
    v = oi.interpret({"task_id": "t1", "completed": True, "redone": True}, "g1")
    assert v["completed"] is True and v["satisfied"] is False
    assert v["satisfaction_source"] == "proxy"
    assert learned and learned[0]["lesson"] == "completed_unsatisfied"


def test_preference_violation_makes_unsatisfied():
    pm = PreferenceModel(os.path.join(tempfile.mkdtemp(), "pm.db"))
    pm.add({"statement": "no em dash", "forbids": "—", "key": "dash"}, "stated")
    oi = _oi(pm=pm)
    v = oi.interpret({"task_id": "t1", "completed": True, "output": "has — dash"}, "g1")
    assert v["satisfied"] is False
    assert v["signals"]["preference_violations"]


def test_clean_completion_is_satisfied():
    oi = _oi()
    v = oi.interpret({"task_id": "t1", "completed": True}, "g1")
    assert v["satisfied"] is True and v["quality"] > 0.5


# -- tiered oracle: proxy vs human + calibration (accept e) ----------------
def test_sample_and_human_verdict_and_calibration():
    oi = _oi()
    oi.interpret({"task_id": "t1", "completed": True, "stakes": "high_stakes",
                  "proxy_confidence": 0.2}, "g1")
    oi.interpret({"task_id": "t2", "completed": True}, "g1")
    sampled = oi.sample_for_human(1.0)
    assert "t1" in sampled and "t2" in sampled
    assert sampled[0] == "t1"  # high-stakes / low-confidence sampled first
    # human contradicts the proxy on t1 -> proxy down-weighted
    oi.record_human_verdict("t1", {"satisfied": False})
    cal = oi.proxy_calibration()
    assert cal["samples"] == 1 and cal["proxy_vs_human_agreement"] == 0.0
    assert cal["proxy_weight"] == 0.0


def test_sample_rate_zero_returns_nothing():
    oi = _oi()
    oi.interpret({"task_id": "t1", "completed": True}, "g1")
    assert oi.sample_for_human(0.0) == []


# -- effectiveness ratio (accept d) ----------------------------------------
def test_effectiveness_drops_when_overhead_climbs():
    lean = _oi()
    lean.interpret({"task_id": "a", "completed": True, "overhead": 1.0,
                    "interruptions": 0.0, "latency": 1.0}, "g")
    heavy = _oi()
    heavy.interpret({"task_id": "a", "completed": True, "overhead": 1.0,
                     "interruptions": 1.0, "latency": 1.0}, "g")
    heavy.interpret({"task_id": "b", "completed": True, "redone": True,
                     "overhead": 1.0, "interruptions": 1.0, "latency": 1.0}, "g")
    # heavy panel: lower satisfaction (a redo) + more interruptions -> lower ratio
    assert heavy.effectiveness()["ratio"] < lean.effectiveness()["ratio"]


def test_effectiveness_empty_is_zero():
    assert _oi().effectiveness()["ratio"] == 0.0


# -- trend + EL audit ------------------------------------------------------
def test_trend_and_el_audit():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    oi = _oi(el=el)
    oi.interpret({"task_id": "t1", "completed": True, "capability_class": "research"},
                 "g1")
    assert len(oi.trend("research")) == 1
    rows = el.query({"source_organ": "OI"})
    assert rows and rows[0]["payload"]["satisfaction_source"] == "proxy"
