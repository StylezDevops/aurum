#!/usr/bin/env python3
"""Sign an operator verdict — the OUT-OF-BAND human step that EXPANDS authority (C1).

Aurum v1 is asymmetric: contraction is automatic, but authority only GROWS on a human-grounded
outcome that is cryptographically verifiable. This tool signs a verdict with an ed25519 PRIVATE
key the operator holds OUTSIDE the cage; the cage verifies it against the matching PUBLIC key and
promotes (kernel.submit_operator_verdict), recording WHICH operator by value in the lineage.

  # 1. one-time: make an operator keypair; deploy ONLY the public key into the cage
  python scripts/sign_operator_verdict.py --generate-key ./operator_keys
  cp ./operator_keys/operator_pubkey.pem <state_root>/governance/operator_pubkeys/dan.pem

  # 2. confirm an outcome (prints a SignedVerdict JSON the operator channel hands to the kernel)
  python scripts/sign_operator_verdict.py --key ./operator_keys/operator_priv.pem \
      --task t1 --capability file_write --satisfied true --domain d365

The agent can never run this (it has no private key) — it can only PROPOSE an outcome for
confirmation. The signature binds the verdict BY VALUE, so it can only promote what it names.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

_PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "aurum"))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)


def _generate_key(out_dir: str) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )
    os.makedirs(out_dir, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    with open(os.path.join(out_dir, "operator_priv.pem"), "wb") as fh:
        fh.write(priv_pem)
    with open(os.path.join(out_dir, "operator_pubkey.pem"), "wb") as fh:
        fh.write(pub_pem)
    print(f"wrote {out_dir}/operator_priv.pem (KEEP OUT OF THE CAGE) and operator_pubkey.pem")
    print("Deploy ONLY operator_pubkey.pem into <state_root>/governance/operator_pubkeys/.")
    return 0


def _sign(args) -> int:
    from aurum.integrations.operator_verdict import sign_verdict
    with open(args.key, "rb") as fh:
        priv_pem = fh.read()
    verdict = {
        "task_id": args.task,
        "capability_class": args.capability,
        "satisfied": str(args.satisfied).lower() in {"1", "true", "yes", "on"},
        "domain": args.domain,
        "environment": args.environment,
        "nonce": args.nonce or uuid.uuid4().hex,   # makes each verdict single-use
    }
    signed = sign_verdict(verdict, priv_pem)
    print(json.dumps({"verdict": signed.verdict, "signature_hex": signed.signature_hex,
                      "key_id": signed.key_id, "verdict_id": signed.id}, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sign an operator verdict (out-of-band).")
    ap.add_argument("--generate-key", metavar="DIR", help="generate an operator keypair into DIR")
    ap.add_argument("--key", help="path to the ed25519 PRIVATE key PEM (to sign)")
    ap.add_argument("--task", default="op", help="task id this verdict confirms")
    ap.add_argument("--capability", help="capability_class to move")
    ap.add_argument("--satisfied", default="true", help="true → promote, false → demote")
    ap.add_argument("--domain", default=None, help="optional domain (builds familiarity)")
    ap.add_argument("--environment", default=None, help="optional environment provenance")
    ap.add_argument("--nonce", default=None, help="optional explicit nonce (default: random)")
    args = ap.parse_args(argv)

    if args.generate_key:
        return _generate_key(args.generate_key)
    if not args.key or not args.capability:
        ap.error("--key and --capability are required to sign (or use --generate-key)")
    return _sign(args)


if __name__ == "__main__":
    raise SystemExit(main())
