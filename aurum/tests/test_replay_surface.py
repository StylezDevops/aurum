"""The ONE replay surface (H3): every govern decision is recorded in the decisions table with a
COMPLETE, BY-VALUE evidence snapshot — not a GOVERNANCE_DECISION event, not a pointer into mutable
state. A future auditor reconstructs the decision (what / why / which authority state / which
organ / which evidence) from the decision row alone. The score is a compression; this is the
evidence behind it (governance provenance / institutional memory).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

_FIELDS = ("ag_band", "ag_authority", "earned_in", "familiarity", "tl_tier", "pk_outcome",
           "chain_outcome", "ca_outcome", "environment", "identity", "reason_codes", "taint")


def test_allow_decision_is_complete_and_by_value(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.govern(to_action("write_file", {"path": "/x", "content": "y"}))
    rec = next(d for d in k.el.recent_decisions() if d["final_decision"] == "allow")
    snap = rec["snapshot"]
    for f in _FIELDS:                                   # every field captured, by value
        assert f in snap
    assert snap["pk_outcome"] == "allow" and snap["ca_outcome"]["resolution"] == "proceed"
    assert isinstance(snap["ag_authority"], (int, float)) and isinstance(snap["earned_in"], list)
    assert "actions" in snap["identity"]                # the JIT scope grant, captured whole


def test_deny_decision_records_the_reason_and_state(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.govern(to_action("send_email", {}))               # network/commit_outward → ag:ceiling deny
    rec = next(d for d in k.el.recent_decisions() if d["final_decision"] == "deny")
    snap = rec["snapshot"]
    assert "ag:ceiling" in snap["reason_codes"]         # which policy fired — by value
    assert snap["ag_band"] in ("advisory", "readonly", "code", "full")
    assert snap["capability_class"] == "network"


def test_snapshot_is_by_value_not_a_pointer(tmp_path):
    # The load-bearing property: the snapshot is the authority state AT DECISION TIME, copied
    # literally. Mutating AG afterwards must NOT change what the recorded decision shows — a
    # reference/seq into live state would drift; a by-value capture cannot.
    k = GovernanceKernel(home=str(tmp_path))
    k.govern(to_action("write_file", {"path": "/x", "content": "y"}))
    rec = next(d for d in k.el.recent_decisions() if d["final_decision"] == "allow")
    authority_at_decision = rec["snapshot"]["ag_authority"]
    band_at_decision = rec["snapshot"]["ag_band"]

    # Drive authority somewhere else entirely (floor it) AFTER the decision was recorded.
    k.ag.set_authority("file_write", 0.0)
    assert k.ag.authority("file_write") != authority_at_decision   # live state moved

    rec2 = next(d for d in k.el.recent_decisions() if d["final_decision"] == "allow")
    assert rec2["snapshot"]["ag_authority"] == authority_at_decision  # the record did NOT
    assert rec2["snapshot"]["ag_band"] == band_at_decision


def test_govern_decisions_are_not_governance_decision_events(tmp_path):
    # The pretension is gone: a per-action decision lives ONLY on the replay surface, not as a
    # GOVERNANCE_DECISION event (those remain for outcomes/health alarms — a different surface).
    k = GovernanceKernel(home=str(tmp_path))
    k.govern(to_action("write_file", {"path": "/x", "content": "y"}))
    gov_events = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert not any((e["payload"] or {}).get("outcome") in ("proceed", "allow", "deny", "needs_gate")
                   for e in gov_events)                 # no per-action decision rides the event stream
    assert k.el.count_decisions() >= 1                  # it's on the decisions surface instead
