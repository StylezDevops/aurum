"""IDM + MPD — observability derived views over EL.

Both are read-only views: no own state, never block, never delete. IDM renders
identity drift from EL evidence_snapshots; MPD flags poisoning signatures and surfaces
the day-one governance-event-rate baseline. These run off history EL already records,
so the baselines exist the moment EL is live.
"""
from __future__ import annotations

import os
import tempfile

from aurum.durability.el import EvidenceLedger
from aurum.observability.idm import IdentityDriftMonitor
from aurum.observability.mpd import MemoryPoisoningDetector


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _snap(el, rules, goals, trust=0.8, authority=0.7, khash="kh", env="env"):
    return el.write_evidence_snapshot({
        "trust": trust, "authority": authority,
        "active_rules": rules, "active_goals": goals,
        "knowledge_state_hash": khash, "environment_hash": env})


def _ev(oid, action="VOTE", conf=0.9, source="TS", **payload):
    return {"event_id": "", "timestamp": "", "source_organ": source,
            "action_type": action, "object_ids": [oid],
            "payload": payload or {"capability_class": "synth"},
            "evidence_confidence": conf, "evidence_source": "test",
            "prev_hash": "", "hash": ""}


# ============================ IDM ============================
def test_idm_identical_snapshots_zero_distance():
    el = _el()
    a = _snap(el, ["r1", "r2"], ["g1"])
    b = _snap(el, ["r1", "r2"], ["g1"])
    assert IdentityDriftMonitor(el).distance(a, b) == 0.0


def test_idm_distance_increases_with_divergence():
    el = _el()
    idm = IdentityDriftMonitor(el)
    base = _snap(el, ["r1", "r2", "r3"], ["g1"], trust=0.8, authority=0.7)
    near = _snap(el, ["r1", "r2"], ["g1"], trust=0.8, authority=0.7)
    far = _snap(el, ["x1"], ["g9"], trust=0.2, authority=0.1, khash="kh2")
    d_near = idm.distance(base, near)
    d_far = idm.distance(base, far)
    assert 0.0 < d_near < d_far <= 1.0


def test_idm_trend_is_a_curve_from_baseline():
    el = _el()
    _snap(el, ["r1", "r2"], ["g1"])
    _snap(el, ["r1"], ["g1"])
    _snap(el, ["z9"], ["g2"], khash="kh2")
    curve = IdentityDriftMonitor(el).trend()
    assert len(curve) == 3
    assert curve[0]["distance"] == 0.0          # baseline vs itself
    assert curve[-1]["distance"] > curve[1]["distance"] > 0.0  # monotone drift here


def test_idm_report_flags_cumulative_drift_and_region():
    el = _el()
    # a series of individually-small rule changes that cumulatively move far
    _snap(el, ["r1", "r2", "r3", "r4"], ["g1"], trust=0.8, authority=0.7)
    _snap(el, ["r2", "r3", "r4", "r5"], ["g1"], trust=0.8, authority=0.7)
    _snap(el, ["a", "b", "c", "d"], ["g1"], trust=0.8, authority=0.7, khash="kh9")
    rep = IdentityDriftMonitor(el, drift_threshold=0.2).report()
    assert rep["snapshots"] == 3
    assert rep["current_vs_baseline"] > 0.2
    assert rep["flagged"] is True
    # rules churned the most (goals/trust/authority held constant)
    assert rep["fastest_drifting_region"] == "rules"


def test_idm_report_empty_and_single_are_safe():
    el = _el()
    assert IdentityDriftMonitor(el).report()["snapshots"] == 0
    assert IdentityDriftMonitor(el).trend() == []
    _snap(el, ["r1"], ["g1"])
    rep = IdentityDriftMonitor(el).report()
    assert rep["current_vs_baseline"] == 0.0 and rep["flagged"] is False


# ============================ MPD ============================
def test_mpd_flags_success_then_contradiction():
    el = _el()
    el.append(_ev("toolX", action="PROMOTION", conf=0.91))   # recorded success
    el.append(_ev("unrelated", action="VOTE", conf=0.42))
    el.append(_ev("toolX", action="EXCEPTION", conf=0.43))   # later contradiction
    mpd = MemoryPoisoningDetector(el)
    suspects = mpd.scan()
    promo_id = el.query({"action_type": "PROMOTION"})[0]["event_id"]
    assert promo_id in suspects
    exp = mpd.explain(promo_id)
    assert exp["signature"] == "success_contradicted_by_later_outcome"
    assert len(exp["contradicting_events"]) == 1


def test_mpd_flags_uniform_confidence_cluster():
    el = _el()
    # 5 events from one source all at an identical confidence -> suspiciously uniform
    for i in range(5):
        el.append(_ev(f"obj{i}", action="VOTE", conf=0.77, source="SEN"))
    mpd = MemoryPoisoningDetector(el, min_cluster=4)
    suspects = mpd.scan()
    assert len(suspects) == 5
    exp = mpd.explain(suspects[0])
    assert exp["signature"] == "suspiciously_uniform_confidence_cluster"
    assert len(exp["contradicting_events"]) == 4  # the siblings


def test_mpd_clean_corpus_has_no_suspects():
    el = _el()
    el.append(_ev("a", action="VOTE", conf=0.51))
    el.append(_ev("b", action="VOTE", conf=0.62))
    el.append(_ev("c", action="PROMOTION", conf=0.83))  # success, never contradicted
    mpd = MemoryPoisoningDetector(el, min_cluster=4)
    assert mpd.scan() == []
    assert mpd.explain("nonexistent") == {"signature": None, "contradicting_events": []}


def test_mpd_governance_event_rate_baseline():
    el = _el()
    el.append(_ev("a", action="VOTE", conf=0.5, source="AG"))
    el.append(_ev("b", action="PROMOTION", conf=0.6, source="TS"))
    el.append(_ev("c", action="EXCEPTION", conf=0.7, source="CB"))
    el.write_evidence_snapshot({"trust": 0.8, "authority": 0.7,
                                "active_rules": [], "active_goals": [],
                                "knowledge_state_hash": "k"})
    el.log_decision({"action_requested": "x", "final_decision": "allow"},
                    {"trust": 0.8, "authority": 0.7, "active_rules": [],
                     "active_goals": [], "knowledge_state_hash": "k"})
    rate = MemoryPoisoningDetector(el).governance_event_rate()
    assert rate["ledger_total"] == 3
    assert rate["by_action_type"] == {"VOTE": 1, "PROMOTION": 1, "EXCEPTION": 1}
    assert rate["by_source_organ"] == {"AG": 1, "TS": 1, "CB": 1}
    assert rate["decisions"] == 1
    assert rate["span_seconds"] >= 0.0


def test_mpd_event_rate_window_filters_old_events():
    el = _el()
    # craft explicit old vs recent timestamps
    old = dict(_ev("old", action="VOTE"), timestamp="2020-01-01T00:00:00+00:00")
    recent = dict(_ev("new", action="VOTE"),
                  timestamp="2026-06-05T00:00:00+00:00")
    el.append(old)
    el.append(recent)
    from datetime import datetime, timezone
    now = datetime(2026, 6, 5, 1, 0, 0, tzinfo=timezone.utc)
    windowed = MemoryPoisoningDetector(el).governance_event_rate(
        window_seconds=3600 * 24, now=now)
    assert windowed["ledger_total"] == 1  # only the recent event survives the window


def test_mpd_event_rate_tolerates_invalid_timestamp():
    # Qodo #12.1: _epoch() returns None for a non-empty unparsable timestamp; the
    # window filter must not compare None to a float (TypeError).
    from datetime import datetime, timezone
    el = _el()
    el.append(dict(_ev("bad", action="VOTE"), timestamp="not-a-real-timestamp"))
    el.append(dict(_ev("good", action="VOTE"), timestamp="2026-06-05T00:00:00+00:00"))
    now = datetime(2026, 6, 5, 1, 0, 0, tzinfo=timezone.utc)
    rate = MemoryPoisoningDetector(el).governance_event_rate(
        window_seconds=3600 * 24, now=now)  # must not raise
    assert rate["ledger_total"] == 1  # invalid-timestamp event excluded from the window


def test_mpd_event_rate_window_honors_window_span_and_decisions():
    # Qodo #12.2: a windowed call must report a window-consistent span/rate and window
    # decisions/conflicts too — not all-time counts with an observed-min/max span.
    from datetime import datetime, timezone
    el = _el()
    el.append(dict(_ev("old", action="VOTE"), timestamp="2020-01-01T00:00:00+00:00"))
    el.append(dict(_ev("new", action="VOTE"), timestamp="2026-06-05T00:30:00+00:00"))
    # an old decision that must fall outside the 1h window
    el.log_decision({"action_requested": "x", "final_decision": "allow",
                     "ts": "2020-01-01T00:00:00+00:00"},
                    {"trust": 0.5, "authority": 0.5, "active_rules": [],
                     "active_goals": [], "knowledge_state_hash": "k",
                     "ts": "2020-01-01T00:00:00+00:00"})
    now = datetime(2026, 6, 5, 1, 0, 0, tzinfo=timezone.utc)
    r = MemoryPoisoningDetector(el).governance_event_rate(window_seconds=3600, now=now)
    assert r["windowed"] is True
    assert r["span_seconds"] == 3600.0            # honours the requested window
    assert r["ledger_total"] == 1                 # only the recent event
    assert r["decisions"] == 0                    # old decision windowed out
    assert r["events_per_hour"] == 1.0            # 1 event / 1h
