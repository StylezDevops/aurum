"""Forward-secure ratcheted MAC for persisted state (AURUM_ERR_046).

The problem: signing BB/EL/memory with an agent-held STATIC key lets a cage breakout forge
history (defeating poison protection); a per-write KMS call annihilates the EL fast-path.
Latency-vs-security squeeze.

The fix (verified in the runtime-integrity reference §5.2): a SYMMETRIC key derived on boot
from the external platform, ratcheted one-way after EVERY write — k_{i+1} = H(k_i). The agent
signs locally and fast; a verifier that knows the boot key replays the ratchet forward to check
every tag.

Property: a compromised agent holds only the CURRENT ratchet state; the ratchet is one-way, so
it cannot recover an earlier key and therefore cannot forge RETROACTIVE signatures — past
entries stay tamper-evident even after a breakout.

HONEST SCOPE (do not over-trust): this protects the integrity of PAST entries against a FUTURE
compromise. It does NOT make a live-compromised agent's NEW writes trustworthy (the attacker
holds the current key and ratchets forward normally). Preventing compromise is the cage's job;
this makes history unforgeable-after-the-fact. Two distinct guarantees.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import List, Sequence


def ratchet(key: bytes) -> bytes:
    """One-way ratchet: k_{i+1} = H(k_i). Cannot recover k_i from k_{i+1} (pre-image
    resistance of SHA-256) — this is what makes retroactive forgery impossible."""
    return hashlib.sha256(key).digest()


def mac(key: bytes, entry: bytes) -> bytes:
    """Local, fast MAC over one entry with the current ratchet key (no network call)."""
    return hmac.new(key, entry, hashlib.sha256).digest()


def derive_boot_key(platform_seed: bytes, *, context: bytes = b"aurum-el-mac-v1") -> bytes:
    """Derive the boot key from an external-platform seed. In production the seed is provided
    per-boot by the platform (not resident in the cage); here we HKDF-like mix it with a fixed
    context so the key is stable for a boot but distinct per purpose."""
    return hmac.new(platform_seed, context, hashlib.sha256).digest()


class ForwardSecureMAC:
    """Append-only forward-secure MAC chain. Holds only the CURRENT key after construction;
    the boot key is consumed to seed the first ratchet state and not retained, so even this
    object cannot reproduce an earlier key. A verifier needs the boot key (or a trusted
    checkpoint) to validate the log — see verify()."""

    def __init__(self, boot_key: bytes) -> None:
        if not boot_key:
            raise ValueError("boot_key must be non-empty")
        # Current ratchet state. We intentionally do NOT keep boot_key.
        self._k = bytes(boot_key)
        self._n = 0

    def sign(self, entry: bytes) -> bytes:
        """Sign one entry with the current key, then ratchet forward (one-way). Returns the
        tag. After this call the previous key is unrecoverable from self."""
        tag = mac(self._k, entry)
        self._k = ratchet(self._k)
        self._n += 1
        return tag

    def current_key(self) -> bytes:
        """The current ratchet state — what an attacker would steal on a live compromise.
        It cannot reproduce any earlier key (one-way ratchet)."""
        return bytes(self._k)

    @property
    def count(self) -> int:
        return self._n

    @staticmethod
    def verify(boot_key: bytes, entries: Sequence[bytes], tags: Sequence[bytes]) -> bool:
        """Replay the ratchet forward from the boot key and check every tag. Any tampered
        PAST entry yields a tag mismatch -> False. A verifier given the WRONG key (e.g. a
        stolen CURRENT key rather than the boot key) cannot reproduce the boot-derived tags,
        so verification fails — there is no retroactive forgery path."""
        if len(entries) != len(tags):
            return False
        k = bytes(boot_key)
        ok = True
        for entry, tag in zip(entries, tags):
            expected = mac(k, entry)
            # Constant-time compare; accumulate so a mismatch can't be timed out early.
            ok = hmac.compare_digest(expected, tag) and ok
            k = ratchet(k)
        return ok


def random_boot_key() -> bytes:
    """A fresh random boot key (for tests / first boot when no platform seed is supplied)."""
    return os.urandom(32)
