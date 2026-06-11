"""Operator verdicts — ed25519-signed ground truth, the ONLY thing that may EXPAND authority.

Aurum v1 is asymmetric by design: contraction is automatic (proxy failure demotes, reflexively),
but EXPANSION requires a human-grounded outcome that is VERIFIABLE — not a proxy wearing a "human"
label. So a promotion is gated on an ed25519 signature from an operator whose PUBLIC key the cage
holds; the PRIVATE key lives OUTSIDE the cage with the human (the same asymmetry as the constitution
manifest — the agent can ask to be trusted more, it cannot grant itself the trust).

The point is NOT secrecy — it is LINEAGE. A verified verdict makes a promotion ATTRIBUTABLE:

    authority(file_write) rose  because  verdict V (task t, satisfied=true)  signed by  operator K

That is a governance fact, captured BY VALUE in the TRUST_CHANGE cause + the outcome event, so a
future auditor can ask "which operators grant promotions? do operator K's promotions correlate with
later failures? is one operator systematically over-trusting?" — governance provenance, for free.

Real asymmetric signatures (ed25519). NEVER HMAC — a symmetric key in the cage could forge its own
verdicts, defeating the point. The signature covers the verdict BY VALUE (capability_class, task,
satisfied, …), so a verdict can only promote what it names and cannot be edited after signing.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


# Verdict fields that are SIGNED (and thus bound by value). A signature is valid only for exactly
# these values — changing the capability_class, the satisfied flag, the task, or the nonce after
# signing invalidates it. `nonce` makes each verdict unique so its id is a stable single-use handle.
_SIGNED_FIELDS = ("task_id", "capability_class", "satisfied", "domain", "environment", "nonce")


def canonical_verdict_bytes(verdict: Dict[str, Any]) -> bytes:
    """Deterministic serialization of the SIGNED subset of a verdict — stable across runs/
    platforms so the signature verifies identically wherever it is checked. Only _SIGNED_FIELDS
    are covered (extra annotation fields are not signed and cannot affect the authority move)."""
    signed = {k: verdict.get(k) for k in _SIGNED_FIELDS}
    return json.dumps(signed, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verdict_id(verdict: Dict[str, Any]) -> str:
    """Stable id of a verdict's signed content — the single-use handle (a replayed verdict has the
    same id, so a consumed-verdict check rejects it) and the lineage key in the ledger."""
    return hashlib.sha256(canonical_verdict_bytes(verdict)).hexdigest()


@dataclass(frozen=True)
class SignedVerdict:
    verdict: Dict[str, Any]   # the ground-truth outcome (satisfied / capability_class / task / …)
    signature_hex: str        # ed25519 signature over canonical_verdict_bytes by the operator key
    key_id: str               # which operator key signed (its public-key fingerprint)

    @property
    def id(self) -> str:
        return verdict_id(self.verdict)


def _pub_fingerprint(pub: Ed25519PublicKey) -> str:
    """Stable short id for a public key (sha256 of its 32 raw bytes, first 16 hex) — the operator
    identity carried by value into the lineage."""
    raw = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def _load_pub(pem: bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("operator key must be ed25519")
    return key


class OperatorVerdictVerifier:
    """Lives in the cage. Holds ONLY operator PUBLIC keys (it can verify, never sign). Construct
    from {label: public_pem}; the verified identity returned is the key's FINGERPRINT (so a renamed
    label can't impersonate, and the lineage records the actual key)."""

    ORGAN = "OPERATOR-IDENTITY"

    def __init__(self, public_pems: Optional[Dict[str, bytes]] = None) -> None:
        self._keys: Dict[str, Ed25519PublicKey] = {}
        for _label, pem in (public_pems or {}).items():
            pub = _load_pub(pem)
            self._keys[_pub_fingerprint(pub)] = pub   # keyed by fingerprint, not the label

    def __bool__(self) -> bool:
        return bool(self._keys)

    def verify(self, signed: SignedVerdict) -> Optional[str]:
        """Return the signing operator's key_id (fingerprint) iff the signature is valid for the
        verdict's signed content under a registered key; else None. Tries every registered key
        (the signed.key_id is a hint, never trusted — the cryptographic check decides)."""
        payload = canonical_verdict_bytes(signed.verdict)
        try:
            sig = bytes.fromhex(signed.signature_hex)
        except ValueError:
            return None
        for key_id, pub in self._keys.items():
            try:
                pub.verify(sig, payload)
                return key_id
            except InvalidSignature:
                continue
        return None


def sign_verdict(verdict: Dict[str, Any], private_key_pem: bytes) -> SignedVerdict:
    """OUT-OF-BAND signing — NOT shipped in the cage. A human runs this on a trusted machine that
    holds the private key, producing the SignedVerdict the cage verifies. The agent can never call
    this (it has no private key); it can only ASK (propose a task outcome for confirmation)."""
    priv = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError("operator signing key must be ed25519")
    sig = priv.sign(canonical_verdict_bytes(verdict))
    return SignedVerdict(verdict=dict(verdict), signature_hex=sig.hex(),
                         key_id=_pub_fingerprint(priv.public_key()))
