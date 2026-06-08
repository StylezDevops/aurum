"""Phase D — one real governed workflow end to end, with a correctly-gated irreversible step.

The Dataverse cleanup (query → read → update → DELETE) runs entirely through the real kernel.
The reversible steps proceed; the IRREVERSIBLE delete is gated to the FULL band and, under
shadow mode, leaves the record intact. Earning top authority unlocks it — proving the gate was
the only thing holding it. This is the proven-useful-workload-under-the-control-plane claim.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.kernel import GovernanceKernel
from aurum.observability.telemetry import GovernanceTelemetry
from aurum.workflows import GovernedWorkflow, ShadowDataverse

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


def _setup(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    dv = ShadowDataverse({"c1": {"name": "Acme Ltd", "statuscode": "active"}})
    return k, dv


def _by_tool(res):
    return {s.tool: s for s in res.steps}


def test_workflow_gates_irreversible_delete_and_contains_it(tmp_path):
    k, dv = _setup(tmp_path)
    res = GovernedWorkflow(k, dv).run_contact_cleanup("c1")
    steps = _by_tool(res)

    # Reversible steps proceeded and took effect (shadow).
    assert steps["dataverse_query"].executed and steps["dataverse_query"].allowed
    assert steps["dataverse_get"].executed
    assert steps["dataverse_update"].executed
    assert dv.get("c1")["statuscode"] == "flagged_for_review"

    # The irreversible delete is gated to FULL authority and is NOT executed → record intact.
    d = steps["dataverse_delete"]
    assert d.irreversible is True
    assert d.allowed is False and d.executed is False
    assert d.rule_id == "ag:ceiling"
    assert dv.exists("c1"), "a gated irreversible delete must not run (shadow containment)"
    assert res.irreversible_contained is True


def test_gated_delete_is_logged_and_auditable(tmp_path):
    k, dv = _setup(tmp_path)
    GovernedWorkflow(k, dv).run_contact_cleanup("c1")

    # The denial is on the append-only ledger — auditable / replayable, not silent.
    decisions = k.el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "deny"
               and e["payload"].get("rule_id") == "ag:ceiling"
               for e in decisions)

    # Telemetry reflects the real workload: a proceed (update) and a deny (the delete).
    rate = GovernanceTelemetry(k).governance_event_rate()
    assert rate["by_outcome"]["proceed"] >= 1
    assert rate["by_outcome"]["deny"] >= 1


def test_earned_full_authority_unlocks_the_irreversible_delete(tmp_path):
    k, dv = _setup(tmp_path)
    # The gate is the ONLY thing holding the delete. Grant top authority the legitimate way —
    # human-grounded-good outcomes promote the class to the FULL band — and the SAME
    # irreversible step now proceeds.
    for i in range(3):
        k.record_outcome_verdict(f"t{i}", "dataverse", satisfied=True)
    assert k.ag.band("dataverse") == "full"

    res = GovernedWorkflow(k, dv).run_contact_cleanup("c1")
    d = _by_tool(res)["dataverse_delete"]
    assert d.allowed is True and d.executed is True
    assert dv.exists("c1") is False           # deleted, correctly, under earned full authority


def test_proxy_success_cannot_unlock_the_delete(tmp_path):
    """The forbidden-feedback guard at the workflow level: a stream of PROXY successes must not
    raise authority to the band that would permit the irreversible delete."""
    k, dv = _setup(tmp_path)
    from aurum.action_map import to_action
    for _ in range(25):
        k.observe_outcome(to_action("dataverse_update", {"id": "c1", "fields": {}}),
                          {"completed": True, "quality": 1.0})
    assert k.ag.band("dataverse") != "full"   # proxy never promotes
    d = _by_tool(GovernedWorkflow(k, dv).run_contact_cleanup("c1"))["dataverse_delete"]
    assert d.allowed is False and dv.exists("c1")
