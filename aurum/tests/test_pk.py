"""Tests for PK — Policy Kernel."""
from __future__ import annotations

import time

import pytest

from aurum.build_state import is_built
from aurum.spine.pk import (
    DeniedIntent, PolicyKernel, _taint_sig,
    _TAINT_SOURCES, _TAINT_SINKS,
)

pytestmark = pytest.mark.skipif(
    not is_built("PK"), reason="PK not built yet"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pk(**kwargs):
    return PolicyKernel(**kwargs)


def _action(atype, resource=None, privilege=0.0, justification_sources=None):
    a = {"action_type": atype, "privilege": privilege}
    if resource:
        a["resource"] = resource
    if justification_sources:
        a["justification_sources"] = justification_sources
    return a


# ---------------------------------------------------------------------------
# check() — basic allow/deny/needs_gate
# ---------------------------------------------------------------------------

def test_check_default_allow():
    pk = _pk()
    r = pk.check(_action("read_file"))
    assert r["decision"] == "allow"
    assert r["rule_id"] is None


def test_check_explicit_deny_rule():
    pk = _pk(rules=[{"rule_id": "r1", "action_type": "delete_all", "decision": "deny",
                      "reason": "forbidden"}])
    r = pk.check(_action("delete_all"))
    assert r["decision"] == "deny"
    assert r["rule_id"] == "r1"


def test_check_needs_gate_rule():
    pk = _pk(rules=[{"rule_id": "r2", "action_type": "promote_tool",
                      "decision": "needs_gate", "gate_class": "B", "ttl_seconds": 60}])
    r = pk.check(_action("promote_tool"))
    assert r["decision"] == "needs_gate"
    # A gate item should have been created
    assert len(pk.open_gates()) == 1


def test_check_rule_not_matched_for_different_action():
    pk = _pk(rules=[{"rule_id": "r1", "action_type": "delete_all", "decision": "deny"}])
    r = pk.check(_action("read_file"))
    assert r["decision"] == "allow"


# ---------------------------------------------------------------------------
# tag_trust / trace_justification — AURUM_ERR_008 surface
# ---------------------------------------------------------------------------

def test_tag_trust_operator():
    pk = _pk()
    assert pk.tag_trust({}, "operator") == "operator"


def test_tag_trust_untrusted():
    pk = _pk()
    for src in ("aa", "sen", "rss-feed", "external-api", ""):
        assert pk.tag_trust({}, src) == "untrusted"


def test_check_injection_boundary_denied():
    pk = _pk()
    # Action whose justification traces to an untrusted source
    action = _action("deploy", justification_sources=["aa-fetched-spec"])
    r = pk.check(action)
    assert r["decision"] == "deny"
    assert r["rule_id"] == "pk:injection-boundary"


def test_check_injection_operator_allowed():
    pk = _pk()
    action = _action("deploy", justification_sources=["operator"])
    r = pk.check(action)
    assert r["decision"] == "allow"


def test_check_injection_denied_logged_in_denied_intents():
    pk = _pk()
    pk.check(_action("x", justification_sources=["malicious-content"]))
    intents = pk.denied_intents()
    assert len(intents) == 1
    assert not intents[0]["expired"]


# ---------------------------------------------------------------------------
# check_chain() — AURUM_ERR_007: semantic privilege escalation
# ---------------------------------------------------------------------------

def test_check_chain_clean():
    pk = _pk()
    chain = [_action("read_file"), _action("write_file")]
    r = pk.check_chain(chain, {})
    assert r["decision"] == "allow"
    assert r["aggregate_privilege"] == 0.0


def test_check_chain_exfiltration_denied():
    """Steps individually pass check(); chain reads secret then exfiltrates → deny."""
    pk = _pk()
    chain = [
        _action("secret_read", privilege=0.2),
        _action("format_data"),
        _action("external_write", privilege=0.2),
    ]
    # Each step alone would pass
    for a in chain:
        if a.get("action_type") not in _TAINT_SOURCES | _TAINT_SINKS:
            assert pk.check(a)["decision"] == "allow"

    r = pk.check_chain(chain, {})
    assert r["decision"] == "deny"
    assert r["rule_id"] == "pk:chain-exfiltration"
    assert "secret_read" in r["reason"] or "external_write" in r["reason"]


def test_check_chain_aggregate_cap():
    pk = _pk(rules=[{"rule_id": "cap1", "type": "aggregate_cap",
                      "aggregate_cap": 0.5}])
    chain = [_action("step_a", privilege=0.3), _action("step_b", privilege=0.3)]
    r = pk.check_chain(chain, {})
    assert r["decision"] == "deny"
    assert "cap1" == r["rule_id"]
    assert r["aggregate_privilege"] == pytest.approx(0.6)


def test_check_chain_no_sink_clean():
    pk = _pk()
    chain = [_action("secret_read", privilege=0.1), _action("read_again")]
    r = pk.check_chain(chain, {})
    # secret_read present but no sink — chain is clean
    assert r["decision"] == "allow"


# ---------------------------------------------------------------------------
# Refusal persistence — AURUM_ERR_009: padding-resistant taint-path matching
# ---------------------------------------------------------------------------

def test_refusal_persistence_same_chain():
    pk = _pk()
    chain = [_action("secret_read"), _action("external_write")]
    r1 = pk.check_chain(chain, {})
    assert r1["decision"] == "deny"

    # Re-submit the same chain → should match prior denial
    r2 = pk.check_chain(chain, {})
    assert r2["decision"] == "deny"
    assert r2["rule_id"] == "pk:prior-denial-taint-path"


def test_refusal_persistence_padding_resistant():
    """Adding benign padding steps between source and sink doesn't break the match."""
    pk = _pk()
    # Initial denial
    chain1 = [_action("secret_read"), _action("external_write")]
    assert pk.check_chain(chain1, {})["decision"] == "deny"

    # Padded version: same source→sink with benign steps inserted
    chain2 = [
        _action("secret_read"),
        _action("format_data"),          # benign
        _action("sanitize_output"),       # benign
        _action("log_local"),             # benign
        _action("external_write"),
    ]
    r2 = pk.check_chain(chain2, {})
    assert r2["decision"] == "deny", (
        "padding-resistant: same taint path must still match prior denial"
    )
    assert r2["rule_id"] == "pk:prior-denial-taint-path"


def test_different_taint_path_not_blocked():
    """A different (novel) taint path isn't blocked by a prior denial for a different path."""
    pk = _pk()
    chain1 = [_action("secret_read"), _action("external_write")]
    pk.check_chain(chain1, {})

    # pii_read → network_send is a DIFFERENT source/sink pair
    chain2 = [_action("pii_read"), _action("network_send")]
    r2 = pk.check_chain(chain2, {})
    # First time this path is seen — denied as new exfiltration (not prior-denial)
    assert r2["decision"] == "deny"
    assert r2["rule_id"] == "pk:chain-exfiltration"


def test_intent_expire_lifts_denial():
    pk = _pk()
    chain = [_action("secret_read"), _action("external_write")]
    pk.check_chain(chain, {})
    intents = pk.denied_intents()
    assert len(intents) == 1

    # Expire the intent (preconditions changed)
    pk.expire_intent(intents[0]["intent_sig"])
    assert pk.denied_intents() == []

    # Now the chain is denied again as a NEW exfiltration (not prior-denial)
    r = pk.check_chain(chain, {})
    assert r["decision"] == "deny"
    assert r["rule_id"] == "pk:chain-exfiltration"


# ---------------------------------------------------------------------------
# Gate management — AURUM_ERR_012 surface
# ---------------------------------------------------------------------------

def test_gate_pending_then_approved():
    pk = _pk(rules=[{"rule_id": "g1", "action_type": "promote_tool",
                      "decision": "needs_gate", "gate_class": "B", "ttl_seconds": 3600}])
    pk.check(_action("promote_tool"))
    gates = pk.open_gates()
    assert len(gates) == 1
    gate_id = gates[0]["gate_id"]
    assert pk.check_gate(gate_id) == "pending"

    pk.approve_gate(gate_id, approved_by="dan")
    assert pk.check_gate(gate_id) == "approved"
    assert pk.open_gates() == []


def test_gate_expires_to_denied():
    pk = _pk(rules=[{"rule_id": "g1", "action_type": "risky_op",
                      "decision": "needs_gate", "gate_class": "C", "ttl_seconds": 10}])
    pk.check(_action("risky_op"))
    gates = pk.open_gates()
    gate_id = gates[0]["gate_id"]

    # Simulate TTL expiry by passing future time
    future = time.time() + 100
    status = pk.check_gate(gate_id, now=future)
    assert status == "expired"
    assert gates[0]["denied"] is True


def test_approve_expired_gate_raises():
    pk = _pk(rules=[{"rule_id": "g1", "action_type": "risky_op",
                      "decision": "needs_gate", "gate_class": "C", "ttl_seconds": 1}])
    pk.check(_action("risky_op"))
    gate_id = pk.open_gates()[0]["gate_id"]
    pk.check_gate(gate_id, now=time.time() + 100)  # expire it
    with pytest.raises(PermissionError):
        pk.approve_gate(gate_id, approved_by="dan")


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def test_redact_defaults():
    pk = _pk()
    payload = {"action": "deploy", "token": "abc123", "password": "hunter2",
               "metadata": {"secret": "hidden", "safe_field": "visible"}}
    result = pk.redact(payload)
    assert result["token"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"
    assert result["metadata"]["secret"] == "[REDACTED]"
    assert result["metadata"]["safe_field"] == "visible"
    assert result["action"] == "deploy"


def test_redact_list():
    pk = _pk()
    payload = [{"token": "t1"}, {"data": "ok"}]
    result = pk.redact(payload)
    assert result[0]["token"] == "[REDACTED]"
    assert result[1]["data"] == "ok"


def test_add_redact_field_bumps_version():
    pk = _pk()
    assert pk.redaction_version == 0
    pk.add_redact_field("ssn")
    assert pk.redaction_version == 1
    payload = {"ssn": "123-45-6789", "name": "Dan"}
    result = pk.redact(payload)
    assert result["ssn"] == "[REDACTED]"
    assert result["name"] == "Dan"


# ---------------------------------------------------------------------------
# Rule lifecycle
# ---------------------------------------------------------------------------

def test_load_rules_bumps_version():
    pk = _pk()
    assert pk.version == 0
    pk.load_rules([{"rule_id": "r1", "decision": "deny"}])
    assert pk.version == 1


def test_test_ruleset_valid():
    pk = _pk()
    assert pk.test([{"rule_id": "r1", "decision": "allow"}])
    assert pk.test([])


def test_test_ruleset_invalid():
    pk = _pk()
    assert not pk.test("not a list")
    assert not pk.test([{"no_rule_id": True}])
    assert not pk.test([{"rule_id": "r1", "decision": "explode"}])


# ---------------------------------------------------------------------------
# Taint signature helper
# ---------------------------------------------------------------------------

def test_taint_sig_deterministic():
    s1 = _taint_sig(frozenset({"secret_read"}), frozenset({"external_write"}))
    s2 = _taint_sig(frozenset({"secret_read"}), frozenset({"external_write"}))
    assert s1 == s2


def test_taint_sig_different_paths():
    s1 = _taint_sig(frozenset({"secret_read"}), frozenset({"external_write"}))
    s2 = _taint_sig(frozenset({"pii_read"}), frozenset({"network_send"}))
    assert s1 != s2
