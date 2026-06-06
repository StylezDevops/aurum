"""PK — Policy Kernel.  Tier 0 spine.

Runtime-enforced, versioned, testable machine-readable policy layer. Every other
organ calls PK.check(action) before side effects.

Key mechanisms implemented here:
  • CHAIN-LEVEL PRIVILEGE CHECK (AURUM_ERR_007): aggregate privilege / exfiltration
    detection — individually-allowed steps that sum to a forbidden taint path are
    denied by check_chain.
  • UNTRUSTED-CONTENT BOUNDARY (AURUM_ERR_008): payload from AA/SEN/BB is tagged
    'untrusted' at the boundary; instructions therein cannot trigger actions, raise
    authority, or satisfy a gate.
  • REFUSAL PERSISTENCE / PADDING-RESISTANT (AURUM_ERR_009): denial signature is the
    data-flow taint path (source→sink pairs), not graph topology.  Adding benign
    padding steps does NOT break the match.
  • GATE MANAGEMENT (AURUM_ERR_012): Class-B/C actions return needs_gate; gates that
    expire without an approver flip to deny.
  • REDACTION POLICY (single source): versioned, inherited by EL/BB/LS/MGC.

"Real rule evaluation is Opus's job; the structure and fail-closed posture are fixed."
The rule table is injected at construction time; the taint and gate mechanics are
fully implemented and test-armed.

Reconciliation note: Hermes-tree tools/skills_guard.py + tools/skill_ci.py are live
prior art covering a SLICE of PK (import-time AST scan / sandboxed validate-before-promote).
Treat those as donors to migrate in later; this organ owns the full spec surface.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, TypedDict

from ..types import GateDecision, Trust


# ---------------------------------------------------------------------------
# Public TypedDicts (imported by tests and other organs)
# ---------------------------------------------------------------------------

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


class GateItem(TypedDict):
    gate_id: str
    rule_id: Optional[str]
    gate_class: str          # "A", "B", or "C"
    created_at: float        # epoch seconds
    ttl_seconds: float
    approved_by: Optional[str]
    denied: bool             # True once TTL expired without approval


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Default fields redacted by PK.redact (case-insensitive key match).
_DEFAULT_REDACT: FrozenSet[str] = frozenset({
    "password", "token", "secret", "api_key", "auth", "credential",
    "private_key", "access_key", "session_key", "bearer", "authorization",
})

# Action types treated as sensitive taint sources / exfiltration sinks.
_TAINT_SOURCES: FrozenSet[str] = frozenset({
    "secret_read", "credential_read", "pii_read", "trusted_context_read",
})
_TAINT_SINKS: FrozenSet[str] = frozenset({
    "external_write", "network_send", "file_exfil", "log_external",
})


def _taint_sig(sources: FrozenSet[str], sinks: FrozenSet[str]) -> str:
    """Stable 16-char hash for a (source-set, sink-set) taint path.

    Data-flow only — not topology.  Identical sources→sinks produce the same
    signature regardless of what intermediate steps exist in the chain.
    """
    key = json.dumps(
        {"sources": sorted(sources), "sinks": sorted(sinks)},
        sort_keys=True,
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# PolicyKernel
# ---------------------------------------------------------------------------

class PolicyKernel:
    ORGAN = "PK"

    def __init__(
        self,
        rules: Optional[List[Dict[str, Any]]] = None,
        redact_fields: Optional[Set[str]] = None,
        el: Any = None,
    ) -> None:
        self.version: int = 0
        self.redaction_version: int = 0
        self._rules: List[Dict[str, Any]] = list(rules or [])
        self._denied_intents: List[DeniedIntent] = []
        self._gates: Dict[str, GateItem] = {}
        self._redact_fields: Set[str] = set(redact_fields or _DEFAULT_REDACT)
        self._el = el

    # -- rule helpers -------------------------------------------------------

    def _match_rule(
        self, rule: Dict[str, Any], action: Dict[str, Any]
    ) -> Optional[CheckResult]:
        """Return a CheckResult if rule matches the action, else None."""
        rtype = rule.get("action_type")
        if rtype and rtype != action.get("action_type", action.get("type")):
            return None
        rresource = rule.get("resource")
        if rresource and rresource != action.get("resource"):
            return None
        decision: GateDecision = rule.get("decision", "allow")
        return CheckResult(
            decision=decision,
            rule_id=rule.get("rule_id"),
            reason=rule.get("reason", "rule matched"),
        )

    # -- taint-path helpers -------------------------------------------------

    def _extract_taint_paths(
        self, actions: List[Dict[str, Any]]
    ) -> List[Tuple[FrozenSet[str], FrozenSet[str]]]:
        """Extract (sources, sink) pairs — one entry per exfiltration sink encountered."""
        accumulated_sources: Set[str] = set()
        paths: List[Tuple[FrozenSet[str], FrozenSet[str]]] = []
        for a in actions:
            atype = a.get("action_type", a.get("type", ""))
            if atype in _TAINT_SOURCES:
                accumulated_sources.add(atype)
            elif atype in _TAINT_SINKS and accumulated_sources:
                paths.append((frozenset(accumulated_sources), frozenset({atype})))
        return paths

    def _record_denial(
        self,
        sig: str,
        preconditions: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._denied_intents.append(
            DeniedIntent(
                intent_sig=sig,
                preconditions=preconditions or {"policy_version": self.version},
                expired=False,
            )
        )

    def _matches_prior_denial(
        self, paths: List[Tuple[FrozenSet[str], FrozenSet[str]]]
    ) -> Optional[str]:
        """Return the matching intent_sig if any taint path matches a live prior denial."""
        live_sigs = {d["intent_sig"] for d in self._denied_intents if not d["expired"]}
        for sources, sinks in paths:
            sig = _taint_sig(sources, sinks)
            if sig in live_sigs:
                return sig
        return None

    # -- per-action check ---------------------------------------------------

    def check(self, action: Any) -> CheckResult:
        """Check a single action.

        Order: (1) injection-boundary guard, (2) rule table match, (3) default allow.
        """
        if not isinstance(action, dict):
            raise TypeError(
                f"PK.check: action must be a dict, got {type(action).__name__!r}"
            )

        # AURUM_ERR_008: justification tracing — untrusted source → deny
        for src in self.trace_justification(action):
            if self.tag_trust({}, src) == "untrusted":
                sig = f"injection:{src}"
                self._record_denial(sig, {"policy_version": self.version})
                return CheckResult(
                    decision="deny",
                    rule_id="pk:injection-boundary",
                    reason=f"justification traces to untrusted source: {src}",
                )

        # Rule table
        for rule in self._rules:
            result = self._match_rule(rule, action)
            if result is not None:
                if result["decision"] == "needs_gate":
                    gate_class = rule.get("gate_class", "B")
                    ttl = float(rule.get("ttl_seconds", 3600.0))
                    rule_id = rule.get("rule_id")
                    # Deduplicate: only one open gate per rule_id at a time.
                    already_open = any(
                        g["rule_id"] == rule_id
                        for g in self.open_gates()
                    )
                    if not already_open:
                        self._create_gate(rule_id, gate_class, ttl)
                return result

        return CheckResult(decision="allow", rule_id=None, reason="no-rule-matched")

    # -- chain check --------------------------------------------------------

    def check_chain(self, actions: List[Any], plan_ctx: Any) -> ChainResult:
        """Aggregate-privilege check over a planned action sequence.

        Denies (a) prior-denial taint-path matches (padding-resistant, AURUM_ERR_009),
        (b) new exfiltration paths (AURUM_ERR_007), (c) aggregate privilege cap violations.
        """
        paths = self._extract_taint_paths(actions)
        agg_privilege = sum(
            float(a.get("privilege", 0.0)) for a in actions if isinstance(a, dict)
        )

        # AURUM_ERR_009: padding-resistant prior-denial check
        prior_sig = self._matches_prior_denial(paths)
        if prior_sig is not None:
            return ChainResult(
                decision="deny",
                rule_id="pk:prior-denial-taint-path",
                reason=f"taint path matches prior denial {prior_sig!r}",
                aggregate_privilege=agg_privilege,
            )

        # AURUM_ERR_007: new taint path (exfiltration).
        # Record ALL paths before returning so padding-resistant matching (AURUM_ERR_009)
        # covers every path in the chain, not just the first one encountered.
        if paths:
            first_sources, first_sinks = paths[0]
            for sources, sinks in paths:
                sig = _taint_sig(sources, sinks)
                self._record_denial(sig, {"policy_version": self.version})
            return ChainResult(
                decision="deny",
                rule_id="pk:chain-exfiltration",
                reason=f"taint path: {sorted(first_sources)} -> {sorted(first_sinks)}",
                aggregate_privilege=agg_privilege,
            )

        # Aggregate cap rules
        for rule in self._rules:
            if rule.get("type") == "aggregate_cap":
                cap = float(rule.get("aggregate_cap", float("inf")))
                if agg_privilege > cap:
                    return ChainResult(
                        decision="deny",
                        rule_id=rule.get("rule_id"),
                        reason=(
                            f"aggregate privilege {agg_privilege:.2f} "
                            f"exceeds cap {cap:.2f}"
                        ),
                        aggregate_privilege=agg_privilege,
                    )

        return ChainResult(
            decision="allow",
            rule_id=None,
            reason="chain-clean",
            aggregate_privilege=agg_privilege,
        )

    # -- untrusted-content boundary -----------------------------------------

    def tag_trust(self, payload: Any, source: str) -> Trust:
        """'operator' iff source is the direct operator channel; else 'untrusted'."""
        return "operator" if source == "operator" else "untrusted"

    def trace_justification(self, action: Any) -> List[str]:
        """Return the source identifiers an action's justification traces to."""
        if not isinstance(action, dict):
            return []
        return list(action.get("justification_sources", []))

    # -- refusal persistence ------------------------------------------------

    def denied_intents(self) -> List[DeniedIntent]:
        """Return live (non-expired) denied intent signatures."""
        return [d for d in self._denied_intents if not d["expired"]]

    def expire_intent(self, intent_sig: str) -> None:
        """Expire a denial when its preconditions genuinely change."""
        for d in self._denied_intents:
            if d["intent_sig"] == intent_sig:
                d["expired"] = True

    # -- gate management (AURUM_ERR_012) ------------------------------------

    def _create_gate(
        self,
        rule_id: Optional[str],
        gate_class: str,
        ttl_seconds: float,
    ) -> str:
        gate_id = str(uuid.uuid4())
        self._gates[gate_id] = GateItem(
            gate_id=gate_id,
            rule_id=rule_id,
            gate_class=gate_class,
            created_at=time.time(),
            ttl_seconds=ttl_seconds,
            approved_by=None,
            denied=False,
        )
        return gate_id

    def approve_gate(self, gate_id: str, approved_by: str) -> None:
        """Record a human approval for a pending gate."""
        item = self._gates.get(gate_id)
        if item is None:
            raise KeyError(f"PK: no gate {gate_id!r}")
        if item["denied"]:
            raise PermissionError(f"PK: gate {gate_id!r} already expired to denied")
        item["approved_by"] = approved_by

    def check_gate(self, gate_id: str, now: Optional[float] = None) -> str:
        """Return 'approved' | 'pending' | 'expired'.

        An expired gate transitions to denied and is irreversible.
        """
        item = self._gates.get(gate_id)
        if item is None:
            raise KeyError(f"PK: no gate {gate_id!r}")
        if item["approved_by"] is not None:
            return "approved"
        t = now if now is not None else time.time()
        if t >= item["created_at"] + item["ttl_seconds"]:
            item["denied"] = True
            return "expired"
        return "pending"

    def open_gates(self) -> List[GateItem]:
        """Return all gate items that are pending and not yet expired."""
        now = time.time()
        result = []
        for item in self._gates.values():
            if not item["denied"] and item["approved_by"] is None:
                if now < item["created_at"] + item["ttl_seconds"]:
                    result.append(item)
        return result

    # -- redaction ----------------------------------------------------------

    def redact(self, payload: Any) -> Any:
        """Recursively redact sensitive fields.  Single policy for all organs."""
        if isinstance(payload, dict):
            return {
                k: "[REDACTED]"
                if k.lower() in self._redact_fields
                else self.redact(v)
                for k, v in payload.items()
            }
        if isinstance(payload, list):
            return [self.redact(item) for item in payload]
        return payload

    def add_redact_field(self, field: str) -> None:
        """Extend the redaction policy in place.  Bumps redaction_version."""
        self._redact_fields.add(field.lower())
        self.redaction_version += 1

    # -- policy lifecycle ---------------------------------------------------

    def load_rules(self, rules: List[Dict[str, Any]]) -> None:
        """Replace the rule table and bump version."""
        self._rules = list(rules)
        self.version += 1

    def test(self, ruleset: Any) -> bool:
        """Validate a proposed ruleset.  Returns True iff structurally valid."""
        if not isinstance(ruleset, list):
            return False
        for rule in ruleset:
            if not isinstance(rule, dict) or "rule_id" not in rule:
                return False
            if "decision" in rule and rule["decision"] not in (
                "allow", "deny", "needs_gate"
            ):
                return False
        return True
