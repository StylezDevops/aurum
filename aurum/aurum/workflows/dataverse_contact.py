"""A real governed Dataverse/D365 contact workflow — end to end, with a correctly-gated
irreversible step, run in shadow mode.

This answers the only criticism that matters at this stage (build brief §6): the control plane
has no proven useful workload under it. Here a realistic multi-step Dataverse cleanup —
query → read → update → DELETE — runs entirely through GovernanceKernel.govern(): every step is
PK/AG/CA/EL-cleared BEFORE it touches state, the reversible steps proceed, and the IRREVERSIBLE
delete is gated to the FULL authority band until that authority is earned.

NO LIVE CREDENTIALS, NO NETWORK. The Dataverse side is an in-memory SHADOW (a faithful stand-in
for the D365 OData surface). This is honest, and it is also the architecture's posture: secrets
are platform-injected per request and never resident in the cage, so a "real" connector is a
deployment concern; the GOVERNANCE being exercised here is the real thing. Gated execution IS
shadow mode — a wrong/over-authority action is observed and contained, never damaging.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..action_map import to_action


class ShadowDataverse:
    """In-memory stand-in for the Dataverse/D365 contact table. No network, no credentials —
    a faithful shadow so the workflow exercises real governance without touching a tenant.
    Thread-safe (defensive: a workflow step is sequential, but the store is shared state)."""

    def __init__(self, contacts: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._lock = threading.Lock()
        self._contacts: Dict[str, Dict[str, Any]] = dict(contacts or {})

    def query(self) -> List[str]:
        with self._lock:
            return sorted(self._contacts.keys())

    def get(self, contact_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            c = self._contacts.get(contact_id)
            return dict(c) if c is not None else None

    def update(self, contact_id: str, fields: Dict[str, Any]) -> bool:
        with self._lock:
            if contact_id not in self._contacts:
                return False
            self._contacts[contact_id].update(fields)
            return True

    def delete(self, contact_id: str) -> bool:
        with self._lock:
            return self._contacts.pop(contact_id, None) is not None

    def exists(self, contact_id: str) -> bool:
        with self._lock:
            return contact_id in self._contacts


@dataclass(frozen=True)
class StepResult:
    tool: str
    allowed: bool
    executed: bool          # a gated step is NOT executed (shadow containment)
    irreversible: bool
    rule_id: Optional[str]
    reason: str
    result: Any = None


@dataclass
class WorkflowResult:
    steps: List[StepResult] = field(default_factory=list)

    @property
    def gated(self) -> List[StepResult]:
        return [s for s in self.steps if not s.allowed]

    @property
    def executed(self) -> List[StepResult]:
        return [s for s in self.steps if s.executed]

    @property
    def irreversible_contained(self) -> bool:
        """True iff every irreversible step that was NOT permitted also did NOT execute —
        the safety property the workflow exists to demonstrate."""
        return all(s.executed for s in self.steps if s.irreversible and s.allowed) and \
            not any(s.executed for s in self.steps if s.irreversible and not s.allowed)


class GovernedWorkflow:
    """Runs Dataverse steps through the real governance kernel. A step executes its shadow
    side effect ONLY after govern() allows it (write-then-act); a gated step is contained."""

    def __init__(self, kernel: Any, dataverse: ShadowDataverse,
                 domain: Optional[str] = None) -> None:
        self.kernel = kernel
        self.dv = dataverse
        self.domain = domain          # optionally domain-scope steps (adds the familiarity gate)

    def _step(self, tool: str, args: Dict[str, Any],
              execute: Callable[[], Any]) -> StepResult:
        action = to_action(tool, args, domain=self.domain)
        decision = self.kernel.govern(action)
        irreversible = bool(action.get("irreversible"))
        executed = False
        result = None
        if decision.allow:
            result = execute()         # shadow side effect, AFTER the gate cleared
            executed = True
        return StepResult(tool=tool, allowed=decision.allow, executed=executed,
                          irreversible=irreversible, rule_id=decision.rule_id,
                          reason=decision.reason, result=result)

    def run_contact_cleanup(self, contact_id: str,
                            update_fields: Optional[Dict[str, Any]] = None) -> WorkflowResult:
        """A realistic cleanup: list contacts, read one, flag it (reversible update), then try
        to delete it (irreversible). The delete is gated to FULL authority; under shadow mode a
        gated delete leaves the record intact."""
        fields = update_fields or {"statuscode": "flagged_for_review"}
        out = WorkflowResult()
        out.steps.append(self._step("dataverse_query", {}, lambda: self.dv.query()))
        out.steps.append(self._step("dataverse_get", {"id": contact_id},
                                    lambda: self.dv.get(contact_id)))
        out.steps.append(self._step("dataverse_update", {"id": contact_id, "fields": fields},
                                    lambda: self.dv.update(contact_id, fields)))
        out.steps.append(self._step("dataverse_delete", {"id": contact_id},
                                    lambda: self.dv.delete(contact_id)))
        return out
