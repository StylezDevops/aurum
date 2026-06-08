"""The external rope — a signed constitutional manifest the agent cannot forge (CS-EQ leg 1).

CORE is unproposable not because a boolean says `proposable=False` (parchment) but because the
agent physically lacks the key to authorize a change to its own trusted base. Secure-Boot /
signed-kernel-modules applied to an agent's CONSTITUTION.

The critical asymmetry: the cage holds ONLY the PUBLIC key (it can VERIFY a manifest); the
PRIVATE key lives OUTSIDE the cage with the human/platform identity (only it can SIGN) — the
same principle as "no durable credentials in the cage", applied to constitutional change. The
agent can PROPOSE a change (write a proposal to EL); it cannot ENACT one (it cannot produce a
valid signed manifest). Enactment = a human reviews the proposal and runs ratify() out of band
with the external key; next boot verifies.

Real asymmetric signatures (ed25519). NEVER HMAC — HMAC is symmetric, so the cage would hold the
secret and could forge its own manifest, defeating the entire point.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


class ConstitutionalBreach(RuntimeError):
    """Raised on boot when the running constitutional surface does not match a human-signed
    manifest. The correct response is FULL fail-closed (not degrade-to-read-only) — the thing
    that failed is the thing everything else rests on."""


def canonical_surface_bytes(surface: Dict[str, Any]) -> bytes:
    """Deterministic serialization so the hash is stable across runs/processes/platforms.

    INVARIANT (load-bearing — a violation causes a catastrophic false-positive boot failure):
    sort_keys=True + separators=(",",":"). The constitutional surface includes dicts/graphs
    (CORE, kinetics, immune-system thresholds, per-tool governance_required flags) that scale
    dynamically, so naive serialization yields a different SHA-256 each boot and tears the
    external rope with a false ConstitutionalBreach. NEVER hash a native Python object/repr —
    only this canonical form. Any code that hashes any part of the surface MUST route here."""
    return json.dumps(surface, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def surface_hash(surface: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_surface_bytes(surface)).hexdigest()


@dataclass(frozen=True)
class SignedManifest:
    surface_sha256: str    # hash of the human-approved constitutional surface
    version: int           # monotonic; a new ratified surface increments it
    signature_hex: str     # ed25519 signature over f"{version}:{surface_sha256}" by EXTERNAL key

    def signed_payload(self) -> bytes:
        return f"{self.version}:{self.surface_sha256}".encode("utf-8")


def _raw_from_pem(pem: bytes) -> bytes:
    """Load an ed25519 public key from PEM and return its 32 raw bytes."""
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("constitution public key must be ed25519")
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


class ConstitutionVerifier:
    """Lives in the cage. Holds ONLY the public key. Can verify, can NEVER sign — there is no
    private-key field and no sign method, so a cage compromise cannot forge a manifest."""

    ORGAN = "CSEQ-CONSTITUTION"

    def __init__(self, public_key_pem: bytes, el: Any) -> None:
        self._pub = Ed25519PublicKey.from_public_bytes(_raw_from_pem(public_key_pem))
        self._el = el

    def verify_on_boot(self, running_surface: Dict[str, Any],
                       manifest: SignedManifest) -> None:
        """Recompute the running surface's hash, check it matches the manifest, and verify the
        manifest's signature with the public key. ANY failure → ConstitutionalBreach (full
        fail-closed). This is what makes CORE 'unproposable' real rather than parchment. Logs the
        verification either way (verify_chain over the constitutional surface, on the record)."""
        running = surface_hash(running_surface)
        ok_hash = (running == manifest.surface_sha256)
        try:
            self._pub.verify(bytes.fromhex(manifest.signature_hex), manifest.signed_payload())
            ok_sig = True
        except (InvalidSignature, ValueError):
            ok_sig = False
        self._log("CONSTITUTION_VERIFY", {"version": manifest.version, "running_hash": running,
                                          "manifest_hash": manifest.surface_sha256,
                                          "hash_match": ok_hash, "sig_valid": ok_sig})
        if not (ok_hash and ok_sig):
            raise ConstitutionalBreach(
                f"constitutional surface unverified (hash_match={ok_hash}, sig_valid={ok_sig}); "
                "failing closed")

    def propose_change(self, proposed_surface: Dict[str, Any], rationale: str) -> str:
        """The agent CAN do this: write a proposed surface + rationale to EL for human review.
        It CANNOT enact it — enactment needs the external private key it does not hold. Returns
        the proposal's hash (what a human would sign to ratify)."""
        h = surface_hash(proposed_surface)
        self._log("CONSTITUTION_PROPOSAL", {"proposed_hash": h, "rationale": rationale})
        return h

    def _log(self, action_type: str, payload: Dict[str, Any]) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": self.ORGAN,
            "action_type": action_type, "object_ids": ["constitutional_surface"],
            "payload": payload, "evidence_confidence": 1.0,
            "evidence_source": self.ORGAN, "prev_hash": "", "hash": "",
        })


def ratify(proposed_surface: Dict[str, Any], version: int,
           private_key_pem: bytes) -> SignedManifest:
    """OUT-OF-BAND ratification — NOT shipped in the cage image. A human runs this on a trusted
    machine that holds the private key, after reviewing a proposal, producing the manifest the
    agent verifies on next boot. The agent can never call this — it has no private key."""
    priv = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError("ratification key must be ed25519")
    h = surface_hash(proposed_surface)
    sig = priv.sign(f"{version}:{h}".encode("utf-8"))
    return SignedManifest(surface_sha256=h, version=version, signature_hex=sig.hex())
