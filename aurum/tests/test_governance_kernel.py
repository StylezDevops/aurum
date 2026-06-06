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


# ---------------------------------------------------------------------------
# OI -> BB -> AG outcome loop (asymmetric)
# ---------------------------------------------------------------------------

def test_observe_outcome_bad_demotes_and_logs(tmp_path):
    k = _k(tmp_path)
    cc = "file_write"
    before = k.ag.band(cc)
    k.observe_outcome(to_action("write_file", {"path": "/x"}),
                      {"completed": False, "error": "boom"})
    assert k.ag.band(cc) != before  # reflex demote
    events = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "outcome_demote" for e in events)


def test_observe_outcome_good_proxy_holds(tmp_path):
    """Good PROXY outcome must NOT change authority (no promote on proxy)."""
    k = _k(tmp_path)
    cc = "file_write"
    before = k.ag.authority(cc)
    for _ in range(10):
        k.observe_outcome(to_action("write_file", {"path": "/x"}),
                          {"completed": True, "quality": 1.0})
    assert k.ag.authority(cc) == before
    events = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "outcome_hold" for e in events)


def test_record_outcome_verdict_human_good_promotes(tmp_path):
    k = _k(tmp_path)
    cc = "file_write"
    before = k.ag.authority(cc)
    k.record_outcome_verdict("task-1", cc, satisfied=True)
    assert k.ag.authority(cc) > before  # only human-grounded good promotes


def test_record_outcome_verdict_human_bad_demotes(tmp_path):
    k = _k(tmp_path)
    cc = "file_write"
    before = k.ag.band(cc)
    k.record_outcome_verdict("task-2", cc, satisfied=False)
    assert k.ag.band(cc) != before


def test_constitutional_only_human_promotes_never_proxy(tmp_path):
    """The whole point: a stream of good proxy outcomes never raises authority;
    only an out-of-loop human verdict does."""
    k = _k(tmp_path)
    cc = "exec"
    start = k.ag.authority(cc)
    for _ in range(25):
        k.observe_outcome(to_action("terminal", {"command": "echo hi"}),
                          {"completed": True, "quality": 1.0})
    assert k.ag.authority(cc) == start, "proxy successes must not promote"
    k.record_outcome_verdict("t", cc, satisfied=True)
    assert k.ag.authority(cc) > start, "human-grounded good must promote"


def test_observe_outcome_never_raises(tmp_path):
    k = _k(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("oi down")

    k.oi.interpret = boom  # type: ignore[method-assign]
    # must not propagate — the action already ran; a learning-update failure is swallowed
    assert k.observe_outcome(to_action("write_file", {"path": "/x"}),
                             {"completed": True}) is None


def test_completed_unsatisfied_banks_bb_lesson(tmp_path):
    k = _k(tmp_path)
    before = len(k.bb.all_ids())
    # completed but a preference/redo failure -> OI marks unsatisfied -> BB lesson
    k.observe_outcome(to_action("write_file", {"path": "/x"}),
                      {"completed": True, "redone": True, "quality": 0.4})
    assert len(k.bb.all_ids()) > before


# ---------------------------------------------------------------------------
# Persistence-as-projection: rehydrate AG authority from the durable EL
# ---------------------------------------------------------------------------

def test_authority_rehydrated_across_kernel_restart(tmp_path):
    """A demote in 'turn 1' must survive --rm: a fresh kernel on the same durable home
    rebuilds the demoted authority from the ledger instead of re-seeding baseline."""
    home = str(tmp_path)
    k1 = GovernanceKernel(home=home)
    k1.observe_outcome(to_action("write_file", {"path": "/x"}),
                       {"completed": False, "error": "boom"})
    demoted = k1.ag.authority("file_write")
    assert k1.ag.band("file_write") == "readonly"

    k2 = GovernanceKernel(home=home)  # fresh kernel, same durable state root
    assert k2.ag.authority("file_write") == demoted
    assert k2.ag.band("file_write") == "readonly"  # learning survived the restart


def test_untouched_class_keeps_baseline_after_restart(tmp_path):
    home = str(tmp_path)
    GovernanceKernel(home=home).observe_outcome(
        to_action("write_file", {"path": "/x"}), {"completed": False})
    k2 = GovernanceKernel(home=home)
    assert k2.ag.band("exec") == "code"  # never touched → still baseline


def test_rehydration_is_silent_no_phantom_events(tmp_path):
    """Restoring authority from history must NOT re-log TRUST_CHANGE (the values were
    already audited when first set). A second boot adds no baseline-seed events."""
    home = str(tmp_path)
    k1 = GovernanceKernel(home=home)
    n1 = len(k1.el.query({"action_type": "TRUST_CHANGE", "limit": 100000}))
    GovernanceKernel(home=home)  # second boot — all classes have history → silent restore
    n2 = len(GovernanceKernel(home=home).el.query({"action_type": "TRUST_CHANGE", "limit": 100000}))
    assert n2 == n1  # no phantom re-seed events accumulate per boot


def test_tampered_ledger_not_rehydrated(tmp_path):
    """The new obligation durable state creates: a ledger that fails verify_chain MUST NOT
    drive authority. Rehydration falls back to the low-trust baseline (fail-safe)."""
    from unittest import mock
    from aurum.durability.el import EvidenceLedger
    home = str(tmp_path)
    k1 = GovernanceKernel(home=home)
    k1.observe_outcome(to_action("write_file", {"path": "/x"}), {"completed": False})
    assert k1.ag.band("file_write") == "readonly"  # history says demoted

    with mock.patch.object(EvidenceLedger, "verify_chain", return_value=False):
        k2 = GovernanceKernel(home=home)
    # verify failed → did NOT project the demote → back to baseline (contraction-safe default)
    assert k2.ag.band("file_write") == "code"


def test_verify_fail_logs_integrity_alarm(tmp_path):
    k = _k(tmp_path)
    k.el.verify_chain = lambda: False  # type: ignore[method-assign]
    assert k._authority_history() == {}
    events = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "integrity_alarm" for e in events)
