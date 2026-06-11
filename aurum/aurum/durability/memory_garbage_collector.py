"""MGC — Memory Garbage Collector. Without it the system self-poisons.

Scheduled + conservative. Archives obsolete skills, merges duplicate BB lessons, flags
unused tools for retirement (HUMAN_GATE via TS/TCM), and compresses EL regions. Two hard
invariants:
  • LEASE-AWARE: any artifact with an active CS lease is skipped from the sweep entirely —
    even if it looks orphaned (AURUM_ERR_003, the ghost-dependency guard). MGC also never
    archives an artifact CS still shows as referenced.
  • ARCHIVE != DELETE: everything is recoverable via restore(); EL compression is LOSSLESS
    STRUCTURAL only (snapshot + raw deltas copied to the archive table; the live ledger is
    never deleted from), never a semantic summary — so lineage delta-count is unchanged
    (AURUM_ERR_002).
Every action is logged to EL.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mgc_archive (
    artifact_id TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    content     TEXT,
    archived_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mgc_merges (
    merged_id TEXT PRIMARY KEY,
    sources_json TEXT NOT NULL,
    merged_at REAL NOT NULL
);
"""


class MemoryGarbageCollector:
    ORGAN = "MGC"

    def __init__(self, path: str = "aurum_mgc.db", el: Any = None, cs: Any = None,
                 tcm: Any = None) -> None:
        self._el, self._cs, self._tcm = el, cs, tcm
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- scan (lease-aware, reference-aware) -------------------------------
    def scan(self, inventory: Optional[Dict[str, Any]] = None,
             do_not_retire: Optional[Any] = None) -> Dict[str, Any]:
        """-> {archivable, mergeable, retirable}. Skips CS-leased and CS-referenced artifacts AND
        anything in `do_not_retire` — the CC systemic-risk set (the kernel passes
        concentration_risks()): an artifact servicing a disproportionate share of activity is a
        single point of concentration that must NOT be swept, even if TCM/lease think it idle. This
        wires the do-not-retire guard the module docstring promises (previously absent: scan only
        checked leases, so CC's signal never actually protected anything)."""
        inv = inventory or {}
        protected = set(do_not_retire or [])
        archivable = [s for s in inv.get("skills", [])
                      if not self._leased(s) and not self._referenced(s) and s not in protected]
        # duplicate lessons (same signature) -> one mergeable group per signature
        groups: Dict[str, List[str]] = {}
        for lesson in inv.get("lessons", []):
            groups.setdefault(lesson["signature"], []).append(lesson["id"])
        mergeable = [ids for ids in groups.values() if len(ids) > 1]
        # retirable tools come from TCM; never sweep a leased one or a do-not-retire (CC) one
        tools = (self._tcm.retirement_candidates() if self._tcm is not None
                 else inv.get("tools", []))
        retirable = [t for t in tools if not self._leased(t) and t not in protected]
        return {"archivable": archivable, "mergeable": mergeable, "retirable": retirable}

    def _leased(self, artifact_id: str) -> bool:
        return bool(self._cs is not None and self._cs.is_leased(artifact_id))

    def _referenced(self, artifact_id: str) -> bool:
        if self._cs is None:
            return False
        affected = self._cs.whatif({"op": "remove", "node_id": artifact_id})["affected"]
        return len(affected) > 0  # still referenced -> not truly orphaned

    # -- archive != delete --------------------------------------------------
    def archive(self, id: str, content: Any = None, kind: str = "artifact") -> None:
        self._db.execute(
            "INSERT INTO mgc_archive(artifact_id,kind,content,archived_at) "
            "VALUES (?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET "
            "content=excluded.content, archived_at=excluded.archived_at",
            (id, kind, json.dumps(content), time.time()))
        self._audit("ARCHIVE", id, "archive")

    def restore(self, id: str) -> Any:
        row = self._db.execute(
            "SELECT content FROM mgc_archive WHERE artifact_id=?", (id,)).fetchone()
        if row is None:
            raise KeyError(f"MGC: nothing archived under {id!r}")
        self._db.execute("DELETE FROM mgc_archive WHERE artifact_id=?", (id,))
        return json.loads(row[0])

    def merge(self, ids: List[str]) -> str:
        """Merge duplicate lessons into one, preserving both source ids in lineage."""
        merged_id = uuid.uuid4().hex
        self._db.execute(
            "INSERT INTO mgc_merges(merged_id,sources_json,merged_at) VALUES (?,?,?)",
            (merged_id, json.dumps(list(ids)), time.time()))
        self._audit("ARCHIVE", merged_id, "merge")
        return merged_id

    def lineage(self, merged_id: str) -> List[str]:
        row = self._db.execute(
            "SELECT sources_json FROM mgc_merges WHERE merged_id=?", (merged_id,)).fetchone()
        return json.loads(row[0]) if row else []

    # -- EL compression: lossless structural only --------------------------
    def compress(self, el_region: Dict[str, Any]) -> int:
        """Copy a cold EL region to the archive table (snapshot + raw deltas). The LIVE
        ledger is never deleted from, so verify_chain and lineage delta-counts are
        unchanged. Returns the number of rows archived."""
        if self._el is None:
            raise RuntimeError("MGC.compress needs an EvidenceLedger")
        object_id = el_region.get("object_id")
        # EL owns its archive table — delegate the structural copy through its public
        # surface rather than reaching into EL._db.
        n = self._el.archive_region(object_id=object_id,
                                    from_seq=el_region.get("from_seq"),
                                    to_seq=el_region.get("to_seq"))
        # NB: the audit's object_id is namespaced so this meta-event is NOT counted as a
        # delta of the compressed object — compression must not change its lineage.
        self._audit("ARCHIVE", f"mgc:compress:{object_id}", "compress")
        return n

    def _audit(self, action_type: str, oid: str, note: str) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "MGC",
            "action_type": action_type, "object_ids": [oid],
            "payload": {"capability_class": "memory", "note": note},
            "evidence_confidence": 1.0, "evidence_source": "MGC",
            "prev_hash": "", "hash": ""})
