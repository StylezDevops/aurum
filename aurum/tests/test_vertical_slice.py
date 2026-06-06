"""Vertical slice: Request → PK → AG → CA → EL → RR.replay().

This test exists to catch interaction bugs — the class of failure where
individual organs are correct but their wiring is wrong.  Half the organs
can return placeholder values; the point is that the path runs end-to-end
without blowing up and that the EL record is replayable by RR.

Control-flow contract exercised:
  1. PK hard-deny short-circuits before CA (CA must never see a pk_deny action).
  2. Happy path: PK allow → AG observe/permits → CA arbitrate → EL append → RR replay.
  3. CA signal format: {signal, directive, basis, constitutional}.
  4. RR.replay() reconstructs the decision from EL without re-running the organs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from aurum.build_state import is_built
from aurum.arbitration.ca import ConflictArbiter, ArbitrationError
from aurum.durability.el import EvidenceLedger
from aurum.durability.rr import ReproducibilityRunner
from aurum.novel.ag import AuthorityGovernor
from aurum.spine.pk import PolicyKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "AG", "CA", "EL", "RR"),
    reason="one or more vertical-slice organs not built",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wired(tmp_path):
    """Return all five organs sharing a single EL instance."""
    el = EvidenceLedger(str(tmp_path / "el.db"))
    pk = PolicyKernel(el=el)
    ag = AuthorityGovernor(el=el)
    ca = ConflictArbiter(str(tmp_path / "ca.db"), el=el)
    rr = ReproducibilityRunner(el=el)
    return el, pk, ag, ca, rr


def _action(action_type="send_email", capability_class="comms", **extra):
    return {
        "action_id": str(uuid.uuid4()),
        "action_type": action_type,
        "capability_class": capability_class,
        **extra,
    }


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _log_governance_decision(el, action, pk_result, ag_permits, ca_result, pk_version):
    """Append a GOVERNANCE_DECISION event to EL so RR can replay it."""
    event_id = str(uuid.uuid4())
    el.append({
        "event_id": event_id,
        "timestamp": _utc_now(),
        "source_organ": "CA",
        "action_type": "GOVERNANCE_DECISION",
        "object_ids": [action.get("action_id", "action")],
        "payload": {
            "decision": ca_result["resolution"],
            "pk_result": pk_result,
            "ag_permits": ag_permits,
            "pk_version": pk_version,
            "inputs": action,
        },
        "evidence_confidence": 1.0,
        "evidence_source": "CA",
        "prev_hash": "",
        "hash": "",
    })
    return event_id


# ---------------------------------------------------------------------------
# Happy path — PK allow → AG → CA proceed → EL → RR replay
# ---------------------------------------------------------------------------

def test_vertical_slice_happy_path(tmp_path):
    el, pk, ag, ca, rr = _wired(tmp_path)
    action = _action()

    # 1. PK hard layer
    pk_result = pk.check(action)
    assert pk_result["decision"] == "allow", f"PK unexpectedly denied: {pk_result}"

    # 2. AG authority check
    ag.observe(action["capability_class"], {"signal": 0.5})
    ag_permits = ag.permits(action)

    # 3. Build signals for CA — PK and AG each contribute one
    ag_directive = "proceed" if ag_permits else "contract"
    signals = [
        {"signal": "PK", "directive": "proceed",
         "basis": {"rule_id": pk_result.get("rule_id"), "decision": "allow"},
         "constitutional": True},
        {"signal": "AG", "directive": ag_directive,
         "basis": {"authority": ag.authority(action["capability_class"]),
                   "permits": ag_permits},
         "constitutional": False},
    ]

    # 4. CA arbitrates — only reached because PK allowed
    ca_result = ca.arbitrate(action, signals)
    assert ca_result["resolution"] in ("proceed", "contract")

    # 5. Log to EL
    event_id = _log_governance_decision(el, action, pk_result, ag_permits, ca_result,
                                        pk.version)

    # 6. RR replay — must reconstruct the decision from EL without re-running organs
    replay = rr.replay(event_id)
    assert replay["run"]["reproduced"] is True
    assert replay["run"]["decision"] == ca_result["resolution"]
    assert replay["environment_fidelity"] == "full"  # no external APIs recorded


# ---------------------------------------------------------------------------
# PK deny short-circuits — CA must never be called
# ---------------------------------------------------------------------------

def test_vertical_slice_pk_deny_short_circuits(tmp_path):
    el, pk, ag, ca, rr = _wired(tmp_path)

    pk_deny_rules = [{"rule_id": "no-delete", "action_type": "delete_all",
                      "decision": "deny", "reason": "forbidden"}]
    pk = PolicyKernel(rules=pk_deny_rules, el=el)

    action = _action(action_type="delete_all")
    pk_result = pk.check(action)
    assert pk_result["decision"] == "deny"

    # CA must not be called after a hard deny — verify the guard raises
    action_with_deny_flag = {**action, "pk_deny": True}
    with pytest.raises(ArbitrationError):
        ca.arbitrate(action_with_deny_flag, [])


# ---------------------------------------------------------------------------
# Injection boundary — PK rejects untrusted justification before CA
# ---------------------------------------------------------------------------

def test_vertical_slice_injection_boundary(tmp_path):
    el, pk, ag, ca, rr = _wired(tmp_path)

    action = _action(justification_sources=["aa-fetched-openapi-spec"])
    pk_result = pk.check(action)
    assert pk_result["decision"] == "deny"
    assert pk_result["rule_id"] == "pk:injection-boundary"
    # CA never reached — no further assertions needed


# ---------------------------------------------------------------------------
# RR diff — context changes between two decisions are detectable
# ---------------------------------------------------------------------------

def test_vertical_slice_rr_diff(tmp_path):
    el, pk, ag, ca, rr = _wired(tmp_path)

    def _run(action_type):
        action = _action(action_type=action_type)
        pk_result = pk.check(action)
        ag_permits = ag.permits(action)
        signals = [
            {"signal": "PK", "directive": "proceed", "basis": {}, "constitutional": True},
            {"signal": "AG", "directive": "proceed", "basis": {}, "constitutional": False},
        ]
        ca_result = ca.arbitrate(action, signals)
        return _log_governance_decision(el, action, pk_result, ag_permits, ca_result,
                                        pk.version)

    eid_a = _run("read_file")
    eid_b = _run("write_file")

    ctx_a = rr.context(eid_a)
    ctx_b = rr.context(eid_b)
    # Both are full-fidelity; inputs differ (different action_type)
    assert ctx_a["inputs"]["action_type"] != ctx_b["inputs"]["action_type"]
