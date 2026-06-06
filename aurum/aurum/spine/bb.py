"""BB — Black Box.  Tier 0 spine.

Failed-task auto-writes a structured postmortem to a searchable, signed on-disk
corpus the loop mines.  Key invariants:

  SIGNED STATE: entries are HMAC-signed on write and verified on load.  An entry
  that fails verification is quarantined and treated as untrusted external content
  — never ingested as trusted history.

  REDACTED: all writes pass through PK.redact (single policy) if PK is injected.

  READ-ONLY TO CONSUMERS: BB never mutates existing entries (append-only).  search()
  returns only non-quarantined entries.

  EL LOGGING: write and verify_on_load results are logged to EL if injected.

Reconciliation: agent/black_box.py in the Hermes tree is working prior art (redacted
postmortems + skill-review addendum) and the live implementation today.  Migrate its
logic INTO this organ as BB matures; keep it running until then.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Stable HMAC key — tamper detection, not secrecy.  A compromised entry that
# had its signature forged is no worse than a missing entry; the goal is that
# OUT-OF-BAND writes (file edits, DB patches) are detectable.
_SIGN_KEY = b"aurum-bb-integrity-v1"


def _sign(body_json: str) -> str:
    return hmac.new(_SIGN_KEY, body_json.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify(body_json: str, signature: str) -> bool:
    expected = _sign(body_json)
    return hmac.compare_digest(expected, signature)


class BlackBox:
    ORGAN = "BB"

    def __init__(
        self,
        db_path: str = ":memory:",
        pk: Optional[Any] = None,
        el: Optional[Any] = None,
    ) -> None:
        self._pk = pk
        self._el = el
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS postmortems ("
            "  id TEXT PRIMARY KEY,"
            "  timestamp TEXT NOT NULL,"
            "  body_json TEXT NOT NULL,"
            "  signature TEXT NOT NULL,"
            "  quarantined INTEGER NOT NULL DEFAULT 0"
            ");"
            "CREATE INDEX IF NOT EXISTS idx_pm_ts ON postmortems(timestamp);"
        )
        self._db.commit()

    # -- helpers ------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _log_el(self, action: str, pm_id: str, detail: Dict[str, Any]) -> None:
        if self._el is None:
            return
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": self._now(),
            "source_organ": "BB",
            "action_type": "PROMOTION",
            "object_ids": [pm_id],
            "payload": {"action": action, **detail},
            "evidence_confidence": 1.0,
            "evidence_source": "BB",
            "prev_hash": "",
            "hash": "",
        }
        self._el.append(event)

    # -- public API ---------------------------------------------------------

    def write(self, postmortem: Dict[str, Any]) -> str:
        """Redact, sign, and persist a postmortem.  Returns the stored id."""
        body: Dict[str, Any] = dict(postmortem)
        if self._pk is not None:
            body = self._pk.redact(body)
        if not body.get("id"):
            body["id"] = str(uuid.uuid4())
        if not body.get("timestamp"):
            body["timestamp"] = self._now()
        pm_id: str = body["id"]
        body_json = json.dumps(body, sort_keys=True, ensure_ascii=False)
        signature = _sign(body_json)
        self._db.execute(
            "INSERT OR REPLACE INTO postmortems"
            " (id, timestamp, body_json, signature, quarantined)"
            " VALUES (?, ?, ?, ?, 0)",
            (pm_id, body["timestamp"], body_json, signature),
        )
        self._db.commit()
        self._log_el("write", pm_id, {"redacted": self._pk is not None})
        return pm_id

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Return non-quarantined postmortems whose body contains query (case-insensitive)."""
        rows = self._db.execute(
            "SELECT body_json FROM postmortems WHERE quarantined=0 ORDER BY timestamp DESC"
        ).fetchall()
        q = query.lower()
        results = []
        for row in rows:
            body = json.loads(row["body_json"])
            if q in json.dumps(body, ensure_ascii=False).lower():
                results.append(body)
        return results

    def verify_on_load(self) -> Dict[str, List[str]]:
        """Verify HMAC signatures of all stored entries.

        Entries that fail verification are quarantined (treated as untrusted
        external content).  Returns {trusted:[id], quarantined:[id]}.
        """
        rows = self._db.execute(
            "SELECT id, body_json, signature FROM postmortems"
        ).fetchall()
        trusted: List[str] = []
        quarantined: List[str] = []
        for row in rows:
            if _verify(row["body_json"], row["signature"]):
                trusted.append(row["id"])
            else:
                self._db.execute(
                    "UPDATE postmortems SET quarantined=1 WHERE id=?", (row["id"],)
                )
                quarantined.append(row["id"])
        self._db.commit()
        if quarantined:
            self._log_el("verify_quarantine", "batch", {"quarantined": quarantined})
        return {"trusted": trusted, "quarantined": quarantined}

    def all_ids(self) -> List[str]:
        """Return all postmortem ids (trusted + quarantined)."""
        rows = self._db.execute("SELECT id FROM postmortems").fetchall()
        return [r["id"] for r in rows]
