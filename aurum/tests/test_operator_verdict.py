"""Operator confirmation channel (C1): a real ed25519-signed verdict is the ONLY path that
EXPANDS authority — v1 is automatic contraction, human-grounded/SIGNED expansion. The promotion is
attributable BY VALUE (authority↑ because verdict V signed by operator K), unsigned/rogue/replayed
verdicts never expand authority, and a demote is honoured even unsigned (contraction is safe).
"""
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.integrations.operator_verdict import sign_verdict
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


def _kernel_with_operator(tmp_path, pub_pem, label="op1"):
    keydir = tmp_path / "governance" / "operator_pubkeys"
    keydir.mkdir(parents=True, exist_ok=True)
    (keydir / f"{label}.pem").write_bytes(pub_pem)
    return GovernanceKernel(home=str(tmp_path))


def _verdict(cc="file_write", satisfied=True, nonce="n1"):
    return {"task_id": "t1", "capability_class": cc, "satisfied": satisfied,
            "domain": None, "environment": None, "nonce": nonce}


# ── a verified verdict promotes, attributed by value ───────────────────────────
def test_signed_verdict_promotes_and_is_attributed(tmp_path):
    priv, pub = _keypair()
    k = _kernel_with_operator(tmp_path, pub)
    before = k.ag.authority("file_write")
    signed = sign_verdict(_verdict(), priv)

    res = k.submit_operator_verdict(signed)
    assert res["promoted"] is True and res["verified"] is True
    assert res["operator_key_id"] == signed.key_id          # the actual signer, by value
    assert k.ag.authority("file_write") > before            # authority EXPANDED

    # lineage captured by value: the outcome event names the operator + verdict
    ev = next(e for e in k.el.query({"source_organ": "GOV"})
              if (e["payload"] or {}).get("outcome") == "outcome_verdict")
    assert ev["payload"]["operator_key_id"] == signed.key_id
    assert ev["payload"]["verdict_id"] == signed.id


# ── no signature → no expansion (the C1 guard) ─────────────────────────────────
def test_unverified_verdict_does_not_promote(tmp_path):
    priv, pub = _keypair()
    k = _kernel_with_operator(tmp_path, pub)
    before = k.ag.authority("file_write")

    class _Unsigned:
        verdict = _verdict()
        signature_hex = "00" * 64
        key_id = "forged"
        id = "v-unsigned"

    res = k.submit_operator_verdict(_Unsigned())
    assert res["promoted"] is False and res["verified"] is False
    assert k.ag.authority("file_write") == before           # authority NOT expanded


def test_rogue_key_does_not_promote(tmp_path):
    _real_priv, real_pub = _keypair()
    rogue_priv, _rogue_pub = _keypair()                     # signs, but its pubkey isn't deployed
    k = _kernel_with_operator(tmp_path, real_pub)
    before = k.ag.authority("file_write")
    res = k.submit_operator_verdict(sign_verdict(_verdict(), rogue_priv))
    assert res["verified"] is False and res["promoted"] is False
    assert k.ag.authority("file_write") == before


# ── a signed verdict is single-use (replay cannot over-promote) ────────────────
def test_replayed_verdict_is_rejected(tmp_path):
    priv, pub = _keypair()
    k = _kernel_with_operator(tmp_path, pub)
    signed = sign_verdict(_verdict(), priv)
    k.submit_operator_verdict(signed)
    after_first = k.ag.authority("file_write")
    res2 = k.submit_operator_verdict(signed)                # same signed verdict again
    assert res2["promoted"] is False and "replay" in res2["reason"]
    assert k.ag.authority("file_write") == after_first      # not promoted twice


# ── a demote is honoured even unsigned (contraction is always safe) ────────────
def test_unsigned_demote_is_honoured(tmp_path):
    priv, pub = _keypair()
    k = _kernel_with_operator(tmp_path, pub)
    # earn some authority first (signed), then demote unsigned
    k.submit_operator_verdict(sign_verdict(_verdict(satisfied=True, nonce="up"), priv))
    high = k.ag.authority("file_write")

    class _UnsignedDemote:
        verdict = _verdict(satisfied=False, nonce="down")
        signature_hex = "00" * 64
        key_id = "x"
        id = "v-demote"

    res = k.submit_operator_verdict(_UnsignedDemote())
    assert res["demoted"] is True and res["verified"] is False
    assert k.ag.authority("file_write") < high              # contraction honoured unsigned


# ── no operator keys deployed → contraction-only (cannot promote at all) ───────
def test_no_keys_deployed_is_contraction_only(tmp_path):
    priv, _pub = _keypair()
    k = GovernanceKernel(home=str(tmp_path))                # NO operator_pubkeys dir
    assert not k.operator_verifier or len(k.operator_verifier._keys) == 0
    before = k.ag.authority("file_write")
    res = k.submit_operator_verdict(sign_verdict(_verdict(), priv))
    assert res["promoted"] is False                         # signed, but no key to verify against
    assert k.ag.authority("file_write") == before
