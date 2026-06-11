"""GR — Goal Registry. Goal health decay, expiry, CS feed, EL audit."""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.durability.goal_registry import GoalRegistry
from aurum.novel.causal_simulator import CausalSimulator

DAY = 24 * 3600.0
WINDOW = 30 * DAY


def _gr(**kw):
    return GoalRegistry(os.path.join(tempfile.mkdtemp(), "gr.db"),
                        decay_window=WINDOW, **kw)


def test_add_get_roundtrip():
    gr = _gr()
    gid = gr.add({"goal": "ship v1", "owner": "dan", "priority": 5,
                  "dependencies": ["g0"]})
    g = gr.get(gid)
    assert g["goal"] == "ship v1" and g["status"] == "active"
    assert g["importance"] == 1.0           # priority 5 -> 1.0
    assert gr.depends(gid) == ["g0"]


def test_fresh_goal_is_active():
    gr = _gr()
    t0 = 1_000_000.0
    gid = gr.add({"goal": "x", "priority": 4, "now": t0})
    assert any(g["goal_id"] == gid for g in gr.active(now=t0))


def test_untouched_goal_decays_out_of_active():
    # accept (d): no touch / no progress for the decay window -> below threshold
    gr = _gr()
    t0 = 1_000_000.0
    gid = gr.add({"goal": "stale", "priority": 4, "now": t0})
    later = t0 + WINDOW + 1  # fully decayed
    assert gr.health(gid, now=later)["score"] < gr.health_threshold
    assert gr.active(now=later) == []
    # ...but an owner touch revives it
    gr.touch(gid, now=later)
    assert any(g["goal_id"] == gid for g in gr.active(now=later))


def test_expire_removes_from_active():
    gr = _gr()
    t0 = 1_000_000.0
    gid = gr.add({"goal": "done", "priority": 5, "now": t0})
    assert gr.active(now=t0)
    gr.expire(gid)
    assert gr.active(now=t0) == []
    assert gr.get(gid)["status"] == "expired"


def test_explicit_expiry_timestamp_drops_after_deadline():
    gr = _gr()
    t0 = 1_000_000.0
    gid = gr.add({"goal": "timeboxed", "priority": 5, "now": t0,
                  "expiry": t0 + DAY})
    assert any(g["goal_id"] == gid for g in gr.active(now=t0))
    assert gr.active(now=t0 + 2 * DAY) == []


def test_achieved_goal_loses_health():
    gr = _gr()
    t0 = 1_000_000.0
    done = gr.add({"goal": "achieved", "priority": 5, "progress": 1.0, "now": t0})
    open_ = gr.add({"goal": "in progress", "priority": 5, "progress": 0.0, "now": t0})
    assert gr.health(done, now=t0)["score"] < gr.health(open_, now=t0)["score"]


def test_add_registers_cs_node():
    cs = CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))
    gr = _gr(cs=cs)
    gid = gr.add({"goal": "served", "priority": 3})
    node_ids = {n["node_id"] for n in cs.graph()["nodes"]}
    assert gid in node_ids


def test_add_and_expire_logged_to_el():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    gr = _gr(el=el)
    gid = gr.add({"goal": "audited", "priority": 3})
    gr.expire(gid)
    actions = {e["action_type"] for e in el.query({"source_organ": "GR"})}
    assert {"PROPOSAL", "ARCHIVE"} <= actions
    assert el.verify_chain() is True


def test_get_missing_raises():
    with pytest.raises(KeyError):
        _gr().get("nope")
