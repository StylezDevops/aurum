"""EL chain serialization under concurrency (AURUM_ERR_031), on the DIRECT append path.

Before this fix, EvidenceLedger.append read the chain tip with no serialization, so concurrent
appends read the SAME tip and forked the chain: an 8-thread x 250-append run produced ~740
distinct prev_hash values across ~1870 rows, LOST ~5% of events to constraint races, and
verify_chain() failed — which on the next --rm boot makes the kernel rehydrate nothing and
silently reset all earned authority/familiarity/TL to baseline (a self-concealing failure).
SerializedLedgerWriter existed but was never wired into the kernel, and its in-process queue
could not serialize CROSS-PROCESS writers anyway (the host maintenance loop and a per-message
cage kernel share the mounted el.db). The fix serializes inside append itself: a per-instance
lock (threads) + a BEGIN IMMEDIATE transaction on a dedicated chain connection (processes).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import threading

from aurum.durability.el import EvidenceLedger


def _ev(n: int, i: int) -> dict:
    return {"event_id": "", "timestamp": "", "source_organ": "T", "action_type": "VOTE",
            "object_ids": [f"obj-{n}"], "payload": {"n": n, "i": i}, "evidence_confidence": 1.0,
            "evidence_source": "t", "prev_hash": "", "hash": ""}


def _hammer(el: EvidenceLedger, n: int, count: int, errs: list) -> None:
    for i in range(count):
        try:
            el.append(_ev(n, i))
        except Exception as e:  # noqa: BLE001 - the test asserts NO append may fail
            errs.append(e)


def test_threaded_appends_do_not_fork_or_lose(tmp_path):
    el = EvidenceLedger(str(tmp_path / "el.db"))
    errs: list = []
    threads = [threading.Thread(target=_hammer, args=(el, n, 75, errs)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errs == []                                   # zero lost events (write-then-act holds)
    rows, distinct = el._db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT prev_hash) FROM evidence_ledger").fetchone()
    assert rows == 600 and distinct == 600              # every append chained a UNIQUE tip — no fork
    assert el.verify_chain() is True


def test_cross_connection_appends_do_not_fork(tmp_path):
    # Two EvidenceLedger instances on the SAME file = the cross-PROCESS deployment shape (the
    # host maintenance loop + a per-message cage kernel share the mounted el.db). The in-process
    # lock cannot help here; the BEGIN IMMEDIATE transaction's write lock is what serializes.
    path = str(tmp_path / "el.db")
    a, b = EvidenceLedger(path), EvidenceLedger(path)
    errs: list = []
    threads = [threading.Thread(target=_hammer, args=(el, n, 50, errs))
               for n, el in enumerate((a, b, a, b))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errs == []
    fresh = EvidenceLedger(path)                        # independent verifier connection
    rows, distinct = fresh._db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT prev_hash) FROM evidence_ledger").fetchone()
    assert rows == 200 and distinct == 200
    assert fresh.verify_chain() is True
