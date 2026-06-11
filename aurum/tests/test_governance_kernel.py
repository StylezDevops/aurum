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
    # the proceed decision is recorded on the ONE replay surface (decisions + by-value snapshot),
    # NOT as a GOVERNANCE_DECISION event
    decisions = k.el.recent_decisions()
    rec = next(x for x in decisions if x["final_decision"] == "allow")
    snap = rec["snapshot"]
    assert snap["ca_outcome"]["resolution"] == "proceed"
    # the snapshot is COMPLETE and BY VALUE — every field captured at decision time
    for field in ("ag_band", "ag_authority", "earned_in", "tl_tier", "pk_outcome",
                  "chain_outcome", "ca_outcome", "environment", "identity", "reason_codes"):
        assert field in snap


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

    # the allow path writes the decision via log_decision (the replay surface), so THAT is the
    # write-then-act point that must fail closed if EL is down.
    k.el.log_decision = boom  # type: ignore[method-assign]
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
    from aurum.durability.evidence_ledger import EvidenceLedger
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
    # The single integrity gate now lives in _verify_chain_safe — the chain is verified ONCE per
    # construction and shared by every projection (M3). A verify failure returns False + logs the
    # alarm; the projections then no-op on chain_ok=False (no authority/familiarity/TL rehydrated).
    assert k._verify_chain_safe() is False
    assert k._authority_history(False) == {}
    events = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "integrity_alarm" for e in events)


# ---------------------------------------------------------------------------
# Replay why-chain: WHY authority moved, not just THAT it moved (criterion 5)
# ---------------------------------------------------------------------------

def test_demotion_records_why_chain(tmp_path):
    k = _k(tmp_path)
    k.observe_outcome(to_action("write_file", {"path": "/x"}),
                      {"completed": False, "error": "disk full"})
    why = k.why_authority("file_write")
    assert why is not None
    assert why["from"] == 0.85 and why["to"] < 0.85      # before -> after recorded
    cause = why["cause"]
    assert cause["classified"] == "bad"
    assert cause["satisfaction_source"] == "proxy"
    assert cause["tool_name"] == "write_file"
    assert "quality" in cause and "decision_id" in cause


def test_why_chain_joins_to_outcome_event(tmp_path):
    """The authority-change event and its triggering outcome event share a decision_id —
    the two ledger rows are joinable, so the cause is reconstructable."""
    k = _k(tmp_path)
    k.observe_outcome(to_action("terminal", {"command": "x"}), {"completed": False})
    outcomes = [e for e in k.el.query({"action_type": "GOVERNANCE_DECISION"})
                if e["payload"].get("outcome") == "outcome_demote"]
    assert outcomes
    outcome_did = outcomes[0]["payload"]["decision_id"]
    why = k.why_authority("exec")
    assert why["cause"]["decision_id"] == outcome_did


def test_promotion_why_is_human_grounded(tmp_path):
    k = _k(tmp_path)
    k.record_outcome_verdict("task-9", "file_write", satisfied=True)
    why = k.why_authority("file_write")
    assert why["to"] > why["from"]               # promoted
    assert why["cause"]["classified"] == "good"
    assert why["cause"]["satisfaction_source"] == "human"   # never proxy on a promote


def test_baseline_only_class_has_no_cause(tmp_path):
    """A class only ever set to baseline has a TRUST_CHANGE but no triggering cause."""
    k = _k(tmp_path)
    why = k.why_authority("exec")  # untouched beyond baseline seed
    assert why is not None and why["cause"] is None


# ---------------------------------------------------------------------------
# Severity-tiered demotion + rule-usage evidence
# ---------------------------------------------------------------------------

def test_governance_breach_floors_even_when_proxy_succeeds(tmp_path):
    """The dangerous case: a governance breach that 'succeeds' by the proxy measure must
    still floor authority (governance detection is INDEPENDENT of proxy-satisfied)."""
    k = _k(tmp_path)
    bb_before = len(k.bb.all_ids())
    k.observe_outcome(
        to_action("write_file", {"path": "/x"}),
        {"completed": True, "quality": 1.0, "governance_violation": "credential_exfil"})
    assert k.ag.band("file_write") == "advisory"          # floored
    assert k.ag.authority("file_write") == k.ag.kinetics()["floor"]
    assert len(k.bb.all_ids()) > bb_before                # breach banked to BB
    why = k.why_authority("file_write")
    assert why["cause"]["severity"] == "governance"
    assert why["cause"]["severity_class"] == "credential_exfil"


def test_task_failure_is_one_band_not_floor(tmp_path):
    k = _k(tmp_path)
    k.observe_outcome(to_action("write_file", {"path": "/x"}), {"completed": False})
    assert k.ag.band("file_write") == "readonly"          # one band, not floor
    assert k.why_authority("file_write")["cause"]["severity"] == "task"


def test_classify_failure_governance_vs_task(tmp_path):
    k = _k(tmp_path)
    a = to_action("write_file", {"path": "/x"})
    v = {"completed": True, "satisfied": True, "quality": 1.0, "signals": {}}
    assert k._classify_failure(a, {"governance_violation": "tenant_boundary"}, v) == \
        ("governance", "tenant_boundary")
    assert k._classify_failure(
        a, {}, {"signals": {"preference_violations": ["constitutional"]}}) == \
        ("governance", "constitutional")
    assert k._classify_failure(a, {}, {"signals": {}}) == ("task", "task_failure")
    # an unknown violation string is NOT a governance class → task
    assert k._classify_failure(a, {"governance_violation": "typo"}, {"signals": {}}) == \
        ("task", "task_failure")


def test_severity_evidence_counts_rule_usage(tmp_path):
    k = _k(tmp_path)
    k.observe_outcome(to_action("write_file", {"path": "/a"}), {"completed": False})
    k.observe_outcome(to_action("terminal", {"command": "x"}), {"completed": False})
    k.observe_outcome(to_action("write_file", {"path": "/b"}),
                      {"completed": True, "quality": 1.0, "governance_violation": "data_destruction"})
    ev = k.severity_evidence()
    assert ev["task_failure"] == 2
    assert ev["data_destruction"] == 1
