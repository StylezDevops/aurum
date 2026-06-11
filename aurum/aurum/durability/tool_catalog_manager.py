"""TCM — Tool Catalog Manager. Manages tool-ecosystem growth.

AA + TS accumulate dozens-to-hundreds of tools; past ~tens, SELECTION becomes the
bottleneck. TCM keeps a capability taxonomy over promoted tools, detects duplicated/
overlapping capabilities, serves selection BY CAPABILITY (a ranked shortlist, not the
flat catalog), and feeds MGC retirement candidates. AA calls check_overlap pre-build and
recommend_domain pre-synthesis.

Merges/retirements themselves are HUMAN_GATE and routed via TS/MGC — TCM only *recommends*.
Own SQLite store; one-directional EL audit on register. `register`/`record_use` are the
ingest/telemetry seam producers (TS promote, live tool calls) feed; the rest is the frozen
consumer-facing interface.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tcm_tools (
    tool_id          TEXT PRIMARY KEY,
    domain           TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    resource         TEXT,
    usage_count      INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'active'
);
"""


class ToolCatalogManager:
    ORGAN = "TCM"

    def __init__(self, path: str = "aurum_tcm.db", el: Any = None) -> None:
        self._el = el
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- ingest / telemetry seam -------------------------------------------
    def register(self, tool: Dict[str, Any]) -> str:
        """Add a promoted tool to the catalog (producer seam: TS.promote feeds this)."""
        tid = tool["tool_id"]
        self._db.execute(
            "INSERT INTO tcm_tools(tool_id,domain,capabilities_json,resource,"
            "usage_count,status) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(tool_id) DO UPDATE SET domain=excluded.domain, "
            "capabilities_json=excluded.capabilities_json, resource=excluded.resource",
            (tid, tool.get("domain"), json.dumps(sorted(tool.get("capabilities", []))),
             tool.get("resource"), int(tool.get("usage_count", 0)),
             tool.get("status", "active")),
        )
        self._audit("PROMOTION", tid)
        return tid

    def record_use(self, tool_id: str) -> None:
        self._db.execute(
            "UPDATE tcm_tools SET usage_count = usage_count + 1 WHERE tool_id=?",
            (tool_id,))

    # -- frozen consumer interface -----------------------------------------
    def check_overlap(self, tool_or_spec: Any) -> Dict[str, Any]:
        """-> {overlaps:[tool_id], recommend: build|merge|reuse}. reuse if an existing
        tool already covers the spec's whole capability surface (accept a); merge if it
        overlaps an existing tool against the SAME resource (accept b); else build."""
        caps = set(tool_or_spec.get("capabilities", []))
        resource = tool_or_spec.get("resource")
        overlaps, covered, same_resource = [], False, False
        for t in self._tools():
            tcaps = set(t["capabilities"])
            same_res = resource is None or t["resource"] == resource
            if caps & tcaps:
                overlaps.append(t["tool_id"])
                if resource is not None and t["resource"] == resource:
                    same_resource = True
            # reuse only if an existing tool covers the whole surface ON THE SAME
            # resource — covering 'read' on a different resource can't serve this gap.
            if caps and caps <= tcaps and same_res:
                covered = True
        if covered:
            recommend = "reuse"
        elif same_resource:
            recommend = "merge"
        else:
            recommend = "build"
        return {"overlaps": overlaps, "recommend": recommend}

    def recommend_domain(self, gap: Any) -> Dict[str, Any]:
        """-> {domain, extend: tool_id|None}. Picks the best existing tool in the gap's
        domain to extend (most capability overlap, usage as tiebreak), else None."""
        domain = gap.get("domain")
        caps = set(gap.get("capabilities", []))
        best, best_key = None, (0, -1)
        for t in self._tools():
            if domain is not None and t["domain"] != domain:
                continue
            overlap = len(caps & set(t["capabilities"]))
            key = (overlap, t["usage_count"])
            if overlap > 0 and key > best_key:
                best, best_key = t["tool_id"], key
        return {"domain": domain, "extend": best}

    def taxonomy(self) -> Dict[str, List[str]]:
        tree: Dict[str, List[str]] = {}
        for t in self._tools():
            tree.setdefault(t["domain"] or "_uncategorised", []).append(t["tool_id"])
        return tree

    def select(self, capability: str) -> List[str]:
        """Ranked shortlist of tools providing `capability` (most-used first) — not the
        flat catalog (accept c)."""
        matches = [t for t in self._tools() if capability in t["capabilities"]
                   and t["status"] == "active"]
        matches.sort(key=lambda t: (-t["usage_count"], t["tool_id"]))
        return [t["tool_id"] for t in matches]

    def retirement_candidates(self) -> List[str]:
        """Unused / deprecated tools — fed to MGC (which does the actual retirement)."""
        return [t["tool_id"] for t in self._tools()
                if t["usage_count"] == 0 or t["status"] == "deprecated"]

    # -- internals ----------------------------------------------------------
    def _tools(self) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT tool_id,domain,capabilities_json,resource,usage_count,status "
            "FROM tcm_tools").fetchall()
        return [{"tool_id": r[0], "domain": r[1],
                 "capabilities": json.loads(r[2] or "[]"), "resource": r[3],
                 "usage_count": r[4], "status": r[5]} for r in rows]

    def _audit(self, action_type: str, tool_id: str) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "TCM",
            "action_type": action_type, "object_ids": [tool_id],
            "payload": {"capability_class": "tool", "tool_id": tool_id},
            "evidence_confidence": 1.0, "evidence_source": "TCM",
            "prev_hash": "", "hash": ""})
