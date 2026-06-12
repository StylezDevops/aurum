"""CC (Concentration Check) — read-only view over EL: usage-distribution shares + systemic-risk
flags for over-concentrated artifacts. Never blocks, never writes."""
import os
import tempfile

import pytest

from aurum.build_state import is_built
from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.observability.concentration_check import ConcentrationCheck


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "cc.db"))


def _append(el, object_ids):
    el.append({"event_id": "", "timestamp": "", "source_organ": "CC-test",
               "action_type": "PROMOTION", "object_ids": object_ids, "payload": {},
               "evidence_confidence": 0.9, "evidence_source": "test",
               "prev_hash": "", "hash": ""})


def test_concentration_is_share_of_events_referencing_each_artifact():
    el = _el()
    for _ in range(4):
        _append(el, ["hot"])
    _append(el, ["cold"])                       # 5 events: hot in 4, cold in 1
    conc = ConcentrationCheck(el).concentration()
    assert conc["hot"] == pytest.approx(0.8) and conc["cold"] == pytest.approx(0.2)


def test_systemic_risks_flags_over_threshold():
    el = _el()
    for _ in range(4):
        _append(el, ["hot", "warm"])
    _append(el, ["cold"])                       # hot=warm=0.8, cold=0.2
    cc = ConcentrationCheck(el, threshold=0.5)
    assert cc.systemic_risks() == ["hot", "warm"]   # both >= 0.5, sorted by share then id
    assert "cold" not in cc.systemic_risks()


def test_min_events_guard_suppresses_tiny_samples():
    el = _el()
    _append(el, ["solo"])                        # 1 event → solo would be 100% but sample too small
    cc = ConcentrationCheck(el, threshold=0.5, min_events=4)
    assert cc.systemic_risks() == []
    assert cc.concentration()["solo"] == pytest.approx(1.0)   # the share is still reported


def test_empty_ledger_is_safe():
    cc = ConcentrationCheck(_el())
    assert cc.concentration() == {} and cc.systemic_risks() == []


def test_requires_el():
    with pytest.raises(RuntimeError):
        ConcentrationCheck().concentration()


def test_read_only_does_not_mutate_ledger():
    el = _el()
    for _ in range(5):
        _append(el, ["a", "b"])
    before = sum(1 for _ in el.iter_events())
    cc = ConcentrationCheck(el)
    cc.concentration()
    cc.systemic_risks()
    after = sum(1 for _ in el.iter_events())
    assert before == after == 5                  # a view, not a writer


def test_cc_is_built():
    assert is_built("CC")
