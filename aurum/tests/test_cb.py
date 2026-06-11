"""CB — Circuit Breaker unit tests.

Covers the two distinct mechanisms (anomaly trip + capability-growth freeze), the
global-integrity LOCKOUT escalation, HUMAN_GATE reset/unfreeze recovery, durable
persistence across a restart, and one-directional EL audit (including the
fail-toward-tripped guarantee when the audit write fails).
"""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.support.circuit_breaker import CircuitBreaker


def _path(name="cb.db"):
    return os.path.join(tempfile.mkdtemp(), name)


def _el():
    return EvidenceLedger(_path("el.db"))


# -- anomaly breakers -------------------------------------------------------
def test_closed_by_default():
    cb = CircuitBreaker(_path())
    assert cb.state() == "closed"
    assert cb.state("API_Synthesis") == "closed"


def test_per_capability_trip_is_scoped():
    cb = CircuitBreaker(_path())
    cb.trip({"kind": "spend_spike", "capability": "API_Synthesis"})
    assert cb.state("API_Synthesis") == "open"
    # an unrelated capability and the global breaker stay closed
    assert cb.state("Email") == "closed"
    assert cb.state() == "closed"


def test_integrity_trip_escalates_to_global_lockout():
    cb = CircuitBreaker(_path())
    cb.trip({"kind": "integrity"})
    assert cb.state() == "lockout"
    # lockout shadows every capability query
    assert cb.state("anything") == "lockout"


def test_explicit_lockout_flag():
    cb = CircuitBreaker(_path())
    cb.trip({"kind": "failure_streak", "lockout": True})
    assert cb.state() == "lockout"


def test_non_dict_signal_is_tolerated():
    cb = CircuitBreaker(_path())
    cb.trip("integrity")  # bare string -> {"kind": "integrity"} -> lockout
    assert cb.state() == "lockout"


def test_reset_clears_breakers_but_not_freezes():
    cb = CircuitBreaker(_path())
    cb.trip({"kind": "integrity"})
    cb.freeze_growth("API_Synthesis")
    cb.reset()
    assert cb.state() == "closed"
    # the deliberate growth freeze is a separate axis — reset must not lift it
    assert cb.is_frozen("API_Synthesis") is True


# -- capability-growth freeze ----------------------------------------------
def test_freeze_and_unfreeze_growth():
    cb = CircuitBreaker(_path())
    assert cb.is_frozen("API_Synthesis") is False
    cb.freeze_growth("API_Synthesis")
    assert cb.is_frozen("API_Synthesis") is True
    # freeze targets ONE class; others are unaffected
    assert cb.is_frozen("Email") is False
    cb.unfreeze_growth("API_Synthesis")
    assert cb.is_frozen("API_Synthesis") is False


def test_freeze_growth_is_idempotent():
    cb = CircuitBreaker(_path())
    cb.freeze_growth("API_Synthesis")
    cb.freeze_growth("API_Synthesis")  # no error, no duplicate
    assert cb.is_frozen("API_Synthesis") is True


def test_lockout_freezes_all_growth():
    cb = CircuitBreaker(_path())
    cb.trip({"kind": "integrity"})
    # no explicit freeze, yet lockout halts growth for any class
    assert cb.is_frozen("API_Synthesis") is True
    assert cb.is_frozen("never_frozen_explicitly") is True


# -- persistence ------------------------------------------------------------
def test_lockout_survives_restart():
    path = _path()
    CircuitBreaker(path).trip({"kind": "integrity"})
    # a fresh instance on the same store must still see the emergency lockout
    assert CircuitBreaker(path).state() == "lockout"


def test_freeze_survives_restart():
    path = _path()
    CircuitBreaker(path).freeze_growth("API_Synthesis")
    assert CircuitBreaker(path).is_frozen("API_Synthesis") is True


# -- EL audit (one-directional) --------------------------------------------
def test_trip_writes_audit_event_to_el():
    el = _el()
    cb = CircuitBreaker(_path(), el=el)
    cb.trip({"kind": "integrity"})
    rows = el.query({"source_organ": "CB"})
    assert len(rows) == 1
    assert rows[0]["payload"]["cb_action"] == "trip"


def test_freeze_and_reset_write_audit_events():
    el = _el()
    cb = CircuitBreaker(_path(), el=el)
    cb.freeze_growth("API_Synthesis")
    cb.reset()
    actions = {r["payload"]["cb_action"] for r in el.query({"source_organ": "CB"})}
    assert {"freeze_growth", "reset"} <= actions


def test_trip_stays_tripped_when_audit_write_fails():
    el = _el()
    cb = CircuitBreaker(_path(), el=el)
    el._db.close()  # any subsequent EL.append now raises
    # the protective trip must survive an audit failure (fail toward tripped)
    cb.trip({"kind": "integrity"})
    assert cb.state() == "lockout"


def test_no_el_handle_is_a_silent_noop():
    cb = CircuitBreaker(_path())  # el=None
    cb.trip({"kind": "integrity"})  # must not raise despite no audit sink
    assert cb.state() == "lockout"
