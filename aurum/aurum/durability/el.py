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
# Author: Daniel Styles <me0wc0w73@gmail.com>
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
-- IDM walks snapshots oldest-first (EL.snapshots); index the order column.
CREATE INDEX IF NOT EXISTS idx_evidence_snapshots_ts ON evidence_snapshots(ts);
-- NOTE (schema design, AURUM_ERR substrate): active_rules_json / active_goals_json here,
-- and participants_json / risk_snapshot_json in CA's ca_conflicts table, are OPAQUE blobs —
-- read-and-parsed in Python (IDM drift, DD divergence) but NEVER used in a SQL WHERE/GROUP BY,
-- so they correctly stay JSON. The only payload field ever SQL-queried is capability_class,
-- already promoted to a STORED generated column + index on evidence_ledger. Promote a JSON
-- field to a generated/indexed column the moment a query starts filtering on it.

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
-- el_seq is queried by RR.replay (decision_for_el_seq) — index the WHERE column.
CREATE INDEX IF NOT EXISTS idx_decisions_el_seq   ON decisions(el_seq);

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

    # -- public read surface (consumers read THROUGH these, never via _db) ----
    # These exist so derived views (RR/IDM/MPD) and MGC never reach into EL's
    # private connection; see organ_dependencies.md. Each returns the seq, so a
    # caller can order/link without a second query.
    _EVENT_COLS = ("seq,event_id,source_organ,action_type,object_ids,payload,"
                   "evidence_confidence,evidence_source,prev_hash,hash,timestamp")

    @staticmethod
    def _event_full(r) -> Dict[str, Any]:
        return {"seq": r[0], "event_id": r[1], "source_organ": r[2], "action_type": r[3],
                "object_ids": json.loads(r[4] or "[]"), "payload": json.loads(r[5] or "{}"),
                "evidence_confidence": r[6], "evidence_source": r[7], "prev_hash": r[8],
                "hash": r[9], "timestamp": r[10]}

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Full ledger row (incl. seq) for an event_id, or None — for RR.replay."""
        row = self._db.execute(
            f"SELECT {self._EVENT_COLS} FROM evidence_ledger WHERE event_id=?",
            (event_id,)).fetchone()
        return self._event_full(row) if row else None

    def iter_events(self, ascending: bool = True) -> List[Dict[str, Any]]:
        """All ledger events (incl. seq), seq-ordered — for derived views (MPD)."""
        order = "ASC" if ascending else "DESC"
        rows = self._db.execute(
            f"SELECT {self._EVENT_COLS} FROM evidence_ledger ORDER BY seq {order}"
        ).fetchall()
        return [self._event_full(r) for r in rows]

    def events_for_object(self, object_id: str) -> List[Dict[str, Any]]:
        """Ledger rows touching object_id, via the object index — for MGC."""
        rows = self._db.execute(
            "SELECT e.seq,e.event_id,e.source_organ,e.action_type,e.object_ids,e.payload,"
            "e.evidence_confidence,e.evidence_source,e.prev_hash,e.hash,e.timestamp "
            "FROM evidence_ledger e JOIN el_object_index oi ON oi.seq = e.seq "
            "WHERE oi.object_id=? ORDER BY e.seq ASC", (object_id,)).fetchall()
        return [self._event_full(r) for r in rows]

    def snapshots(self) -> List[Dict[str, Any]]:
        """Governance-state snapshots oldest-first — for IDM drift."""
        rows = self._db.execute(
            "SELECT snapshot_id,ts,trust,authority,active_rules_json,active_goals_json,"
            "knowledge_state_hash,environment_hash FROM evidence_snapshots "
            "ORDER BY ts ASC, rowid ASC").fetchall()
        return [{"snapshot_id": r[0], "ts": r[1], "trust": r[2], "authority": r[3],
                 "active_rules": json.loads(r[4] or "[]"),
                 "active_goals": json.loads(r[5] or "[]"),
                 "knowledge_state_hash": r[6], "environment_hash": r[7]} for r in rows]

    def decision_for_el_seq(self, seq: int) -> Optional[Dict[str, Any]]:
        """The decision + its evidence snapshot linked to a ledger seq — for RR.replay."""
        row = self._db.execute(
            "SELECT d.decision_id,d.final_decision,d.authority_score,d.reason_json,"
            "s.active_rules_json,s.active_goals_json,s.knowledge_state_hash,"
            "s.environment_hash FROM decisions d "
            "JOIN evidence_snapshots s ON d.evidence_snapshot_id = s.snapshot_id "
            "WHERE d.el_seq=?", (seq,)).fetchone()
        if row is None:
            return None
        return {"decision_id": row[0], "final_decision": row[1], "authority_score": row[2],
                "reason": json.loads(row[3] or "{}"),
                "active_rules": json.loads(row[4] or "[]"),
                "active_goals": json.loads(row[5] or "[]"),
                "knowledge_state_hash": row[6], "environment_hash": row[7]}

    def count_decisions(self, since: Optional[str] = None) -> int:
        if since is None:
            return self._db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        return self._db.execute(
            "SELECT COUNT(*) FROM decisions WHERE ts >= ?", (since,)).fetchone()[0]

    def count_conflicts(self, since: Optional[str] = None) -> int:
        if since is None:
            return self._db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
        return self._db.execute(
            "SELECT COUNT(*) FROM conflicts WHERE ts >= ?", (since,)).fetchone()[0]

    def archive_region(self, object_id: Optional[str] = None,
                       from_seq: Optional[int] = None,
                       to_seq: Optional[int] = None) -> int:
        """Copy a cold region to the archive table (structural, lossless — the live
        ledger is never deleted from). EL OWNS its archive table, so MGC.compress
        delegates here rather than reaching into _db. Returns rows archived."""
        if object_id is not None:
            rows = self._db.execute(
                "SELECT e.seq, e.event_id, e.payload FROM evidence_ledger e "
                "JOIN el_object_index oi ON oi.seq = e.seq WHERE oi.object_id=?",
                (object_id,)).fetchall()
        else:
            rows = self._db.execute(
                "SELECT seq, event_id, payload FROM evidence_ledger WHERE seq BETWEEN ? AND ?",
                (from_seq or 0, to_seq or 0)).fetchall()
        n = 0
        for seq, event_id, payload in rows:
            self._db.execute(
                "INSERT OR IGNORE INTO evidence_ledger_archive(seq, blob) VALUES (?,?)",
                (seq, json.dumps({"event_id": event_id, "payload": payload})))
            n += 1
        return n

    def _row_to_event(self, r) -> ELEvent:
        # seq,event_id,timestamp,source_organ,action_type,object_ids,payload,
        # evidence_confidence,evidence_source,prev_hash,hash,capability_class
        return {
            "event_id": r[1], "timestamp": r[2], "source_organ": r[3],
            "action_type": r[4], "object_ids": json.loads(r[5]),
            "payload": json.loads(r[6]), "evidence_confidence": r[7],
            "evidence_source": r[8], "prev_hash": r[9], "hash": r[10],
        }
