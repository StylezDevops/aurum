"""Phase D — GovernanceTelemetry: the day-one operational dashboard (read-only derived view)."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.kernel import GovernanceKernel
from aurum.observability.telemetry import GovernanceTelemetry

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


def _k(tmp_path):
    return GovernanceKernel(home=str(tmp_path))


def test_ledger_latency_metrics(tmp_path):
    k = _k(tmp_path)
    t = GovernanceTelemetry(k)
    m = t.ledger_latency()
    assert {"append_count", "in_flight", "max_in_flight",
            "p50_ms", "p95_ms", "p99_ms", "max_ms"} <= set(m)
    assert m["append_count"] >= 1            # baseline seeding already appended
    assert m["in_flight"] == 0               # nothing in flight at rest
    assert m["p50_ms"] >= 0.0 and m["p99_ms"] >= m["p50_ms"]
    before = m["append_count"]
    k.govern(to_action("write_file", {"path": "/x", "content": "y"}))   # a proceed → append
    assert t.ledger_latency()["append_count"] > before


def test_governance_event_rate(tmp_path):
    k = _k(tmp_path)
    t = GovernanceTelemetry(k)
    cold = t.governance_event_rate()
    assert cold["total_decisions"] == 0                       # no DECISIONS yet (clean no-op)
    assert all(v == 0 for v in cold["by_outcome"].values())

    k.govern(to_action("write_file", {"path": "/x", "content": "y"}))   # proceed
    k.govern(to_action("send_email", {}))                               # deny (ag:ceiling)
    r = t.governance_event_rate()
    assert r["by_outcome"]["proceed"] >= 1
    assert r["by_outcome"]["deny"] >= 1
    assert r["total_decisions"] >= 2
    assert r["by_action_type"].get("GOVERNANCE_DECISION", 0) >= 2
    assert r["by_action_type"].get("TRUST_CHANGE", 0) >= 1    # baseline seed events


def test_authority_distribution(tmp_path):
    k = _k(tmp_path)
    d = GovernanceTelemetry(k).authority_distribution()
    assert "file_write" in d["classes"]
    assert d["classes"]["file_write"]["band"] == "code"       # baseline 0.85
    assert d["classes"]["network"]["band"] == "readonly"      # baseline 0.65
    assert sum(d["band_histogram"].values()) == d["tracked_classes"]


def test_snapshot_has_three_sections(tmp_path):
    s = GovernanceTelemetry(_k(tmp_path)).snapshot()
    assert set(s) == {"ledger", "authority", "governance"}


def test_telemetry_is_readonly(tmp_path):
    """Observability ≠ control: reading telemetry must not append events or move authority."""
    k = _k(tmp_path)
    t = GovernanceTelemetry(k)
    before_events = len(k.el.query({}))
    before_auth = k.ag.authority("file_write")
    t.snapshot()
    t.governance_event_rate()
    t.authority_distribution()
    t.ledger_latency()
    assert len(k.el.query({})) == before_events
    assert k.ag.authority("file_write") == before_auth
