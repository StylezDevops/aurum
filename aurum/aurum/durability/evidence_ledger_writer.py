"""Single-writer serialization for the EL hash chain (AURUM_ERR_031 / 037 / 053).

EL is hash-chained: every event carries prev_hash = the prior event's hash. Aurum has MANY
concurrent writers (the synchronous decision path PLUS background organs: MGC sweeps, MAA
audits, CPD, EG branches, AA discovery). If two appends read the same chain tip in the same
instant, they both build on the same prev_hash -> the chain FORKS -> verify_chain() fails
catastrophically. This is a latent data-corruption bug in the foundation; it must be fixed
before anything is built on EL.

THREE properties, all required:

  - SINGLE-WRITER SERIALIZATION (031): every append (sync or async, any thread) enqueues to ONE
    background writer that assigns prev_hash and commits sequentially. With exactly one writer
    there is no tip contention, so no fork is possible by construction.

  - BOUNDED BACKPRESSURE, FAIL-CLOSED (037): an unbounded queue is a DoS vector (a runaway
    producer exhausts host memory). The queue is BOUNDED and append() carries a short timeout.
    The subtlety: backpressure must fail the ACTION, not drop the LOG. On saturation we cannot
    silently discard the event (breaks "no action without an audit trail", AURUM_ERR_011) and
    cannot block forever (the DoS). So timeout -> raise LedgerBackpressure -> the action that
    wanted to log is blocked. The timeout uses EXECUTION time (real monotonic, via queue.put),
    never Domain Time (AURUM_ERR_054).

  - GIL-SAFE SPLIT HASH (053): json.dumps + sha256 are CPU-bound; under the GIL one thread maxes
    one core and backs the queue into LedgerBackpressure. So the EXPENSIVE content hash (depends
    only on the event) runs in a PROCESS POOL off the writer thread; the writer does only the
    CHEAP O(1) chain-link (prev_hash + content hash) and the SQLite append. Expensive work
    parallel, serial step constant-time.

max_queue / append_timeout are CONSTITUTIONAL (human-gated) — a runaway organ cannot widen its
own backpressure limit. Sustained backpressure is itself a CB.trip candidate (the velocity
guard): something is dumping logs abnormally; freeze that class.

Multi-PROCESS note: if a future deployment runs organs in separate processes, the in-process
queue is insufficient — use an atomic compare-and-swap on the tip inside a SQLite BEGIN
IMMEDIATE transaction (insert only if the current tip still equals the prev_hash you read; on
conflict re-read and retry). For the current single-process design the queue is simpler and
sufficient; the CAS path is documented so the migration is known.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import queue
import threading
import uuid
from concurrent.futures import Executor, ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from ..types import chain_link, content_hash
from .clock import execution_now  # noqa: F401  (documents the execution-time boundary)


class LedgerBackpressure(RuntimeError):
    """EL write queue saturated. Raised to the caller so the ACTION fails closed — the log is
    never silently dropped and the writer never blocks forever. Same posture as EL.append fail."""


class _Future:
    """Minimal future carrying a value OR an exception, so a worker fault propagates to the
    caller (the action then fails closed rather than proceeding as if logged)."""

    def __init__(self) -> None:
        self._e = threading.Event()
        self._v: Any = None
        self._exc: Optional[BaseException] = None

    def set(self, v: Any) -> None:
        self._v = v
        self._e.set()

    def set_exception(self, exc: BaseException) -> None:
        self._exc = exc
        self._e.set()

    def get(self) -> Any:
        self._e.wait()
        if self._exc is not None:
            raise self._exc
        return self._v


_STOP = object()


class SerializedLedgerWriter:
    """Wraps an EvidenceLedger. Every caller (sync or async) goes through the bounded queue;
    one worker thread assigns prev_hash and writes in order. No two writers ever read the same
    tip. The expensive content hash is computed in `pool` (a ProcessPoolExecutor by default, to
    escape the GIL); the worker does only the O(1) chain-link + append_raw."""

    def __init__(
        self,
        el: Any,
        *,
        max_queue: int = 1000,         # CONSTITUTIONAL (human-gated)
        append_timeout: float = 2.0,   # CONSTITUTIONAL — EXECUTION-time seconds
        pool: Optional[Executor] = None,
        mac: Any = None,               # optional ForwardSecureMAC over content hashes
        start_paused: bool = False,    # test hook for deterministic backpressure
    ) -> None:
        self._el = el
        self._q: "queue.Queue[Any]" = queue.Queue(maxsize=max_queue)
        self._append_timeout = append_timeout
        self._owns_pool = pool is None
        # Lazy default so merely constructing a writer does not spawn processes; created on
        # first use as a ProcessPoolExecutor (GIL escape) unless one was injected.
        self._pool = pool
        self._mac = mac
        self._gate = threading.Event()
        if not start_paused:
            self._gate.set()
        self._stopped = False
        self._worker = threading.Thread(target=self._run, name="el-writer", daemon=True)
        self._worker.start()

    # -- pool management ---------------------------------------------------

    def _ensure_pool(self) -> Executor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor()
        return self._pool

    def uses_process_pool(self) -> bool:
        return isinstance(self._pool, ProcessPoolExecutor)

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        while True:
            # Pause point BEFORE dequeue: a paused worker pulls nothing, so the queue fills
            # deterministically (used by the backpressure test); negligible when running.
            self._gate.wait()
            item = self._q.get()
            if item is _STOP:
                self._q.task_done()
                return
            event, content_future, fut = item
            try:
                content_h = content_future.result()
                # SINGLE writer: read tip, O(1) chain-link, append — no contention possible.
                tip = self._el.tip_hash()
                event["prev_hash"] = tip
                event["hash"] = chain_link(content_h, tip)
                self._el.append_raw(event)
                if self._mac is not None:
                    self._mac.sign(content_h.encode("utf-8"))
                fut.set(event["hash"])
            except BaseException as exc:  # noqa: BLE001 - propagate to caller, fail closed
                fut.set_exception(
                    RuntimeError(f"EL writer failed; action must not proceed: {exc}")
                )
            finally:
                self._q.task_done()

    # -- public append -----------------------------------------------------

    def append(self, event: dict) -> str:
        """Thread-safe. Blocks until the event is durably chained, then returns its hash.

        Backpressure: if the bounded queue is saturated, raises LedgerBackpressure after a
        short EXECUTION-time timeout — the action fails closed, the log is NEVER dropped and the
        writer NEVER blocks forever. Async callers wrap this in a thread executor; they never
        write the chain directly.
        """
        ev = dict(event)
        # Single redaction policy (PK-owned), applied on write before anything is persisted.
        ev["payload"] = self._el.redact(ev.get("payload", {}))
        if not ev.get("event_id"):
            ev["event_id"] = uuid.uuid4().hex
        if not ev.get("timestamp"):
            # Wall-clock metadata on the event, not decay LOGIC — so a plain UTC timestamp is
            # correct here; this is not the injectable Domain Time clock.
            ev["timestamp"] = datetime.now(timezone.utc).isoformat()
        # Expensive content hash OFF the writer thread (GIL escape, 053). content_hash excludes
        # prev_hash/hash, so it does not depend on the chain and is safe to parallelize.
        content_future = self._ensure_pool().submit(content_hash, ev)
        fut = _Future()
        try:
            self._q.put((ev, content_future, fut), timeout=self._append_timeout)
        except queue.Full:
            raise LedgerBackpressure(
                "EL write queue saturated; action blocked, fail closed (log not dropped)"
            )
        return fut.get()

    # -- lifecycle / test hooks -------------------------------------------

    def pause(self) -> None:
        """Pause the worker (it stops draining the queue). Test hook for backpressure."""
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._gate.set()          # ensure the worker can reach the stop sentinel
        self._q.put(_STOP)
        self._worker.join(timeout=10.0)
        if self._owns_pool and self._pool is not None:
            self._pool.shutdown(wait=True)

    def __enter__(self) -> "SerializedLedgerWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()
