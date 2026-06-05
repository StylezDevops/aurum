"""OI — Outcome Interpreter. Task completion != goal satisfaction.

A task can succeed mechanically while the OUTCOME fails (booked a holiday, terrible hotel).
OI judges outcome QUALITY against the originating goal + the owner's preferences so BB learns
the real lesson and TL/AG don't get a false positive.

TIERED SATISFACTION ORACLE (the Goodhart bullseye): a PROXY signal (HVP/PM/corrections) runs
the loop immediately, marked satisfaction_source="proxy", low confidence, and ALONE may NOT
drive self-modification. OI asynchronously SAMPLES a fraction of completed tasks for delayed
owner judgment (satisfaction_source="human", high confidence) — that ground truth is what
self-modification weights, and it CALIBRATES the proxy (a proxy that diverges is down-weighted).

OPERATIONAL EFFECTIVENESS (over-caution guard): effectiveness = mean(completion×satisfaction)
/ normalized cost (w_v·overhead + w_i·interruptions + w_l·latency, each scaled [0,1] vs its
rolling baseline, weights summing to 1.0). The signal is the TREND, not the absolute value.

Scope bound: OI judges outcomes + computes the effectiveness ratio; it does NOT arbitrate
conflict or adjudicate deadlock (those READ OI). Own SQLite store; verdicts logged to EL with
satisfaction_source.
"""
from __future__ import annotations

import math
import sqlite3
import time
from typing import Any, Dict, List, Optional, TypedDict


class OutcomeVerdict(TypedDict):
    completed: bool
    satisfied: bool
    quality: float
    satisfaction_source: str
    signals: Dict[str, Any]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS oi_verdicts (
    task_id          TEXT PRIMARY KEY,
    goal_id          TEXT,
    capability_class TEXT,
    completed        INTEGER NOT NULL,
    satisfied        INTEGER NOT NULL,
    quality          REAL NOT NULL,
    satisfaction_source TEXT NOT NULL,
    human_satisfied  INTEGER,                 -- null until owner-sampled
    overhead         REAL NOT NULL DEFAULT 0,
    interruptions    REAL NOT NULL DEFAULT 0,
    latency          REAL NOT NULL DEFAULT 0,
    stakes           TEXT NOT NULL DEFAULT 'routine',
    proxy_confidence REAL NOT NULL DEFAULT 0.3,
    ts               REAL NOT NULL
);
"""

_EFF_WEIGHTS = {"overhead": 1 / 3, "interruptions": 1 / 3, "latency": 1 / 3}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


class OutcomeInterpreter:
    ORGAN = "OI"

    def __init__(self, path: str = "aurum_oi.db", el: Any = None, pm: Any = None,
                 bb_record: Any = None, eff_weights: Optional[Dict[str, float]] = None
                 ) -> None:
        self._el, self._pm, self._bb = el, pm, bb_record
        self.eff_weights = eff_weights or dict(_EFF_WEIGHTS)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- proxy interpretation ----------------------------------------------
    def interpret(self, task_result: Any, goal_id: str) -> OutcomeVerdict:
        completed = bool(task_result.get("completed"))
        violations: List[str] = []
        if self._pm is not None and "output" in task_result:
            violations = self._pm.check(task_result["output"]).get("violated", [])
        redone = bool(task_result.get("redone"))
        proxy_ok = bool(task_result.get("proxy_satisfied", True))
        satisfied = completed and not violations and not redone and proxy_ok
        quality = _clamp01(task_result.get("quality", 1.0 if satisfied else 0.3))
        signals = {"preference_violations": violations, "redone": redone,
                   "proxy_satisfied": proxy_ok}
        task_id = task_result.get("task_id") or f"{goal_id}:{time.time()}"
        self._db.execute(
            "INSERT INTO oi_verdicts(task_id,goal_id,capability_class,completed,"
            "satisfied,quality,satisfaction_source,overhead,interruptions,latency,"
            "stakes,proxy_confidence,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(task_id) DO UPDATE SET satisfied=excluded.satisfied, "
            "quality=excluded.quality",
            (task_id, goal_id, task_result.get("capability_class"), int(completed),
             int(satisfied), quality, "proxy", float(task_result.get("overhead", 0)),
             float(task_result.get("interruptions", 0)),
             float(task_result.get("latency", 0)),
             task_result.get("stakes", "routine"),
             float(task_result.get("proxy_confidence", 0.3)), time.time()),
        )
        # completed-but-not-satisfied is a LEARNING case for BB, not a success
        if completed and not satisfied and self._bb is not None:
            self._bb({"organ": "OI", "goal_id": goal_id,
                      "lesson": "completed_unsatisfied", "signals": signals})
        self._audit(goal_id, satisfied, "proxy")
        return {"completed": completed, "satisfied": satisfied, "quality": quality,
                "satisfaction_source": "proxy", "signals": signals}

    # -- ground-truth sampling + calibration -------------------------------
    def sample_for_human(self, rate: float) -> List[str]:
        """Risk-weighted async sample of not-yet-human-judged tasks (high-stakes /
        low-proxy-confidence first). Returns task_ids for delayed owner judgment."""
        rows = self._db.execute(
            "SELECT task_id, stakes, proxy_confidence FROM oi_verdicts "
            "WHERE human_satisfied IS NULL").fetchall()
        if not rows or rate <= 0:
            return []
        rows.sort(key=lambda r: (0 if r[1] == "high_stakes" else 1, r[2]))  # risk first
        n = max(1, math.ceil(_clamp01(rate) * len(rows)))
        return [r[0] for r in rows[:n]]

    def record_human_verdict(self, task_id: str, verdict: Any) -> None:
        sat = int(bool(verdict.get("satisfied") if isinstance(verdict, dict) else verdict))
        self._db.execute(
            "UPDATE oi_verdicts SET human_satisfied=?, satisfaction_source='human', "
            "proxy_confidence=1.0 WHERE task_id=?", (sat, task_id))
        self._audit(task_id, bool(sat), "human")

    def proxy_calibration(self) -> Dict[str, Any]:
        """-> {proxy_vs_human_agreement, proxy_weight}. A divergent proxy is down-weighted."""
        rows = self._db.execute(
            "SELECT satisfied, human_satisfied FROM oi_verdicts "
            "WHERE human_satisfied IS NOT NULL").fetchall()
        if not rows:
            return {"proxy_vs_human_agreement": 1.0, "proxy_weight": 1.0, "samples": 0}
        agree = sum(1 for s, h in rows if s == h) / len(rows)
        return {"proxy_vs_human_agreement": agree, "proxy_weight": agree,
                "samples": len(rows)}

    # -- trend + effectiveness ---------------------------------------------
    def trend(self, capability_class: str) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT ts, quality, satisfied, satisfaction_source FROM oi_verdicts "
            "WHERE capability_class=? ORDER BY ts ASC", (capability_class,)).fetchall()
        return [{"ts": r[0], "quality": r[1], "satisfied": bool(r[2]),
                 "satisfaction_source": r[3]} for r in rows]

    def effectiveness(self) -> Dict[str, float]:
        rows = self._db.execute(
            "SELECT completed,satisfied,human_satisfied,overhead,interruptions,latency "
            "FROM oi_verdicts").fetchall()
        if not rows:
            return {"ratio": 0.0, "completion": 0.0, "satisfaction": 0.0,
                    "overhead": 0.0, "interruptions": 0.0, "latency": 0.0}
        n = len(rows)
        completion = sum(r[0] for r in rows) / n
        # human-sourced satisfaction overrides proxy where present
        sat = sum((r[2] if r[2] is not None else r[1]) for r in rows) / n
        overhead = sum(r[3] for r in rows) / n
        interruptions = sum(r[4] for r in rows) / n
        latency = sum(r[5] for r in rows) / n
        # scale each cost component to [0,1] vs the window's own max (rolling baseline)
        mo = max((r[3] for r in rows), default=0.0) or 1.0
        mi = max((r[4] for r in rows), default=0.0) or 1.0
        ml = max((r[5] for r in rows), default=0.0) or 1.0
        w = self.eff_weights
        denom = (w["overhead"] * (overhead / mo)
                 + w["interruptions"] * (interruptions / mi)
                 + w["latency"] * (latency / ml))
        ratio = (completion * sat) / max(denom, 1e-6)
        return {"ratio": ratio, "completion": completion, "satisfaction": sat,
                "overhead": overhead, "interruptions": interruptions, "latency": latency}

    # -- audit --------------------------------------------------------------
    def _audit(self, oid: str, satisfied: bool, source: str) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "OI",
            "action_type": "VOTE", "object_ids": [oid],
            "payload": {"capability_class": "outcome", "satisfied": satisfied,
                        "satisfaction_source": source},
            "evidence_confidence": 1.0 if source == "human" else 0.3,
            "evidence_source": "OI", "prev_hash": "", "hash": ""})
