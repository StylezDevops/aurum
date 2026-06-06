"""Tests for GovernanceKernel — the live-agent governance seam.

Covers the canonical flow (PK→AG→CA→EL) on real-shaped actions and the tiered
fail-safe posture: PK/EL fault → full fail-closed; AG/CA fault → degrade to read-only.
"""
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.action_map import to_action
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


def _k(tmp_path, **kwargs):
    return GovernanceKernel(home=str(tmp_path), **kwargs)


# ---------------------------------------------------------------------------
# Canonical flow
# ---------------------------------------------------------------------------

def test_safe_read_allowed_without_el_write(tmp_path):
    k = _k(tmp_path)
    before = len(k.el.query({}))
    d = k.govern(to_action("read_file", {"path": "/x"}))
    assert d.allow is True
    assert d.reason == "safe-read"
    # SAFE_READ must not append a GOVERNANCE_DECISION (no ledger bloat on reads)
    assert len(k.el.query({})) == before


def test_consequential_write_allowed_at_baseline(tmp_path):
    k = _k(tmp_path)
    d = k.govern(to_action("write_file", {"path": "/x", "content": "y"}))
    assert d.allow is True
    assert d.reason == "ca:proceed"
    # the proceed decision is logged
    events = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "proceed" for e in events)


def test_injection_source_denied(tmp_path):
    k = _k(tmp_path)
    a = to_action("write_file", {"path": "/x"}, justification_sources=["aa-fetched-spec"])
    d = k.govern(a)
    assert d.allow is False
    assert d.rule_id == "pk:injection-boundary"


def test_exfiltration_chain_denied(tmp_path):
    k = _k(tmp_path)
    # raise network authority so the AG ceiling doesn't mask the chain deny
    k.ag.set_authority("network", 0.99)
    k.govern(to_action("read_secret", {}))          # action_type -> secret_read (source)
    d = k.govern(to_action("send_email", {}))       # action_type -> network_send (sink)
    assert d.allow is False
    assert d.rule_id == "pk:chain-exfiltration"


def test_outward_action_gated_by_ag_ceiling(tmp_path):
    k = _k(tmp_path)  # network baseline is below the full band
    d = k.govern(to_action("send_email", {}))
    assert d.allow is False
    assert d.rule_id == "ag:ceiling"


def test_tool_promotion_needs_gate(tmp_path):
    k = _k(tmp_path)
    d = k.govern(to_action("skill_manage", {"action": "promote"}))
    assert d.allow is False
    assert d.gate is not None
    assert "needs_gate" in d.reason


# ---------------------------------------------------------------------------
# Tiered fail-safe
# ---------------------------------------------------------------------------

def test_el_fault_on_allow_path_full_fail_closed(tmp_path):
    k = _k(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("disk gone")

    k.el.append = boom  # type: ignore[method-assign]
    d = k.govern(to_action("write_file", {"path": "/x"}))
    assert d.allow is False
    assert d.rule_id == "gov:fail-closed"


def test_broken_el_blocks_even_safe_read(tmp_path):
    k = _k(tmp_path)
    k.el.health = lambda: {"available": False}  # type: ignore[method-assign]
    d = k.govern(to_action("read_file", {"path": "/x"}))
    assert d.allow is False
    assert d.rule_id == "gov:fail-closed"


def test_pk_fault_full_fail_closed(tmp_path):
    k = _k(tmp_path)

    def pkboom(*_a, **_k):
        raise RuntimeError("pk bug")

    k.pk.check = pkboom  # type: ignore[method-assign]
    d = k.govern(to_action("read_file", {"path": "/x"}))
    assert d.allow is False
    assert d.degraded is True
    assert d.rule_id == "gov:fail-closed"


def test_ag_fault_degrades_to_read_only(tmp_path):
    k = _k(tmp_path)

    def agboom(*_a, **_k):
        raise RuntimeError("ag bug")

    k.ag.permits = agboom  # type: ignore[method-assign]
    # SAFE_READ never reaches AG, so it proceeds normally
    assert k.govern(to_action("read_file", {"path": "/x"})).allow is True
    # CONSEQUENTIAL hits the AG fault → degraded block
    d_write = k.govern(to_action("write_file", {"path": "/x"}))
    assert d_write.allow is False
    assert d_write.degraded is True
    assert d_write.rule_id == "gov:degraded"
    # INGEST is also blocked while degraded (no untrusted ingestion through the open door)
    d_ingest = k.govern(to_action("web_fetch", {"url": "http://x"}))
    assert d_ingest.allow is False
    assert d_ingest.degraded is True


def test_degraded_event_is_logged(tmp_path):
    k = _k(tmp_path)

    def caboom(*_a, **_k):
        raise RuntimeError("ca bug")

    k.ca.arbitrate = caboom  # type: ignore[method-assign]
    k.govern(to_action("write_file", {"path": "/x"}))
    events = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "degraded" for e in events), \
        "degraded mode must be loudly flagged to EL, never silent"
