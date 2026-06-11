"""PM — Preference Model. Separates enduring preferences from goals and identity.

The single source of truth for "how the owner likes things done" — standing constraints
("use PowerShell", "API-first", "no em dashes", "never LinkedIn") that persist while goals
churn. LS is FORBIDDEN from encoding a preference as a constitution rule; preference-shaped
proposals route here instead, keeping the constitution lean.

Each preference carries scope (global / domain / task_type), strength, and provenance
(stated vs inferred). STATED outranks INFERRED: where they conflict on the same key, the
stated one owns the verdict and the inferred one is suppressed. Inferred preferences are
PROPOSALS until confirmed. OI consults check() to judge whether an outcome respected
preferences, not just completed the task. Own SQLite store; add logged to EL.
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_prefs (
    pref_id    TEXT PRIMARY KEY,
    statement  TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'global',   -- global | domain | task_type
    domain     TEXT,
    task_type  TEXT,
    strength   REAL NOT NULL DEFAULT 1.0,
    provenance TEXT NOT NULL DEFAULT 'stated',   -- stated | inferred
    forbids    TEXT,                             -- substring that must NOT appear
    requires   TEXT,                             -- substring that MUST appear
    key        TEXT,                             -- conflict key (stated owns it)
    status     TEXT NOT NULL DEFAULT 'active'    -- active | proposed
);
"""


class PreferenceModel:
    ORGAN = "PM"

    def __init__(self, path: str = "aurum_pm.db", el: Any = None) -> None:
        self._el = el
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- registration -------------------------------------------------------
    def add(self, preference: Dict[str, Any], provenance: str) -> str:
        pid = preference.get("pref_id") or uuid.uuid4().hex
        # inferred preferences are proposals until confirmed; stated are active
        status = "active" if provenance == "stated" else "proposed"
        self._db.execute(
            "INSERT INTO pm_prefs(pref_id,statement,scope,domain,task_type,strength,"
            "provenance,forbids,requires,key,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, preference.get("statement", ""), preference.get("scope", "global"),
             preference.get("domain"), preference.get("task_type"),
             float(preference.get("strength", 1.0)), provenance,
             preference.get("forbids"), preference.get("requires"),
             preference.get("key"), status),
        )
        self._audit(pid, provenance)
        return pid

    def get(self, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        if scope is None:
            rows = self._db.execute("SELECT * FROM pm_prefs").fetchall()
        else:
            rows = self._db.execute("SELECT * FROM pm_prefs WHERE scope=?",
                                    (scope,)).fetchall()
        return [self._row(r) for r in rows]

    # -- matching / evaluation ---------------------------------------------
    def applies(self, action: Any) -> List[Dict[str, Any]]:
        """Preferences relevant to an action: global always; domain / task_type when
        they match the action's domain / task_type."""
        domain = action.get("domain") if isinstance(action, dict) else None
        task_type = action.get("task_type") if isinstance(action, dict) else None
        out = []
        for p in self.get():
            if p["scope"] == "global" \
                    or (p["scope"] == "domain" and p["domain"] == domain) \
                    or (p["scope"] == "task_type" and p["task_type"] == task_type):
                out.append(p)
        return out

    def check(self, output: Any) -> Dict[str, List[str]]:
        """-> {respected:[id], violated:[id]}. STATED outranks INFERRED: an inferred
        preference whose conflict key is owned by a stated one is suppressed."""
        text = output if isinstance(output, str) else str(output.get("text", output))
        prefs = self.get()
        stated_keys = {p["key"] for p in prefs
                       if p["provenance"] == "stated" and p["key"]}
        respected, violated = [], []
        for p in prefs:
            if p["provenance"] == "inferred" and p["key"] in stated_keys:
                continue  # stated preference owns this key — suppress the inferred one
            verdict = self._evaluate(p, text)
            if verdict is True:
                respected.append(p["pref_id"])
            elif verdict is False:
                violated.append(p["pref_id"])
        return {"respected": respected, "violated": violated}

    @staticmethod
    def _evaluate(pref: Dict[str, Any], text: str) -> Optional[bool]:
        """True respected / False violated / None not-applicable to this output."""
        verdict: Optional[bool] = None
        if pref.get("forbids"):
            verdict = pref["forbids"] not in text
        if pref.get("requires"):
            req_ok = pref["requires"] in text
            verdict = req_ok if verdict is None else (verdict and req_ok)
        return verdict

    # -- internals ----------------------------------------------------------
    def _row(self, r) -> Dict[str, Any]:
        cols = ["pref_id", "statement", "scope", "domain", "task_type", "strength",
                "provenance", "forbids", "requires", "key", "status"]
        return dict(zip(cols, r))

    def _audit(self, pref_id: str, provenance: str) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "PM",
            "action_type": "PROPOSAL", "object_ids": [pref_id],
            "payload": {"capability_class": "preference", "pref_id": pref_id,
                        "provenance": provenance},
            "evidence_confidence": 1.0, "evidence_source": "PM",
            "prev_hash": "", "hash": ""})
