"""Tests for TS — Toolsmith."""
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.spine.toolsmith import Toolsmith

pytestmark = pytest.mark.skipif(
    not is_built("TS"), reason="TS not built yet"
)


def _ts(**kwargs):
    return Toolsmith(**kwargs)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

def test_propose_returns_record():
    ts = _ts()
    record = ts.propose({"tool_id": "t1", "name": "my_tool"})
    assert record["tool_id"] == "t1"
    assert record["state"] == "proposed"


def test_propose_assigns_id_if_missing():
    ts = _ts()
    record = ts.propose({"name": "anon_tool"})
    assert record["tool_id"]
    assert record["state"] == "proposed"


def test_propose_non_dict_spec():
    ts = _ts()
    record = ts.propose("just a string spec")
    assert record["state"] == "proposed"


# ---------------------------------------------------------------------------
# Full happy-path lifecycle
# ---------------------------------------------------------------------------

def test_full_lifecycle(tmp_path):
    ts = _ts(db_path=str(tmp_path / "ts.db"))

    # propose → caged
    spec = {"tool_id": "tool_happy", "name": "HappyTool"}
    ts.propose(spec)
    ts.build_caged(spec)
    assert ts.get_tool("tool_happy")["state"] == "caged"

    # test
    result = ts.test({"tool_id": "tool_happy"})
    assert result["passed"] is True
    assert ts.get_tool("tool_happy")["state"] == "tested"

    # promote (HUMAN_GATE)
    ts.promote({"tool_id": "tool_happy"}, approved_by="dan")
    assert ts.get_tool("tool_happy")["state"] == "promoted"

    # deprecate
    ts.deprecate("tool_happy")
    assert ts.get_tool("tool_happy")["state"] == "deprecated"


# ---------------------------------------------------------------------------
# HUMAN_GATE enforcement
# ---------------------------------------------------------------------------

def test_promote_without_approver_raises():
    ts = _ts()
    spec = {"tool_id": "t_gate"}
    ts.propose(spec); ts.build_caged(spec); ts.test(spec)
    with pytest.raises(PermissionError, match="HUMAN_GATE"):
        ts.promote(spec)


def test_unquarantine_without_approver_raises():
    ts = _ts()
    spec = {"tool_id": "t_q"}
    ts.propose(spec); ts.build_caged(spec); ts.test(spec)
    ts.promote(spec, approved_by="dan")
    ts.quarantine("t_q", "reliability dropped")
    with pytest.raises(PermissionError, match="HUMAN_GATE"):
        ts.unquarantine("t_q")


# ---------------------------------------------------------------------------
# quarantine / unquarantine
# ---------------------------------------------------------------------------

def test_quarantine_blocks_from_promote():
    ts = _ts()
    spec = {"tool_id": "t_qb"}
    ts.propose(spec); ts.build_caged(spec); ts.test(spec)
    ts.quarantine("t_qb", "reliability: 60%")
    assert ts.get_tool("t_qb")["state"] == "quarantined"


def test_unquarantine_restores_to_tested():
    ts = _ts()
    spec = {"tool_id": "t_uq"}
    ts.propose(spec); ts.build_caged(spec); ts.test(spec)
    ts.quarantine("t_uq", "flaky")
    ts.unquarantine("t_uq", approved_by="dan")
    assert ts.get_tool("t_uq")["state"] == "tested"
    assert ts.get_tool("t_uq")["quarantine_reason"] is None


def test_unquarantine_non_quarantined_raises():
    ts = _ts()
    spec = {"tool_id": "t_nq"}
    ts.propose(spec); ts.build_caged(spec); ts.test(spec)
    with pytest.raises(ValueError, match="not quarantined"):
        ts.unquarantine("t_nq", approved_by="dan")


def test_quarantine_then_promote_with_gate():
    ts = _ts()
    spec = {"tool_id": "t_qp"}
    ts.propose(spec); ts.build_caged(spec); ts.test(spec)
    ts.promote(spec, approved_by="dan")
    ts.quarantine("t_qp", "oops")
    # promote from quarantined state requires approval too
    ts.promote("t_qp", approved_by="dan")
    assert ts.get_tool("t_qp")["state"] == "promoted"


# ---------------------------------------------------------------------------
# Unknown tool_id errors
# ---------------------------------------------------------------------------

def test_test_unknown_tool_raises():
    ts = _ts()
    with pytest.raises(KeyError):
        ts.test({"tool_id": "nope"})


def test_test_requires_caged_state():
    """test() must enforce state='caged' — calling it on proposed/promoted must raise."""
    ts = _ts()
    spec = {"tool_id": "t_state"}
    ts.propose(spec)
    with pytest.raises(ValueError, match="caged"):
        ts.test(spec)  # proposed → not allowed


def test_quarantine_unknown_tool_raises():
    ts = _ts()
    with pytest.raises(KeyError):
        ts.quarantine("nope", "reason")


def test_deprecate_unknown_tool_raises():
    ts = _ts()
    with pytest.raises(KeyError):
        ts.deprecate("nope")


def test_promote_from_wrong_state_raises():
    ts = _ts()
    spec = {"tool_id": "t_wrong"}
    ts.propose(spec)
    # proposed → cannot promote directly (not tested)
    with pytest.raises(ValueError, match="cannot promote"):
        ts.promote(spec, approved_by="dan")


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------

def test_list_tools_all():
    ts = _ts()
    ts.propose({"tool_id": "a"}); ts.propose({"tool_id": "b"})
    tools = ts.list_tools()
    assert len(tools) == 2


def test_list_tools_filtered_by_state():
    ts = _ts()
    spec_a = {"tool_id": "fa"}; spec_b = {"tool_id": "fb"}
    ts.propose(spec_a); ts.build_caged(spec_a)
    ts.propose(spec_b)
    caged = ts.list_tools(state="caged")
    assert len(caged) == 1
    assert caged[0]["tool_id"] == "fa"


# ---------------------------------------------------------------------------
# EL logging
# ---------------------------------------------------------------------------

def test_ts_logs_to_el(tmp_path):
    from aurum.durability.evidence_ledger import EvidenceLedger
    el = EvidenceLedger(str(tmp_path / "el.db"))
    ts = _ts(el=el)
    spec = {"tool_id": "logged_tool"}
    ts.propose(spec)
    ts.build_caged(spec)
    ts.test(spec)
    ts.promote(spec, approved_by="dan")
    events = el.query({})
    organs = [e["source_organ"] for e in events]
    assert "TS" in organs
    assert len(events) >= 4
