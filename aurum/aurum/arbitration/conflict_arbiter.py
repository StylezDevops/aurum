"""CA — Conflict Arbiter (synchronous, in the decision path). Deliberately DUMB.

Resolves a live conflict among SOFT governance signals deterministically, per action:
most-conservative-wins, no model call, fully replayable. All intelligence is downstream
(OI-effectiveness + DD + the human). Do NOT optimise CA — a clever per-step arbiter
reintroduces the order-dependent, non-replayable coupling the design kills.

Runs ONLY below the hard layer (§1): PK hard-deny / CB freeze / HUMAN_GATE are handled
upstream and a hard-blocked action must NEVER enter CA (AURUM_ERR_015). Tier-1 {AG, HVP}
are contraction-capable; Tier-2 {OI, LS} are proceed-only and can never override a Tier-1
contraction. A ConflictRecord is written whenever ≥2 signals held directives — even if the
contraction agreed with a deny, even when the outcome equals a plain proceed (AURUM_ERR_016);
a silent contraction blinds DD forever. The risk_snapshot is captured BY VALUE, immutably.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CONTRACT = "contract"
PROCEED = "proceed"
TIER1 = ("AG", "HVP")          # contraction-capable
TIER2 = ("OI", "LS")           # proceed-only
_MOST_RESTRICTIVE = ("AG", "HVP")  # total order — names the winner only
ARBITER_VERSION = "ca-1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ca_conflicts (
    conflict_id      TEXT PRIMARY KEY,
    ts               TEXT NOT NULL,
    action_id        TEXT NOT NULL,
    capability_class TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    resolution       TEXT NOT NULL,
    winner           TEXT,
    winner_constitutional INTEGER NOT NULL DEFAULT 0,
    risk_snapshot_json TEXT NOT NULL,
    evidence_version TEXT NOT NULL,
    arbiter_version  TEXT NOT NULL
);
"""


class ArbitrationError(RuntimeError):
    """Raised when a hard-blocked action is handed to CA (it must not be arbitrated)."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConflictArbiter:
    ORGAN = "CA"

    def __init__(self, path: str = "aurum_ca.db", el: Any = None) -> None:
        self._el = el
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    def arbitrate(self, action: Dict[str, Any],
                  signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """signals: [{signal, directive: contract|proceed, basis:{...}, constitutional:bool}].
        Returns {resolution, winner, record}. Pure + deterministic over the full set."""
        if action.get("pk_deny") or action.get("cb_frozen") or action.get("needs_gate"):
            raise ArbitrationError(
                "hard layer not arbitrated: action is PK-denied / CB-frozen / gated")
        contractors = [s for s in signals
                       if s["signal"] in TIER1 and s.get("directive") == CONTRACT]
        if contractors:
            resolution = CONTRACT
            winner = self._most_restrictive(contractors)
        else:
            resolution, winner = PROCEED, None
        record = self._record(action, signals, resolution, winner)
        if len([s for s in signals if s.get("directive")]) >= 2:
            self._write(record)  # mandatory whenever ≥2 signals held directives
        return {"resolution": resolution, "winner": winner, "record": record}

    @staticmethod
    def _most_restrictive(contractors: List[Dict[str, Any]]) -> str:
        present = {c["signal"] for c in contractors}
        for sig in _MOST_RESTRICTIVE:
            if sig in present:
                return sig
        return contractors[0]["signal"]

    def _record(self, action, signals, resolution, winner) -> Dict[str, Any]:
        win = next((s for s in signals if s["signal"] == winner), None)
        # risk_snapshot: immutable by-value capture of the justifying signals NOW.
        snapshot = {s["signal"]: dict(s.get("basis", {})) for s in signals}
        return {
            "conflict_id": uuid.uuid4().hex, "timestamp": _utc_now_iso(),
            "action_id": action.get("action_id", "action"),
            "capability_class": action.get("capability_class", "default"),
            "participants": [{"signal": s["signal"], "directive": s.get("directive"),
                              "basis": dict(s.get("basis", {})),
                              "constitutional": bool(s.get("constitutional", False))}
                             for s in signals],
            "resolution": resolution, "winner": winner,
            "winner_constitutional": bool(win.get("constitutional")) if win else False,
            "risk_snapshot": snapshot,
            "evidence_version": str(self._el.tip_seq()) if self._el is not None else "0",
            "arbiter_version": ARBITER_VERSION,
        }

    def _write(self, r: Dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO ca_conflicts(conflict_id,ts,action_id,capability_class,"
            "participants_json,resolution,winner,winner_constitutional,"
            "risk_snapshot_json,evidence_version,arbiter_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["conflict_id"], r["timestamp"], r["action_id"], r["capability_class"],
             json.dumps(r["participants"]), r["resolution"], r["winner"],
             int(r["winner_constitutional"]), json.dumps(r["risk_snapshot"]),
             r["evidence_version"], r["arbiter_version"]),
        )
        if self._el is not None:  # mirror a summary to the canonical ledger
            self._el.log_conflict({
                "conflict_id": r["conflict_id"], "action": r["action_id"],
                "winner": r["winner"] or "none",
                "loser": next((p["signal"] for p in r["participants"]
                               if p["signal"] != r["winner"]), "none"),
                "winner_position": r["resolution"], "loser_position": PROCEED,
                "risk_signals_live": None})

    def conflicts(self) -> List[Dict[str, Any]]:
        """Full ConflictRecords for DD to read (richer than EL's summary row)."""
        rows = self._db.execute(
            "SELECT conflict_id,ts,action_id,capability_class,participants_json,"
            "resolution,winner,winner_constitutional,risk_snapshot_json,evidence_version "
            "FROM ca_conflicts ORDER BY ts ASC").fetchall()
        return [{"conflict_id": r[0], "timestamp": r[1], "action_id": r[2],
                 "capability_class": r[3], "participants": json.loads(r[4]),
                 "resolution": r[5], "winner": r[6],
                 "winner_constitutional": bool(r[7]),
                 "risk_snapshot": json.loads(r[8]), "evidence_version": r[9]}
                for r in rows]
