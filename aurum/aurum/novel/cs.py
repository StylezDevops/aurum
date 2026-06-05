"""CS — Causal Simulator.  REFERENCE IMPLEMENTATION (separate SQLite store).

Deliberately a DIFFERENT database file from EL. EL is append-only/immutable
(trigger-enforced); CS is mutable graph + lease state. Mixing them would force
EL's triggers to allow updates, killing AURUM_ERR_001. So: one immutable ledger
(EL), one mutable graph store (CS). CS WRITES events to EL (one-directional);
EL never writes to CS.

v1 ships the deterministic core: dependency graph (nodes/edges), whatif
blast-radius via recursive CTE, and TTL + HEARTBEAT leases (crash-safe ghost-
dependency guard). CS.project deep risk-projection is DEFERRED.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

from ..base import unbuilt

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cs_nodes (
    node_id TEXT PRIMARY KEY,
    type    TEXT NOT NULL,                 -- skill|tool|policy_rule|active_goal
    shared_assumptions TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS cs_edges (
    src TEXT NOT NULL REFERENCES cs_nodes(node_id),
    dst TEXT NOT NULL REFERENCES cs_nodes(node_id),
    PRIMARY KEY (src, dst)
);
CREATE INDEX IF NOT EXISTS idx_cs_edges_dst ON cs_edges(dst);

-- Leases: TTL + heartbeat. A lease is ACTIVE iff (last_heartbeat + ttl) > now.
-- A crashed holder stops heart-beating, the lease lapses, MGC reclaims the node.
CREATE TABLE IF NOT EXISTS cs_leases (
    node_id        TEXT PRIMARY KEY REFERENCES cs_nodes(node_id),
    locked_by      TEXT NOT NULL,
    ttl            REAL NOT NULL,
    last_heartbeat REAL NOT NULL
);
"""


class CausalSimulator:
    ORGAN = "CS"

    def __init__(self, path: str = "aurum_cs.db", el: Any = None) -> None:
        # el is the EvidenceLedger; CS writes (never reads) audit events to it.
        self._el = el
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.execute("PRAGMA foreign_keys=ON;")
        self._db.executescript(_SCHEMA)

    # -- graph --------------------------------------------------------------
    def graph(self) -> Dict[str, Any]:
        nodes = self._db.execute("SELECT node_id,type FROM cs_nodes").fetchall()
        edges = self._db.execute("SELECT src,dst FROM cs_edges").fetchall()
        return {"nodes": [{"node_id": n[0], "type": n[1]} for n in nodes],
                "edges": [{"src": e[0], "dst": e[1]} for e in edges]}

    def add_node(self, node_id: str, type: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO cs_nodes(node_id,type) VALUES (?,?)", (node_id, type)
        )

    def add_edge(self, src: str, dst: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO cs_edges(src,dst) VALUES (?,?)", (src, dst)
        )

    # -- whatif (blast radius via recursive CTE) ----------------------------
    def whatif(self, change: Dict[str, Any]) -> Dict[str, Any]:
        """change = {'op':'remove'|'add', 'node_id':..}. Returns the transitive
        set of nodes that reference the target (affected) plus leaf orphans."""
        target = change["node_id"]
        affected = [
            r[0] for r in self._db.execute(
                """
                WITH RECURSIVE up(n) AS (
                    SELECT ?
                    UNION
                    SELECT e.src FROM cs_edges e JOIN up ON e.dst = up.n
                )
                SELECT n FROM up WHERE n <> ?
                """,
                (target, target),
            ).fetchall()
        ]
        # orphaned: nodes that, with target removed, have no remaining referrer
        orphaned = [
            r[0] for r in self._db.execute(
                """
                SELECT n.node_id FROM cs_nodes n
                WHERE n.node_id <> ?
                  AND NOT EXISTS (
                    SELECT 1 FROM cs_edges e
                    WHERE e.dst = n.node_id AND e.src <> ?
                  )
                  AND EXISTS (SELECT 1 FROM cs_edges e2 WHERE e2.dst = n.node_id)
                """,
                (target, target),
            ).fetchall()
        ]
        return {"affected": affected, "conflicts": [], "orphaned": orphaned}

    def project(self, plan: Any) -> Any:  # DEFERRED/EXPERIMENTAL
        raise unbuilt(self.ORGAN, "project")

    # -- leases (TTL + heartbeat, crash-safe) -------------------------------
    def lease(self, object_id: str, ttl: float, holder: str = "planner") -> None:
        now = time.time()
        self._db.execute(
            "INSERT INTO cs_leases(node_id,locked_by,ttl,last_heartbeat) VALUES (?,?,?,?) "
            "ON CONFLICT(node_id) DO UPDATE SET locked_by=excluded.locked_by, "
            "ttl=excluded.ttl, last_heartbeat=excluded.last_heartbeat",
            (object_id, holder, ttl, now),
        )

    def heartbeat(self, object_id: str) -> None:
        """Live holder renews. No renewal within ttl => lease lapses (crash-safe)."""
        self._db.execute(
            "UPDATE cs_leases SET last_heartbeat=? WHERE node_id=?",
            (time.time(), object_id),
        )

    def release(self, object_id: str) -> None:
        self._db.execute("DELETE FROM cs_leases WHERE node_id=?", (object_id,))

    def is_leased(self, object_id: str) -> bool:
        row = self._db.execute(
            "SELECT ttl,last_heartbeat FROM cs_leases WHERE node_id=?", (object_id,)
        ).fetchone()
        if not row:
            return False
        ttl, last_hb = row
        return (last_hb + ttl) > time.time()
