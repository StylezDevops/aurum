"""CB — Circuit Breaker. External-anomaly trips + capability-growth freeze.

Two deliberately SEPARATE mechanisms (spec §CB — coordinate, do NOT merge with EG,
which handles INTERNAL epistemic reroute; CB handles EXTERNAL anomaly):

  1. ANOMALY TRIP — ``trip(signal)`` opens a breaker on an external anomaly (spend
     spike, failure streak, off-pattern) or an EL integrity break. An integrity /
     explicitly-flagged trip escalates to LOCKOUT (system-wide emergency stop, e.g.
     the AURUM_ERR_001 path: ``EL.verify_chain()`` fails → ``CB.trip()`` → lockout).
     A breaker is cleared ONLY by ``reset()`` [HUMAN_GATE] — denials persist by
     intent (spec invariant: a blocked action cannot be reopened by rewording or
     waiting; reversal needs a real state change, here a human reset).

  2. CAPABILITY-GROWTH FREEZE — ``freeze_growth(class)`` [HUMAN_GATE]: "stop
     evolution, keep operation." Halts CREATION paths (AA synth / TS promote / LS
     revision / TL tier-up) for ONE capability class while already-promoted
     artifacts keep operating untouched. Distinct from an anomaly trip; not driven
     by any threshold. Lifted only by ``unfreeze_growth(class)`` [HUMAN_GATE].

State is persisted to its OWN SQLite file (mutable — trips reset, freezes lift), so
a trip/lockout survives a restart: a restart must never silently clear an emergency
lockout. CB WRITES audit events to EL one-directionally when an EL handle is supplied
(it never reads EL). HUMAN_GATE here is documentary, matching the rest of the
scaffold (TS/TL/LS) — enforcement arrives with PK/AG; the persistence + human-only
recovery is what makes the denial durable in the meantime.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

# Global breaker key + states. CLOSED is the ABSENCE of a breaker row (the normal
# case), so a fresh store is closed-by-default with no seeding.
GLOBAL = "__global__"
CLOSED = "closed"
OPEN = "open"
LOCKOUT = "lockout"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cb_breakers (
    capability TEXT PRIMARY KEY,   -- capability class, or '__global__'
    status     TEXT NOT NULL,      -- 'open' | 'lockout'  (CLOSED = no row)
    signal     TEXT,               -- JSON of the tripping signal
    tripped_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cb_freezes (
    capability_class TEXT PRIMARY KEY,
    frozen_at        TEXT NOT NULL,
    reason           TEXT
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CircuitBreaker:
    ORGAN = "CB"

    def __init__(self, path: str = "aurum_cb.db", el: Any = None) -> None:
        # el is the EvidenceLedger; CB writes (never reads) audit events to it.
        self._el = el
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- anomaly breakers ---------------------------------------------------
    def trip(self, signal: Any) -> None:
        """Open a breaker on an external anomaly. An integrity break or a signal
        carrying ``lockout`` escalates to a system-wide LOCKOUT. The protective
        state is written FIRST and the audit write is best-effort: when integrity
        is in question we fail toward STAYING tripped rather than letting an audit
        failure un-trip the breaker."""
        sig = signal if isinstance(signal, dict) else {"kind": str(signal)}
        capability = sig.get("capability") or GLOBAL
        lockout = bool(sig.get("lockout")) or sig.get("kind") == "integrity"
        if lockout:
            capability, status = GLOBAL, LOCKOUT
        else:
            status = OPEN
        self._db.execute(
            "INSERT INTO cb_breakers(capability,status,signal,tripped_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(capability) DO UPDATE SET "
            "status=excluded.status, signal=excluded.signal, "
            "tripped_at=excluded.tripped_at",
            (capability, status, json.dumps(sig), _utc_now_iso()),
        )
        self._audit("trip", capability, sig, swallow=True)

    def state(self, capability: str = GLOBAL) -> str:
        """Breaker status for a capability. A global LOCKOUT shadows everything."""
        g = self._status(GLOBAL)
        if g == LOCKOUT:
            return LOCKOUT
        if capability == GLOBAL:
            return g or CLOSED
        return self._status(capability) or CLOSED

    def reset(self) -> None:  # HUMAN_GATE
        """Clear ALL anomaly breakers (incl. lockout). Growth freezes are a separate
        deliberate mode and are NOT cleared here — use unfreeze_growth."""
        self._db.execute("DELETE FROM cb_breakers")
        self._audit("reset", GLOBAL, {})

    # -- capability-growth freeze ------------------------------------------
    def freeze_growth(self, capability_class: str) -> None:  # HUMAN_GATE
        self._db.execute(
            "INSERT OR IGNORE INTO cb_freezes(capability_class,frozen_at) "
            "VALUES (?,?)",
            (capability_class, _utc_now_iso()),
        )
        self._audit("freeze_growth", capability_class, {})

    def unfreeze_growth(self, capability_class: str) -> None:  # HUMAN_GATE
        self._db.execute(
            "DELETE FROM cb_freezes WHERE capability_class=?", (capability_class,)
        )
        self._audit("unfreeze_growth", capability_class, {})

    def is_frozen(self, capability_class: str) -> bool:
        """True iff this class's GROWTH is frozen — either an explicit class freeze
        or a system-wide lockout (which halts all growth). Anomaly trips on a single
        capability are an orthogonal axis; query those via state()."""
        if self._status(GLOBAL) == LOCKOUT:
            return True
        row = self._db.execute(
            "SELECT 1 FROM cb_freezes WHERE capability_class=?", (capability_class,)
        ).fetchone()
        return row is not None

    # -- internals ----------------------------------------------------------
    def _status(self, capability: str) -> Optional[str]:
        row = self._db.execute(
            "SELECT status FROM cb_breakers WHERE capability=?", (capability,)
        ).fetchone()
        return row[0] if row else None

    def _audit(self, cb_action: str, capability: str, signal: Any,
               swallow: bool = False) -> None:
        """One-directional audit write to EL. action_type reuses the authoritative
        ELActionType "EXCEPTION" (a trip/freeze is a governance exception event);
        the specific CB action lives in payload. ``swallow`` keeps a protective trip
        in force even if the audit write fails."""
        if self._el is None:
            return
        ev = {
            "event_id": "", "timestamp": "", "source_organ": "CB",
            "action_type": "EXCEPTION", "object_ids": [capability],
            "payload": {"cb_action": cb_action,
                        "capability_class": capability, "signal": signal},
            "evidence_confidence": 1.0, "evidence_source": "CB",
            "prev_hash": "", "hash": "",
        }
        try:
            self._el.append(ev)
        except Exception:
            if not swallow:
                raise
