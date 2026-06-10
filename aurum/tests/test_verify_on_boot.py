"""CS-EQ leg 3 wired live: the kernel verifies its constitutional surface against a signed manifest
on boot, fail-closed-HARD on a mismatch — opt-in by ratifying (no manifest deployed → no-op).

The cage holds only the PUBLIC key; only an out-of-band PRIVATE key (here, a test keypair) can
ratify. A tampered surface (different CORE/rules) no longer boots silently.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import json

import pytest

from aurum.build_state import is_built
from aurum.cseq.manifest import ConstitutionalBreach, canonical_surface_bytes, ratify
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )
    priv = Ed25519PrivateKey.generate()
    return (priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
            priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))


def _deploy_manifest(home, surface, priv_pem, pub_pem, version=1):
    man = ratify(surface, version=version, private_key_pem=priv_pem)
    base = home / "governance"
    base.mkdir(parents=True, exist_ok=True)
    (base / "constitution_pubkey.pem").write_bytes(pub_pem)
    (base / "constitution_manifest.json").write_text(json.dumps(
        {"surface_sha256": man.surface_sha256, "version": man.version,
         "signature_hex": man.signature_hex}))


# ── no manifest → tamper-evidence inactive (opt-in), boots fine ────────────────
def test_no_manifest_is_a_noop(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))            # must not raise
    assert "pk_rules" in k.constitutional_surface()


# ── ratified surface verifies on the next boot ─────────────────────────────────
def test_ratified_surface_boots(tmp_path):
    home = str(tmp_path)
    surface = GovernanceKernel(home=home, verify_constitution=False).constitutional_surface()
    priv_pem, pub_pem = _keypair()
    _deploy_manifest(tmp_path, surface, priv_pem, pub_pem)
    GovernanceKernel(home=home)                         # verify ON, surface matches → no raise


# ── a tampered surface (different rules) fails CLOSED-HARD ─────────────────────
def test_tampered_surface_fails_closed(tmp_path):
    home = str(tmp_path)
    surface = GovernanceKernel(home=home, verify_constitution=False).constitutional_surface()
    priv_pem, pub_pem = _keypair()
    _deploy_manifest(tmp_path, surface, priv_pem, pub_pem)
    with pytest.raises(ConstitutionalBreach):
        GovernanceKernel(home=home,
                         rules=[{"rule_id": "rogue", "action_type": "x", "decision": "allow"}])


# ── a manifest signed by a ROGUE key fails CLOSED-HARD ─────────────────────────
def test_rogue_signature_fails_closed(tmp_path):
    home = str(tmp_path)
    surface = GovernanceKernel(home=home, verify_constitution=False).constitutional_surface()
    rogue_priv, _ = _keypair()
    _, real_pub = _keypair()                            # deploy the WRONG public key
    _deploy_manifest(tmp_path, surface, rogue_priv, real_pub)
    with pytest.raises(ConstitutionalBreach):
        GovernanceKernel(home=home)


# ── manifest hardening: canonical serializer rejects non-JSON (no silent str()) ─
def test_canonical_surface_rejects_non_json():
    with pytest.raises(TypeError):
        canonical_surface_bytes({"bad": object()})
