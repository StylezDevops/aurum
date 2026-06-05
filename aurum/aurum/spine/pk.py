"""PK — Policy Kernel.  Tier 0 spine (stubbed mock, per handoff mandate 4).

Runtime-enforced, versioned, testable machine-readable policy layer. Every other
organ calls PK before side effects. Carries the chain-level privilege check,
the untrusted-content boundary, precondition-scoped refusal persistence (matched
by data-flow taint paths, not topology), and the single redaction policy.

This is a MOCK satisfying the named signatures + trust-tagging assumptions. Real
rule evaluation is Opus's job; the structure and fail-closed posture are fixed.

Reconciliation (decision: THIS scaffold organ is canonical). Partial PRIOR ART runs in
the Hermes tree — `tools/skills_guard.py` (import-time AST scan / force-scan of hidden
exec+test code) and `tools/skill_ci.py` (sandboxed validate-before-promote) — and is the
live supply-chain guard today. It covers only a SLICE of PK; the spec PK also owns
`check_chain`, the untrusted-content boundary, precondition-scoped refusal persistence,
and the single redaction policy. Treat that code as a DONOR to migrate INTO this organ,
not as the organ. `build_state.BUILT['PK']` stays False — and AURUM_ERR_007/008/009 stay
skipped — until this stub is spec-complete. Do NOT flip-to-True and wire the partial impl:
that would arm the gates before they can be honestly satisfied.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from ..base import unbuilt
from ..types import GateDecision, Trust


class CheckResult(TypedDict):
    decision: GateDecision
    rule_id: Optional[str]
    reason: str


class ChainResult(TypedDict):
    decision: GateDecision
    rule_id: Optional[str]
    reason: str
    aggregate_privilege: float


class DeniedIntent(TypedDict):
    intent_sig: str          # data-flow taint signature (source->sink), not topology
    preconditions: Dict[str, Any]  # authority/tool/policy state at denial time
    expired: bool


class PolicyKernel:
    ORGAN = "PK"

    def __init__(self) -> None:
        self.version: int = 0
        self.redaction_version: int = 0

    # -- per-action and chain checks ---------------------------------------
    def check(self, action: Any) -> CheckResult:
        raise unbuilt(self.ORGAN, "check")

    def check_chain(self, actions: List[Any], plan_ctx: Any) -> ChainResult:
        """Aggregate-privilege check over a planned sequence (semantic privilege
        escalation guard). Judges the cumulative effect, not each step."""
        raise unbuilt(self.ORGAN, "check_chain")

    # -- untrusted-content boundary (prompt-injection guard) ----------------
    def tag_trust(self, payload: Any, source: str) -> Trust:
        """Ingested content (AA/SEN/BB) is 'untrusted'; only the operator's direct
        channel is 'operator'. Instructions in untrusted content never bind."""
        raise unbuilt(self.ORGAN, "tag_trust")

    def trace_justification(self, action: Any) -> List[str]:
        """Return the sources an action's justification traces to. Deny if any is
        untrusted."""
        raise unbuilt(self.ORGAN, "trace_justification")

    # -- refusal persistence (precondition-scoped, taint-path matched) ------
    def denied_intents(self) -> List[DeniedIntent]:
        raise unbuilt(self.ORGAN, "denied_intents")

    # -- redaction (single policy inherited by EL/BB/LS/MGC) ----------------
    def redact(self, payload: Any) -> Any:
        raise unbuilt(self.ORGAN, "redact")

    # -- policy lifecycle ---------------------------------------------------
    def test(self, ruleset: Any) -> bool:
        raise unbuilt(self.ORGAN, "test")
