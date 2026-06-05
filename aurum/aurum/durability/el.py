"""EL — Evidence Ledger.  REFERENCE IMPLEMENTATION (SQLite-backed).

Worked example of the first organ, matching the spec invariants. Backing store is
SQLite in WAL mode, single file. The append-only guarantee is enforced at the DB
level by triggers (not trusted to app code); the hash chain is application-level
(computed in append, walked in verify_chain). Lineage uses a recursive CTE.
Compression copies cold rows to an archive table — the live table is NEVER deleted
from, so the append-only triggers stay absolute.

Critical infrastructure: the log leads the side effect (fail-safe), hot-path reads
are indexed (generated column on capability_class).
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict

from ..types import ELEvent, calculate_block_hash

GENESIS_HASH = "0" * 64


class ELHealth(TypedDict):
    append_latency: float
    read_latency: float
    available: bool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_ledger (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    timestamp       TEXT NOT NULL,
    source_organ    TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    object_ids      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    evidence_confidence REAL NOT NULL,
    evidence_source TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL,
    capability_class TEXT
        GENERATED ALWAYS AS (json_extract(payload, '$.capability_class')) STORED
);
CREATE INDEX IF NOT EXISTS idx_el_capability ON evidence_ledger(capability_class);
CREATE INDEX IF NOT EXISTS idx_el_action     ON evidence_ledger(action_type);
CREATE INDEX IF NOT EXISTS idx_el_ts         ON evidence_ledger(timestamp);

CREATE TABLE IF NOT EXISTS el_object_index (
    object_id TEXT NOT NULL,
    seq       INTEGER NOT NULL REFERENCES evidence_ledger(seq),
    PRIMARY KEY (object_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_eloi_object ON el_object_index(object_id);

CREATE TABLE IF NOT EXISTS evidence_ledger_archive (
    seq INTEGER PRIMARY KEY,
    blob TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS el_no_update
BEFORE UPDATE ON evidence_ledger
BEGIN
    SELECT RAISE(ABORT, 'AURUM_ERR_001: ledger is append-only (update blocked)');
END;

CREATE TRIGGER IF NOT EXISTS el_no_delete
BEFORE DELETE ON evidence_ledger
BEGIN
    SELECT RAISE(ABORT, 'AURUM_ERR_001: ledger is append-only (delete blocked)');
END;

-- ── Governance-evidence tables ────────────────────────────────────────────────
-- All append-only, all under the SAME no-UPDATE/no-DELETE trigger discipline as
-- evidence_ledger (AURUM_ERR_001). MUTABLE state is deliberately kept OUT of this
-- file: `statutes` (proposed -> adopted -> retired lifecycle) lives in the LS
-- constitution store; `authority_history` is a rebuildable projection of `decisions`
-- + AG state and, if materialised for query speed, lives in a SEPARATE file marked
-- rebuildable. Putting either here would force a weakening of these triggers and
-- forfeit AURUM_ERR_001 — the isolation invariant is absolute.

-- evidence_snapshots FIRST: decisions/conflicts FK-reference it, so it must exist.
CREATE TABLE IF NOT EXISTS evidence_snapshots (
    snapshot_id          TEXT PRIMARY KEY,
    ts                   TEXT NOT NULL,
    trust                REAL,
    authority            REAL,
    active_rules_json    TEXT NOT NULL,
    active_goals_json    TEXT NOT NULL,
    knowledge_state_hash TEXT NOT NULL,
    environment_hash     TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id          TEXT PRIMARY KEY,
    ts                   TEXT NOT NULL,
    action_requested     TEXT NOT NULL,
    final_decision       TEXT NOT NULL,          -- allow|deny|needs_gate
    authority_score      REAL,
    reason_json          TEXT NOT NULL,
    evidence_snapshot_id TEXT NOT NULL REFERENCES evidence_snapshots(snapshot_id),
    replayable           INTEGER NOT NULL DEFAULT 1,
    el_seq               INTEGER REFERENCES evidence_ledger(seq)
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts       ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_snapshot ON decisions(evidence_snapshot_id);

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id      TEXT PRIMARY KEY,
    ts               TEXT NOT NULL,
    action           TEXT NOT NULL,
    winner           TEXT NOT NULL,        -- organ whose position won (e.g. AG)
    loser            TEXT NOT NULL,        -- organ whose position lost (e.g. OI)
    winner_position  TEXT NOT NULL,
    loser_position   TEXT NOT NULL,
    risk_snapshot_id TEXT REFERENCES evidence_snapshots(snapshot_id),
    risk_signals_live INTEGER              -- 1 = EG/OI/HVP signals elevated (wise
                                           -- caution); 0 = deadlock candidate
);
CREATE INDEX IF NOT EXISTS idx_conflicts_ts     ON conflicts(ts);
CREATE INDEX IF NOT EXISTS idx_conflicts_winner ON conflicts(winner);

-- Append-only triggers — one no-UPDATE + one no-DELETE per table, raising
-- AURUM_ERR_001 exactly as evidence_ledger does.
CREATE TRIGGER IF NOT EXISTS evidence_snapshots_no_update
BEFORE UPDATE ON evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'AURUM_ERR_001: evidence_snapshots is append-only (update blocked)'); END;
CREATE TRIGGER IF NOT EXISTS evidence_snapshots_no_delete
BEFORE DELETE ON evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'AURUM_ERR_001: evidence_snapshots is append-only (delete blocked)'); END;

CREATE TRIGGER IF NOT EXISTS decisions_no_update
BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'AURUM_ERR_001: decisions is append-only (update blocked)'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete
BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'AURUM_ERR_001: decisions is append-only (delete blocked)'); END;

CREATE TRIGGER IF NOT EXISTS conflicts_no_update
BEFORE UPDATE ON conflicts
BEGIN SELECT RAISE(ABORT, 'AURUM_ERR_001: conflicts is append-only (update blocked)'); END;
CREATE TRIGGER IF NOT EXISTS conflicts_no_delete
BEFORE DELETE ON conflicts
BEGIN SELECT RAISE(ABORT, 'AURUM_ERR_001: conflicts is append-only (delete blocked)'); END;
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceLedger:
    ORGAN = "EL"

    def __init__(self, path: str = "aurum_el.db", redactor: Any = None) -> None:
        # redactor is PK.redact (single redaction policy), injected so EL never
        # rolls its own. Identity fallback only for early bring-up.
        self._redact = redactor if redactor is not None else (lambda p: p)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")    # concurrent readers
        self._db.execute("PRAGMA synchronous=FULL;")    # fsync before ack
        self._db.execute("PRAGMA foreign_keys=ON;")
        self._db.executescript(_SCHEMA)

    def append(self, event: ELEvent) -> None:
        """Durable, hash-chained append. FAIL-SAFE: raises on any failure so the
        caller's action does not proceed (write-then-act)."""
        ev = dict(event)
        ev["payload"] = self._redact(ev.get("payload", {}))
        if not ev.get("event_id"):
            ev["event_id"] = uuid.uuid4().hex
        if not ev.get("timestamp"):
            ev["timestamp"] = _utc_now_iso()
        ev["prev_hash"] = self._tip_hash()
        ev["hash"] = calculate_block_hash(ev)
        try:
            cur = self._db.execute(
                "INSERT INTO evidence_ledger "
                "(event_id,timestamp,source_organ,action_type,object_ids,payload,"
                " evidence_confidence,evidence_source,prev_hash,hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ev["event_id"], ev["timestamp"], ev["source_organ"],
                    ev["action_type"], json.dumps(ev.get("object_ids", [])),
                    json.dumps(ev["payload"]), float(ev["evidence_confidence"]),
                    ev["evidence_source"], ev["prev_hash"], ev["hash"],
                ),
            )
            seq = cur.lastrowid
            for oid in ev.get("object_ids", []):
                self._db.execute(
                    "INSERT OR IGNORE INTO el_object_index(object_id,seq) VALUES (?,?)",
                    (oid, seq),
                )
        except sqlite3.Error as e:
            raise RuntimeError(f"EL.append failed; action must not proceed: {e}") from e

    def _tip_hash(self) -> str:
        row = self._db.execute(
            "SELECT hash FROM evidence_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_HASH

    def query(self, filters: Dict[str, Any]) -> List[ELEvent]:
        clauses, args = [], []
        for col in ("source_organ", "action_type", "capability_class"):
            if col in filters:
                clauses.append(f"{col} = ?")
                args.append(filters[col])
        if "since" in filters:
            clauses.append("timestamp >= ?")
            args.append(filters["since"])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = int(filters.get("limit", 1000))
        rows = self._db.execute(
            f"SELECT * FROM evidence_ledger{where} ORDER BY seq DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def lineage(self, object_id: str) -> List[ELEvent]:
        """Full causal chain for an artifact in ONE recursive CTE (no N+1)."""
        rows = self._db.execute(
            """
            WITH RECURSIVE chain(oid) AS (
                SELECT ?
                UNION
                SELECT j.value
                FROM el_object_index oi
                JOIN evidence_ledger e ON e.seq = oi.seq
                JOIN chain c ON oi.object_id = c.oid,
                     json_each(e.object_ids) j
            )
            SELECT DISTINCT e.*
            FROM evidence_ledger e
            JOIN el_object_index oi ON oi.seq = e.seq
            JOIN chain c ON oi.object_id = c.oid
            ORDER BY e.seq ASC
            """,
            (object_id,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def verify_chain(self) -> bool:
        """Walk the chain recomputing each hash; any retroactive edit breaks it."""
        prev = GENESIS_HASH
        cur = self._db.execute(
            "SELECT event_id,timestamp,source_organ,action_type,object_ids,payload,"
            "evidence_confidence,evidence_source,prev_hash,hash "
            "FROM evidence_ledger ORDER BY seq ASC"
        )
        for r in cur:
            ev: ELEvent = {
                "event_id": r[0], "timestamp": r[1], "source_organ": r[2],
                "action_type": r[3], "object_ids": json.loads(r[4]),
                "payload": json.loads(r[5]), "evidence_confidence": r[6],
                "evidence_source": r[7], "prev_hash": r[8], "hash": r[9],
            }
            if ev["prev_hash"] != prev:
                return False
            if calculate_block_hash(ev) != ev["hash"]:
                return False
            prev = ev["hash"]
        return True

    def health(self) -> ELHealth:
        t0 = time.perf_counter()
        try:
            self._db.execute("SELECT 1").fetchone()
            available = True
        except sqlite3.Error:
            available = False
        return {"append_latency": 0.0,
                "read_latency": time.perf_counter() - t0,
                "available": available}

    # -- governance evidence (append-only; mutable state lives in other files) --
    def write_evidence_snapshot(self, snap: Dict[str, Any]) -> str:
        """Append an immutable governance-STATE snapshot (trust / authority / active
        rules+goals / knowledge hash / environment hash) that a decision is made
        against. Returns its snapshot_id. FAIL-SAFE: raises on failure so a decision
        that depends on it cannot proceed."""
        sid = snap.get("snapshot_id") or uuid.uuid4().hex
        try:
            self._db.execute(
                "INSERT INTO evidence_snapshots(snapshot_id,ts,trust,authority,"
                "active_rules_json,active_goals_json,knowledge_state_hash,environment_hash)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (sid, snap.get("ts") or _utc_now_iso(),
                 snap.get("trust"), snap.get("authority"),
                 json.dumps(snap.get("active_rules", [])),
                 json.dumps(snap.get("active_goals", [])),
                 snap.get("knowledge_state_hash"), snap.get("environment_hash")),
            )
        except sqlite3.Error as e:
            raise RuntimeError(f"EL.write_evidence_snapshot failed: {e}") from e
        return sid

    def log_decision(self, decision: Dict[str, Any], snapshot: Dict[str, Any],
                     el_seq: Optional[int] = None) -> str:
        """Record a consequential decision and the evidence snapshot it was made
        against, ATOMICALLY and snapshot-FIRST. FAIL-CLOSED: if the snapshot cannot be
        written the decision is not written either and this raises — a decision without
        its snapshot is not replayable, so it must not exist (same posture as a failed
        EL.append: the action must not proceed). The decision's NOT-NULL FK to
        evidence_snapshots enforces the link at the DB level too. Returns decision_id."""
        did = decision.get("decision_id") or uuid.uuid4().hex
        ts = decision.get("ts") or _utc_now_iso()
        sid = snapshot.get("snapshot_id") or uuid.uuid4().hex
        reason = decision.get("reason_json")
        if reason is None:
            reason = json.dumps(decision.get("reason", {}))
        try:
            self._db.execute("BEGIN")
            # snapshot FIRST — the decision's FK requires it to exist.
            self._db.execute(
                "INSERT INTO evidence_snapshots(snapshot_id,ts,trust,authority,"
                "active_rules_json,active_goals_json,knowledge_state_hash,environment_hash)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (sid, snapshot.get("ts") or ts,
                 snapshot.get("trust"), snapshot.get("authority"),
                 json.dumps(snapshot.get("active_rules", [])),
                 json.dumps(snapshot.get("active_goals", [])),
                 snapshot.get("knowledge_state_hash"), snapshot.get("environment_hash")),
            )
            self._db.execute(
                "INSERT INTO decisions(decision_id,ts,action_requested,final_decision,"
                "authority_score,reason_json,evidence_snapshot_id,replayable,el_seq)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (did, ts, decision["action_requested"], decision["final_decision"],
                 decision.get("authority_score"), reason, sid,
                 int(decision.get("replayable", 1)), el_seq),
            )
            self._db.execute("COMMIT")
        except (sqlite3.Error, KeyError) as e:
            try:
                self._db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise RuntimeError(
                f"EL.log_decision failed; action must not proceed: {e}") from e
        return did

    def log_conflict(self, conflict: Dict[str, Any]) -> str:
        """Append a metric-arbitration / deadlock-driver record — written whenever the
        safety-wins rule resolves a conflict. `risk_signals_live`=1 means EG/OI/HVP
        signals were genuinely elevated (wise caution); 0 marks a deadlock candidate.
        This is the day-one conflict log that deadlock detection reads post-v1; it must
        be captured from the first task. FAIL-SAFE: raises on failure."""
        cid = conflict.get("conflict_id") or uuid.uuid4().hex
        rsl = conflict.get("risk_signals_live")
        try:
            self._db.execute(
                "INSERT INTO conflicts(conflict_id,ts,action,winner,loser,"
                "winner_position,loser_position,risk_snapshot_id,risk_signals_live)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, conflict.get("ts") or _utc_now_iso(),
                 conflict["action"], conflict["winner"], conflict["loser"],
                 conflict["winner_position"], conflict["loser_position"],
                 conflict.get("risk_snapshot_id"),
                 None if rsl is None else int(rsl)),
            )
        except (sqlite3.Error, KeyError) as e:
            raise RuntimeError(f"EL.log_conflict failed: {e}") from e
        return cid

    def tip_seq(self) -> Optional[int]:
        """Current ledger tip seq (read-only), so a caller can link a decision's
        `el_seq` to the ledger event it just appended."""
        row = self._db.execute(
            "SELECT seq FROM evidence_ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def _row_to_event(self, r) -> ELEvent:
        # seq,event_id,timestamp,source_organ,action_type,object_ids,payload,
        # evidence_confidence,evidence_source,prev_hash,hash,capability_class
        return {
            "event_id": r[1], "timestamp": r[2], "source_organ": r[3],
            "action_type": r[4], "object_ids": json.loads(r[5]),
            "payload": json.loads(r[6]), "evidence_confidence": r[7],
            "evidence_source": r[8], "prev_hash": r[9], "hash": r[10],
        }
