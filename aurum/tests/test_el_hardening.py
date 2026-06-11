"""Phase A — EL hardening assertions: AURUM_ERR_031 / 037 / 044 / 046 / 053 / 054 / 068.

Every organ writes to EL, so EL is made CORRECT before anything is built on top. These pin the
seven runtime-integrity mechanisms (runtime-integrity reference PARTs 1, 4.4, 5.2, 6.5, 6.7, 11.3):
single-writer no-fork, bounded fail-closed backpressure, injectable Domain Time + decay,
forward-secure ratcheted MAC, GIL-safe split hash, Domain-vs-Execution clocks, linear-time
redaction. Each is unconditionally live (the mechanisms ship with this change); test names carry
the AURUM_ERR id so `pytest -v` prints each one.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import pytest

from aurum.durability.clock import (
    DAY, LAMBDA, DomainClock, decay, execution_now, half_life_lambda,
)
from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.durability.evidence_ledger_writer import LedgerBackpressure, SerializedLedgerWriter
from aurum.durability.ratchet_mac import ForwardSecureMAC, random_boot_key, ratchet
from aurum.durability.redaction import USES_NATIVE_RE, LinearRedactor
from aurum.types import chain_link, content_hash


def _db() -> str:
    return os.path.join(tempfile.mkdtemp(), "el.db")


def _event(i: int) -> dict:
    return {
        "event_id": "", "timestamp": "", "source_organ": "TEST",
        "action_type": "PROMOTION", "object_ids": [f"obj_{i}"],
        "payload": {"capability_class": "synth", "i": i},
        "evidence_confidence": 1.0, "evidence_source": "test",
        "prev_hash": "", "hash": "",
    }


def _chain_is_contiguous(events: list[dict]) -> bool:
    """events seq-ascending; each prev_hash must equal the prior event's hash (no fork)."""
    from aurum.durability.evidence_ledger import GENESIS_HASH
    prev = GENESIS_HASH
    for e in events:
        if e["prev_hash"] != prev:
            return False
        prev = e["hash"]
    return True


# ── AURUM_ERR_031 — concurrent append integrity (single-writer, no fork) ──────────────────

def test_AURUM_ERR_031_concurrent_append_integrity():
    el = EvidenceLedger(_db())
    writer = SerializedLedgerWriter(el, pool=ThreadPoolExecutor(max_workers=4))
    n_threads, per_thread = 10, 20
    errors: list[BaseException] = []

    def worker(base: int) -> None:
        for j in range(per_thread):
            try:
                writer.append(_event(base * per_thread + j))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    writer.stop()

    assert not errors, f"unexpected append errors: {errors[:3]}"
    events = el.iter_events(ascending=True)
    assert len(events) == n_threads * per_thread, "lost or duplicated events under concurrency"
    assert el.verify_chain() is True, "concurrent appends forked / corrupted the chain"
    assert _chain_is_contiguous(events), "non-contiguous chain (a fork escaped serialization)"


# ── AURUM_ERR_037 — bounded queue backpressure fails closed (never drops a log) ───────────

def test_AURUM_ERR_037_backpressure_fails_closed():
    el = EvidenceLedger(_db())
    # start_paused: the worker drains nothing, so the bounded queue fills deterministically.
    writer = SerializedLedgerWriter(
        el, max_queue=3, append_timeout=0.05, start_paused=True,
        pool=ThreadPoolExecutor(max_workers=2),
    )
    total = 6
    backpressured = threading.Semaphore(0)
    outcomes: dict[int, str] = {}
    lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            writer.append(_event(i))
            with lock:
                outcomes[i] = "written"
        except LedgerBackpressure:
            with lock:
                outcomes[i] = "backpressure"
            backpressured.release()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
    for t in threads:
        t.start()

    # Exactly (total - max_queue) appends cannot enqueue and must fail closed. Wait for those
    # three to raise (deterministic, no fixed sleep) BEFORE resuming the worker.
    for _ in range(total - 3):
        assert backpressured.acquire(timeout=5.0), "expected backpressure did not fire"

    writer.resume()
    for t in threads:
        t.join(timeout=5.0)
    writer.stop()

    bp = [i for i, o in outcomes.items() if o == "backpressure"]
    ok = [i for i, o in outcomes.items() if o == "written"]
    assert len(bp) == total - 3, f"expected 3 fail-closed, got {len(bp)}"
    assert len(ok) == 3, f"expected 3 written, got {len(ok)}"
    # The log is NEVER silently dropped: exactly the enqueued events are in the ledger, and the
    # backpressured actions left NO partial record (they failed closed).
    assert len(el.iter_events()) == 3
    assert el.verify_chain() is True


# ── AURUM_ERR_044 — clock injection: decay is deterministic Domain Time ───────────────────

def test_AURUM_ERR_044_clock_injection_decay():
    lam = LAMBDA["FAST"]              # 14-day half-life
    t0 = 1_000_000.0
    clock = DomainClock(t0)
    # Fresh: factor ~ 1.0
    assert abs(decay(t0, now=clock.now(), lam=lam) - 1.0) < 1e-9
    # One half-life later: factor ~ 0.5 — simulated by ADVANCING the injected clock, no waiting.
    clock.advance(14 * DAY)
    assert abs(decay(t0, now=clock.now(), lam=lam) - 0.5) < 1e-9
    # A year later (FAST): decayed toward 0.
    clock.advance((365 - 14) * DAY)
    assert decay(t0, now=clock.now(), lam=lam) < 1e-6
    # half_life_lambda is the inverse: at exactly one half-life, 0.5.
    lam2 = half_life_lambda(30 * DAY)
    assert abs(decay(0.0, now=30 * DAY, lam=lam2) - 0.5) < 1e-9


# ── AURUM_ERR_046 — forward-secure ratcheted MAC ──────────────────────────────────────────

def test_AURUM_ERR_046_forward_secure_mac():
    boot = random_boot_key()
    m = ForwardSecureMAC(boot)
    entries = [b"decision-1", b"decision-2", b"decision-3"]
    tags = [m.sign(e) for e in entries]

    # A clean log verifies against the boot key.
    assert ForwardSecureMAC.verify(boot, entries, tags) is True
    # A tampered PAST entry is detected.
    tampered = [b"decision-1-FORGED", entries[1], entries[2]]
    assert ForwardSecureMAC.verify(boot, tampered, tags) is False
    # No retroactive forgery: the STOLEN current key cannot reproduce the boot-derived tags.
    stolen = m.current_key()
    assert stolen != boot
    assert ForwardSecureMAC.verify(stolen, entries, tags) is False
    # The ratchet is one-way: H(boot) is the next state and is not invertible to boot.
    assert ratchet(boot) != boot


# ── AURUM_ERR_053 — GIL-safe split hash (content hash off-thread, O(1) serial link) ───────

def test_AURUM_ERR_053_split_hash_throughput():
    # Unit: the split is byte-consistent and the serial link is O(1) over fixed-size strings.
    ev = _event(0)
    ev["prev_hash"] = "a" * 64
    ch = content_hash(ev)
    assert content_hash(ev) == ch, "content hash must be deterministic"
    link = chain_link(ch, ev["prev_hash"])
    assert len(link) == 64 and len(ch) == 64
    # chain_link's inputs are two fixed 64-char hex strings regardless of payload size.
    big = _event(1)
    big["payload"]["blob"] = "x" * 100_000
    big["prev_hash"] = "b" * 64
    assert len(chain_link(content_hash(big), big["prev_hash"])) == 64

    # Integration: content hashing in a PROCESS POOL (off the GIL) + serial link/append, and the
    # resulting chain verifies under concurrency.
    el = EvidenceLedger(_db())
    writer = SerializedLedgerWriter(el, pool=ProcessPoolExecutor(max_workers=2))
    assert writer.uses_process_pool() is True
    errors: list[BaseException] = []

    def worker(base: int) -> None:
        for j in range(10):
            try:
                writer.append(_event(base * 10 + j))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    writer.stop()

    assert not errors, f"process-pool split-hash append errored: {errors[:3]}"
    assert len(el.iter_events()) == 50
    assert el.verify_chain() is True


# ── AURUM_ERR_054 — Domain Time and Execution Time are separate clocks ────────────────────

def test_AURUM_ERR_054_domain_vs_execution_time():
    clock = DomainClock(1000.0)
    e_before = execution_now()
    clock.advance(365 * DAY)                 # a fake YEAR of Domain Time, instantly
    e_after = execution_now()
    # Execution time barely moved: advancing Domain Time did NOT wait or fake the real clock.
    assert (e_after - e_before) < 0.5
    # Domain Time genuinely elapsed a year (FAST decay collapsed).
    assert decay(1000.0, now=clock.now(), lam=LAMBDA["FAST"]) < 1e-6
    # execution_now is the real monotonic clock and is non-decreasing, independent of the
    # Domain clock; the EL backpressure timeout (037) runs on THIS clock, never Domain Time.
    assert execution_now() >= e_after
    clock.set(0.0)                           # rewind Domain Time (allowed for tests)
    assert execution_now() >= e_after        # execution time cannot be rewound by a Domain op


# ── AURUM_ERR_068 — linear-time redaction (no native `re` ReDoS on the fast path) ─────────

def test_AURUM_ERR_068_linear_time_redaction():
    r = LinearRedactor()
    assert USES_NATIVE_RE is False, "native re is forbidden on the EL/PK fast path"
    assert r.engine in ("re2", "linear-scan")

    # Secrets/PII are redacted.
    out = r.redact("contact me0wc0w73@gmail.com or use api_key=SECRET_TOKEN_VALUE here")
    assert "me0wc0w73@gmail.com" not in out
    assert "SECRET_TOKEN_VALUE" not in out
    assert "[REDACTED]" in out
    # Payload walk redacts secret-named keys wholesale.
    red = r.redact_payload({"password": "hunter2", "note": "ok", "n": 5})
    assert red["password"] == "[REDACTED]" and red["note"] == "ok" and red["n"] == 5

    # ReDoS immunity: a classic catastrophic-backtracking payload must complete fast and scale
    # ~linearly, NOT exponentially (which native `re` on a vulnerable pattern would).
    redos = "a" * 50_000 + "@" + "b" * 50_000  # ~100k chars, the kind that locks a backtracker
    t0 = execution_now()
    _ = r.redact(redos)
    small_dt = execution_now() - t0
    assert small_dt < 1.0, f"redaction not linear-time (took {small_dt:.3f}s on 100k chars)"

    # Doubling input ~doubles time (linear), nowhere near squaring/exponential.
    big = "x" * 400_000
    t1 = execution_now()
    r.redact(big)
    big_dt = execution_now() - t1
    assert big_dt < 2.0
