"""Shared types for AURUM. Lifted from the spec APPENDIX — IMPLEMENTATION REFERENCE.

These are authoritative. Organs depend on these definitions; do not redefine them
locally. Where the spec gives a TypedDict, it appears verbatim here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Optional, TypedDict

# ---------------------------------------------------------------------------
# Trust / gate primitives
# ---------------------------------------------------------------------------

Trust = Literal["operator", "untrusted"]

# Delegated gate classes (conventions block).
# A = low risk / reversible / high authority -> auto, batch-reviewed.
# B = medium risk -> batched approval.
# C = high risk / irreversible -> individual sign-off.
GateClass = Literal["A", "B", "C"]

GateDecision = Literal["allow", "deny", "needs_gate"]

# AG authority bands.
AuthorityBand = Literal["full", "code", "readonly", "advisory"]

# KVE volatility classes.
VolatilityClass = Literal["STATIC", "SLOW", "FAST"]

# HVP verification stakes.
Stakes = Literal["high_stakes", "routine"]

# RR environment fidelity (decision replay vs environment replay).
EnvironmentFidelity = Literal["full", "partial", "unavailable"]

# EL evidence strength (LS attribution discipline).
EvidenceStrength = Literal["strong", "weak"]


# ---------------------------------------------------------------------------
# EL — Evidence Ledger block (spec appendix, verbatim + evidence fields)
# ---------------------------------------------------------------------------

ELActionType = Literal[
    "VOTE", "PROMOTION", "EXCEPTION", "TRUST_CHANGE",
    "BRANCH", "PROPOSAL", "COST_ANOMALY", "ARCHIVE",
    # TS lifecycle action types (action.upper() from ts._log)
    "PROMOTE", "PROPOSE", "BUILD_CAGED", "TEST", "QUARANTINE", "UNQUARANTINE", "DEPRECATE",
    # Arbitration
    "GOVERNANCE_DECISION",
    # CS-EQ — constitutional stability (legs 1 & 3) + integrity probes
    "CONSTITUTION_VERIFY", "CONSTITUTION_PROPOSAL", "CONSTITUTION_INCIDENT",
    "EQUILIBRIUM_OBS", "INTEGRITY_PROBE",
    # FC — forced contestability (re-justification of elite pathways)
    "FC_REJUSTIFY_SCHEDULED", "FC_REJUSTIFY_PASSED", "FC_REJUSTIFY_FAILED",
]


class ELEvent(TypedDict):
    event_id: str
    timestamp: str  # ISO 8601 UTC
    source_organ: str
    action_type: ELActionType
    object_ids: List[str]
    payload: Dict[str, Any]
    evidence_confidence: float  # downstream organs weight by this
    evidence_source: str
    prev_hash: str
    hash: str


def content_hash(event: ELEvent) -> str:
    """The EXPENSIVE, chain-INDEPENDENT half of the block hash (AURUM_ERR_053).

    Hashes every field except `hash` AND `prev_hash`, in sorted-key order. Because it
    does not depend on the prior event, it can be computed OFF the single writer thread
    (process pool) — the CPU-bound `json.dumps` + `sha256` that would otherwise saturate
    one core under the GIL and back the ledger queue into LedgerBackpressure. See
    durability/el_writer.py.
    """
    serialized = json.dumps(
        {k: event[k] for k in sorted(event.keys()) if k not in ("hash", "prev_hash")},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def chain_link(content_h: str, prev_hash: str) -> str:
    """The CHEAP, serial half (AURUM_ERR_053): O(1) over two fixed-size hex strings.

    This is the only hashing the single writer does on its own thread — folding the
    pre-computed content hash into the chain at the current tip. Its input size is
    independent of the event payload, so it cannot become a per-event CPU bottleneck.
    """
    return hashlib.sha256(f"{prev_hash}:{content_h}".encode("utf-8")).hexdigest()


def calculate_block_hash(event: ELEvent) -> str:
    """Type-layer cryptographic integrity across the reasoning spine.

    Split-hash composition: chain_link(content_hash(event), event.prev_hash). Folding
    the chain-independent content hash with the prior hash keeps the chain deterministic
    and any retroactive edit detectable via verify_chain() (a tampered content field
    changes content_hash; a tampered prev_hash changes the link) — while letting the
    expensive half run off the writer thread (AURUM_ERR_053). Equivalent for direct
    callers (EL.append, verify_chain); the writer computes the two halves separately.
    """
    return chain_link(content_hash(event), event.get("prev_hash", ""))


# ---------------------------------------------------------------------------
# EG — composite uncertainty (spec appendix)
# ---------------------------------------------------------------------------

EGComponent = Literal[
    "verifier_disagreement", "retrieval_conflict",
    "tool_failure_rate", "policy_ambiguity",
    "historical_failure_similarity",
]


class EGScore(TypedDict):
    U: float  # 0.00–1.00
    components: Dict[EGComponent, float]


# ---------------------------------------------------------------------------
# CS — causal graph node with lease (spec appendix)
# ---------------------------------------------------------------------------

CSNodeType = Literal["skill", "tool", "policy_rule", "active_goal"]


class CSNode(TypedDict):
    node_id: str
    type: CSNodeType
    references: List[str]
    shared_assumptions: List[str]
    locked_by: Optional[str]
    lease_until: Optional[float]  # epoch seconds


# ---------------------------------------------------------------------------
# HVP — verifier roster entry
# ---------------------------------------------------------------------------

class EndpointEntry(TypedDict):
    id: str
    base_url: str
    api_key_ref: str          # key-store reference, never an inline secret
    model: str
    family: str               # independence is judged by family, not model
    provider: str
    trust_tier: int
    cost_class: int
    sees_sensitive: bool
