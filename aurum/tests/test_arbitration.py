"""Arbitration layer — CA (deterministic conservative-wins) + DD (escalate-only)."""
from __future__ import annotations

import os
import tempfile

from aurum.arbitration.conflict_arbiter import ConflictArbiter
from aurum.arbitration.deadlock_detector import DeadlockDetector
from aurum.durability.evidence_ledger import EvidenceLedger


def _ca(el=None):
    return ConflictArbiter(os.path.join(tempfile.mkdtemp(), "ca.db"), el=el)


def _sig(name, directive, just=None, const=False):
    return {"signal": name, "directive": directive,
            "basis": ({} if just is None else {"justification": just}),
            "constitutional": const}


def test_proceed_when_no_tier1_contraction():
    ca = _ca()
    out = ca.arbitrate({"action_id": "a"}, [_sig("AG", "proceed"), _sig("OI", "proceed")])
    assert out["resolution"] == "proceed" and out["winner"] is None


def test_tier2_cannot_contract():
    ca = _ca()
    # OI/LS are proceed-only; even if they "contract" they cannot block
    out = ca.arbitrate({"action_id": "a"},
                       [_sig("OI", "contract"), _sig("LS", "contract")])
    assert out["resolution"] == "proceed"


def test_most_restrictive_names_ag_over_hvp():
    ca = _ca()
    out = ca.arbitrate({"action_id": "a"},
                       [_sig("HVP", "contract"), _sig("AG", "contract")])
    assert out["resolution"] == "contract" and out["winner"] == "AG"


def test_single_signal_writes_no_record():
    ca = _ca()
    ca.arbitrate({"action_id": "a"}, [_sig("AG", "contract", 0.5)])  # only 1 directive
    assert ca.conflicts() == []


def test_ca_mirrors_to_el_conflict_log():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    ca = _ca(el=el)
    ca.arbitrate({"action_id": "a", "capability_class": "c"},
                 [_sig("AG", "contract", 0.5), _sig("OI", "proceed")])
    assert el._db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 1


def test_dd_below_min_recurrence_does_not_flag():
    ca = _ca()
    for j in (0.9, 0.5, 0.1):  # only 3 < min_recurrence(5)
        ca.arbitrate({"action_id": "a", "capability_class": "c"},
                     [_sig("AG", "contract", j), _sig("OI", "proceed")])
    assert DeadlockDetector(ca=ca).scan() == []


def test_dd_human_gated_recalibration_allowed():
    dd = DeadlockDetector()
    dd.set_parameters({"d_flag": 0.5}, human_gate=True)  # Class-C gate -> allowed
    assert dd._params["d_flag"] == 0.5


def test_dd_excludes_settled_policy_even_when_constitutional_signal_is_not_the_winner():
    # HVP (constitutional) AND AG (non-constitutional) both contract; CA names AG winner by the
    # fixed order, so winner_constitutional is False — but the contraction IS settled policy
    # (a constitutional signal is contracting), so DD must NOT escalate it as a deadlock.
    ca = _ca()
    for j in (0.9, 0.7, 0.5, 0.3, 0.1):  # >= min_recurrence, justification abating (would score high)
        ca.arbitrate({"action_id": "a", "capability_class": "c"},
                     [_sig("AG", "contract", j, const=False),
                      _sig("HVP", "contract", j, const=True)])
    assert ca.conflicts()[0]["winner"] == "AG"                       # AG named winner (non-const)
    assert DeadlockDetector(ca=ca).scan() == []                      # excluded: settled policy


def test_dd_still_escalates_a_purely_non_constitutional_abated_contraction():
    # No constitutional contractor; justification abates while the contraction sticks -> deadlock.
    ca = _ca()
    for j in (0.9, 0.7, 0.5, 0.3, 0.1):
        ca.arbitrate({"action_id": "a", "capability_class": "c"},
                     [_sig("AG", "contract", j, const=False), _sig("OI", "proceed")])
    flags = DeadlockDetector(ca=ca).scan()
    assert len(flags) == 1 and flags[0]["signature"]["capability_class"] == "c"
