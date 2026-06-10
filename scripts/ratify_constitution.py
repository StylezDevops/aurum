#!/usr/bin/env python3
"""Ratify the constitutional surface — the OUT-OF-BAND human step (CS-EQ leg 1).

This attaches the "external rope": it signs the kernel's current constitutional surface with an
ed25519 PRIVATE key that lives OUTSIDE the cage, producing a manifest + public key the agent
verifies on every boot (kernel._verify_constitution_on_boot). The agent can PROPOSE a change to
its constitution; it can never ENACT one, because it does not hold the private key — only this
tool, run by a human on a trusted machine, can.

  # 1. one-time: make a keypair (keep the PRIVATE key OUT of the cage/repo)
  python scripts/ratify_constitution.py --generate-key ./constitution_keys

  # 2. ratify the current surface (writes manifest.json + pubkey.pem into <home>/governance)
  python scripts/ratify_constitution.py --home <state_root> --key ./constitution_keys/constitution_priv.pem

After ratifying, the next kernel boot recomputes the surface hash and fails CLOSED-HARD if it
doesn't match the signed manifest. Re-run this tool whenever a constitutional change is approved.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import argparse
import json
import os
import sys

_PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "aurum"))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from aurum.kernel import GovernanceKernel  # noqa: E402
from aurum.paths import state_root  # noqa: E402


def _generate_key(out_dir: str) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )
    os.makedirs(out_dir, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    priv_path = os.path.join(out_dir, "constitution_priv.pem")
    pub_path = os.path.join(out_dir, "constitution_pubkey.pem")
    with open(priv_path, "wb") as fh:
        fh.write(priv_pem)
    with open(pub_path, "wb") as fh:
        fh.write(pub_pem)
    print(f"wrote {priv_path} (KEEP OUT OF THE CAGE) and {pub_path}")
    print("Deploy ONLY the public key into the cage; never the private key.")
    return 0


def _ratify(home: str, key_path: str, version: int) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from aurum.cseq.manifest import ratify

    with open(key_path, "rb") as fh:
        priv_pem = fh.read()
    # Build the kernel WITHOUT the boot check (we are creating the manifest it will verify against).
    kernel = GovernanceKernel(home=home, verify_constitution=False)
    surface = kernel.constitutional_surface()
    manifest = ratify(surface, version=version, private_key_pem=priv_pem)

    base = os.path.join(home, "governance")
    os.makedirs(base, exist_ok=True)
    man_path = os.path.join(base, "constitution_manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump({"surface_sha256": manifest.surface_sha256, "version": manifest.version,
                   "signature_hex": manifest.signature_hex}, fh, indent=2)
    # Derive + write the PUBLIC key into the cage so the next boot can verify (the private key stays
    # with the operator). The cage never receives the private key.
    priv = serialization.load_pem_private_key(priv_pem, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        print("error: key is not ed25519", file=sys.stderr)
        return 2
    pub_pem = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_path = os.path.join(base, "constitution_pubkey.pem")
    with open(pub_path, "wb") as fh:
        fh.write(pub_pem)
    print(f"ratified surface v{version} (sha256={manifest.surface_sha256[:16]}…)")
    print(f"wrote {man_path} and {pub_path}; next boot will verify against them.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ratify the Aurum constitutional surface (out-of-band).")
    ap.add_argument("--generate-key", metavar="DIR", help="generate an ed25519 keypair into DIR and exit")
    ap.add_argument("--home", default=state_root(), help="governance state root (durable mount)")
    ap.add_argument("--key", help="path to the ed25519 PRIVATE key PEM (for ratifying)")
    ap.add_argument("--version", type=int, default=1, help="manifest version (monotonic)")
    args = ap.parse_args(argv)

    if args.generate_key:
        return _generate_key(args.generate_key)
    if not args.key:
        ap.error("--key (the ed25519 private PEM) is required to ratify (or use --generate-key)")
    return _ratify(args.home, args.key, args.version)


if __name__ == "__main__":
    raise SystemExit(main())
