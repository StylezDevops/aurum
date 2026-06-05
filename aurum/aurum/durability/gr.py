"""GR — Goal Registry. Covers WHY the agent acts. Goals decay (health).

A long-running agent accumulates tools/skills for goals that no longer matter; GR tracks
intent so things can expire with it. Each goal carries a computed `health` — a goal that's
achieved, abandoned, or superseded loses health and therefore INFLUENCE: it drops out of
`active()` (so AA won't build for it and TL won't grant autonomy for it) before it is
formally expired.

Health = f(importance, progress, recent_activity, owner_interest), each in [0,1]. Note
`progress` enters as REMAINING work (1 - progress): an achieved goal stops deserving
resources. recent_activity / owner_interest decay with time since the last progress / owner
touch over `decay_window`. Own SQLite store (mutable goal state). Writes audit events to EL
(one-directional) and registers each goal as an `active_goal` node in CS so goal-removal
blast radius is queryable (accept b). Reads nothing back from either.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, TypedDict


class GoalHealth(TypedDict):
    score: float
    importance: float
    progress: float
    recent_activity: float
    owner_interest: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS gr_goals (
    goal_id       TEXT PRIMARY KEY,
    goal          TEXT NOT NULL,
    owner         TEXT,
    priority      REAL,
    importance    REAL NOT NULL DEFAULT 0.5,
    progress      REAL NOT NULL DEFAULT 0.0,
    expiry        REAL,
    dependencies  TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    REAL NOT NULL,
    last_touch    REAL NOT NULL,
    last_activity REAL NOT NULL
);
"""

_DEFAULT_DECAY_WINDOW = 30.0 * 24 * 3600  # 30 days


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


class GoalRegistry:
    ORGAN = "GR"

    def __init__(self, path: str = "aurum_gr.db", el: Any = None, cs: Any = None,
                 decay_window: float = _DEFAULT_DECAY_WINDOW,
                 health_threshold: float = 0.5) -> None:
        self._el = el
        self._cs = cs
        self.decay_window = float(decay_window)
        self.health_threshold = float(health_threshold)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- registration -------------------------------------------------------
    def add(self, goal: Dict[str, Any]) -> str:
        gid = goal.get("goal_id") or uuid.uuid4().hex
        now = goal.get("now") or time.time()
        importance = goal.get("importance")
        if importance is None:
            pr = goal.get("priority")
            importance = _clamp01(float(pr) / 5.0) if pr is not None else 0.5
        self._db.execute(
            "INSERT INTO gr_goals(goal_id,goal,owner,priority,importance,progress,"
            "expiry,dependencies,status,created_at,last_touch,last_activity) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (gid, goal.get("goal", ""), goal.get("owner"), goal.get("priority"),
             _clamp01(float(importance)), _clamp01(float(goal.get("progress", 0.0))),
             goal.get("expiry"), json.dumps(goal.get("dependencies", [])),
             "active", now, now, now),
        )
        if self._cs is not None:        # feed CS so goal-removal blast radius is queryable
            self._cs.add_node(gid, "active_goal")
        self._audit("PROPOSAL", gid)    # goal registration
        return gid

    def get(self, goal_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            "SELECT goal_id,goal,owner,priority,importance,progress,expiry,"
            "dependencies,status,created_at,last_touch,last_activity "
            "FROM gr_goals WHERE goal_id=?", (goal_id,)).fetchone()
        if row is None:
            raise KeyError(f"GR: no goal {goal_id!r}")
        return {"goal_id": row[0], "goal": row[1], "owner": row[2], "priority": row[3],
                "importance": row[4], "progress": row[5], "expiry": row[6],
                "dependencies": json.loads(row[7] or "[]"), "status": row[8],
                "created_at": row[9], "last_touch": row[10], "last_activity": row[11]}

    # -- health-filtered view ----------------------------------------------
    def active(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = time.time() if now is None else now
        out = []
        for row in self._db.execute(
                "SELECT goal_id FROM gr_goals WHERE status='active'").fetchall():
            g = self.get(row[0])
            if g["expiry"] is not None and g["expiry"] <= now:
                continue  # past expiry
            if self.health(g["goal_id"], now=now)["score"] >= self.health_threshold:
                out.append(g)
        return out

    def expire(self, goal_id: str) -> None:
        self.get(goal_id)  # KeyError if missing
        self._db.execute("UPDATE gr_goals SET status='expired' WHERE goal_id=?",
                         (goal_id,))
        self._audit("ARCHIVE", goal_id)

    def depends(self, goal_id: str) -> List[str]:
        return self.get(goal_id)["dependencies"]

    def health(self, goal_id: str, now: Optional[float] = None) -> GoalHealth:
        g = self.get(goal_id)
        now = time.time() if now is None else now
        w = self.decay_window if self.decay_window > 0 else 1.0
        recent_activity = _clamp01(1.0 - (now - g["last_activity"]) / w)
        owner_interest = _clamp01(1.0 - (now - g["last_touch"]) / w)
        importance, progress = g["importance"], g["progress"]
        # progress enters as remaining work: an achieved goal stops deserving resources.
        score = (0.25 * importance + 0.15 * (1.0 - progress)
                 + 0.30 * recent_activity + 0.30 * owner_interest)
        return {"score": score, "importance": importance, "progress": progress,
                "recent_activity": recent_activity, "owner_interest": owner_interest}

    def touch(self, goal_id: str, now: Optional[float] = None) -> None:
        """Owner refreshes interest (and activity)."""
        self.get(goal_id)  # KeyError if missing
        now = time.time() if now is None else now
        self._db.execute(
            "UPDATE gr_goals SET last_touch=?, last_activity=? WHERE goal_id=?",
            (now, now, goal_id))

    # -- internals ----------------------------------------------------------
    def _audit(self, action_type: str, goal_id: str) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "GR",
            "action_type": action_type, "object_ids": [goal_id],
            "payload": {"capability_class": "goal", "goal_id": goal_id},
            "evidence_confidence": 1.0, "evidence_source": "GR",
            "prev_hash": "", "hash": ""})
