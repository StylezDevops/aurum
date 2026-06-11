"""The observed/inferred constitutional boundary (Part 2). Integrity signals are a SEPARATE channel
that feeds the learning organs but can NEVER synthesize a breach or trigger a floor. Only an
observed member of the five must-never classes floors. This is the line that stands in front of the
most natural drift a learning organ can have ("this trajectory is hostile, tighten the rules").
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import inspect

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.kernel import _GOVERNANCE_FAILURE_CLASSES, _INTEGRITY_SIGNALS, GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

_DESTRUCT = {"capability_class": "file_write", "action_class": "commit_outward",
             "risk_tier": "consequential", "governance_class": "destructive_data_loss"}


def _stack_every_signal(k, cc="file_write", times=30):
    """Light up EVERY integrity signal, at max confidence, many times — a maximal stack."""
    for _ in range(times):
        for sig in _INTEGRITY_SIGNALS:
            k.record_integrity_signal(sig, {"capability_class": cc, "confidence": 1.0})


def test_invariant_and_signals_are_in_the_signed_surface(tmp_path):
    surface = GovernanceKernel(home=str(tmp_path)).constitutional_surface()
    assert "Inferred intent never does." in surface["constitutional_invariant"]
    assert sorted(surface["integrity_signals_never_floor"]) == sorted(_INTEGRITY_SIGNALS)
    # the two channels are disjoint — no integrity signal is also a floor class
    assert not (_INTEGRITY_SIGNALS & _GOVERNANCE_FAILURE_CLASSES)


def test_classify_failure_is_structurally_blind_to_integrity_signals():
    # Wiring a signal into the floor decision must require a CONSPICUOUS signature change, not a
    # quiet line. Lock the signature: no integrity/signal parameter may exist here.
    params = list(inspect.signature(GovernanceKernel._classify_failure).parameters)
    assert params == ["self", "action", "task_result", "verdict"]
    assert not any(("signal" in p) or ("integrity" in p) for p in params)


def test_integrity_signal_is_a_separate_channel_not_a_decision(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.record_integrity_signal("concealment_attempt", {"capability_class": "file_write"})
    sigs = k.el.query({"action_type": "INTEGRITY_SIGNAL"})
    assert sigs and sigs[0]["payload"]["signal"] == "concealment_attempt"
    # it is NOT a decision and NOT an outcome — distinct stream
    assert k.el.count_decisions() == 0


# ── (c) ANTI-ACCUMULATION (load-bearing) ──────────────────────────────────────
def test_maximal_signal_stack_with_no_observed_event_does_not_floor(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    cc = "file_write"
    base = k.ag.authority(cc)
    _stack_every_signal(k, cc)
    # a NORMAL outcome — no observed five-class event
    k.observe_outcome(to_action("write_file", {"path": "/x"}),
                      {"completed": True, "proxy_satisfied": True, "quality": 1.0})
    assert k.ag.authority(cc) == base                     # NOT floored — only inferred signals
    # and no governance-severity floor was synthesized from the signal stack
    floors = [e for e in k.el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE"})
              if (e["payload"].get("cause") or {}).get("severity") == "governance"]
    assert floors == []


# ── (e) SEVERITY BOUNDARY ─────────────────────────────────────────────────────
def test_task_failure_with_full_signal_stack_stays_task(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    _stack_every_signal(k)
    sev, scls = k._classify_failure(to_action("write_file", {"path": "/x"}),
                                    {"completed": False}, {"signals": {}})
    assert (sev, scls) == ("task", "task_failure")        # signals never cross task→governance


# ── (d) REPLAYABILITY INVARIANT — walks the LEDGER, not the code ──────────────
def test_every_floor_in_the_ledger_resolves_to_an_observed_class(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    # a real, legitimately-caused floor (observed five-class event) on file_write
    k.observe_outcome(to_action("danger", {}, classification=_DESTRUCT),
                      {"completed": True, "proxy_satisfied": True, "quality": 1.0})
    # plus heavy integrity-signal traffic + a normal failure on a DIFFERENT, un-floored capability
    # (network) — so a forbidden signal-driven floor would actually MOVE authority and emit a
    # TRUST_CHANGE the walk can catch (it can't hide behind file_write being already floored).
    _stack_every_signal(k, cc="network")
    k.observe_outcome(to_action("send_message", {}), {"completed": False})

    floors = [e for e in k.el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE"})
              if (e["payload"].get("cause") or {}).get("severity") == "governance"]
    assert floors                                          # at least the one legitimate floor
    for f in floors:
        cause = f["payload"]["cause"]
        # INVARIANT: every floor resolves to an observed member of the five must-never classes.
        # A floor with a cause outside the five (e.g. an integrity signal) is a TEST FAILURE —
        # it means inferred intent reached the floor. This trips on the FIRST such event regardless
        # of how a future drift was written, because it tests the ledger, not the code path.
        assert cause["severity_class"] in _GOVERNANCE_FAILURE_CLASSES
