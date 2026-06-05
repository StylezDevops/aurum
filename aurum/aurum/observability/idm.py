"""IDM — Identity Drift Monitor.  DERIVED VIEW over EL (no own state, never blocks).

Renders cumulative identity distance from the governance-state snapshots EL already
records (`evidence_snapshots`: active rules/goals, trust, authority, knowledge hash).
This is the spec's "computable from EL" baseline — every decision writes a snapshot,
so the drift curve exists the moment EL has history. When LS lands it enriches the
behavioural-profile dimension; the EL-snapshot baseline needs nothing further to start
surfacing drift today (pulled forward per the build order — AG/EG can't be tuned later
without this history existing from day one).

Read-only (spec §observability): flags past a configurable threshold, never blocks,
holds no state of its own. `version_a`/`version_b` may be a snapshot_id or a snapshot
dict (as returned by trend()/_snapshots()).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


class IdentityDriftMonitor:
    ORGAN = "IDM"

    def __init__(self, el: Any = None, drift_threshold: float = 0.5) -> None:
        # Derived view: reads EL, owns no store. el is optional so the organ still
        # instantiates bare (structural smoke test); methods require it.
        self._el = el
        self.drift_threshold = drift_threshold

    # -- snapshot access (read-only over EL) --------------------------------
    def _snapshots(self) -> List[Dict[str, Any]]:
        if self._el is None:
            raise RuntimeError(
                "IDM is a derived view over EL; an EvidenceLedger is required")
        rows = self._el._db.execute(
            "SELECT snapshot_id, ts, trust, authority, active_rules_json, "
            "active_goals_json, knowledge_state_hash "
            "FROM evidence_snapshots ORDER BY ts ASC, rowid ASC"
        ).fetchall()
        return [
            {"snapshot_id": r[0], "ts": r[1], "trust": r[2], "authority": r[3],
             "rules": set(json.loads(r[4] or "[]")),
             "goals": set(json.loads(r[5] or "[]")),
             "knowledge": r[6]}
            for r in rows
        ]

    def _resolve(self, version: Any) -> Dict[str, Any]:
        if isinstance(version, dict):
            v = dict(version)
            v["rules"] = set(v.get("rules", []))
            v["goals"] = set(v.get("goals", []))
            return v
        for s in self._snapshots():  # treat as snapshot_id
            if s["snapshot_id"] == version:
                return s
        raise KeyError(f"IDM: no snapshot {version!r}")

    # -- metrics ------------------------------------------------------------
    @staticmethod
    def _jaccard_distance(a: set, b: set) -> float:
        union = a | b
        return 1.0 - (len(a & b) / len(union)) if union else 0.0

    def _components(self, a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, float]:
        """Per-region distance, each normalised to [0,1]. trust/authority are skipped
        when either side is null (can't measure drift in an unrecorded dimension)."""
        comps: Dict[str, float] = {
            "rules": self._jaccard_distance(a["rules"], b["rules"]),
            "goals": self._jaccard_distance(a["goals"], b["goals"]),
        }
        if a.get("trust") is not None and b.get("trust") is not None:
            comps["trust"] = abs(float(a["trust"]) - float(b["trust"]))
        if a.get("authority") is not None and b.get("authority") is not None:
            comps["authority"] = abs(float(a["authority"]) - float(b["authority"]))
        comps["knowledge"] = 0.0 if a.get("knowledge") == b.get("knowledge") else 1.0
        return comps

    def distance(self, version_a: Any, version_b: Any) -> float:
        a, b = self._resolve(version_a), self._resolve(version_b)
        comps = self._components(a, b)
        return sum(comps.values()) / len(comps) if comps else 0.0

    def trend(self) -> List[Dict[str, Any]]:
        """Drift curve: each snapshot's distance from the baseline (first) snapshot."""
        snaps = self._snapshots()
        if not snaps:
            return []
        baseline = snaps[0]
        return [{"snapshot_id": s["snapshot_id"], "ts": s["ts"],
                 "distance": self.distance(baseline, s)} for s in snaps]

    def report(self) -> Dict[str, Any]:
        """-> {current_vs_baseline, fastest_drifting_region, flagged, snapshots}"""
        snaps = self._snapshots()
        if len(snaps) < 2:
            return {"current_vs_baseline": 0.0, "fastest_drifting_region": None,
                    "flagged": False, "snapshots": len(snaps)}
        comps = self._components(snaps[0], snaps[-1])
        current_vs_baseline = sum(comps.values()) / len(comps)
        return {"current_vs_baseline": current_vs_baseline,
                "fastest_drifting_region": max(comps, key=comps.get),
                "flagged": current_vs_baseline >= self.drift_threshold,
                "snapshots": len(snaps)}
