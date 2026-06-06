"""TS — Toolsmith.  Tier 0 spine.

Governed tool lifecycle: propose → build_caged → test → promote → [quarantine ↔] → deprecate.

QUARANTINE is the missing middle state: a tool that isn't bad enough to delete
but isn't trusted (e.g. reliability drops 98%→70%).  Not promoted, not deprecated
— available only via explicit per-use approval until it recovers or is retired.

HUMAN_GATE: promote and unquarantine require an approved_by argument; without it
they raise PermissionError.  This matches the LS and CB convention (same pattern).

EL LOGGING: every state transition is logged as a PROMOTION event if EL is injected.

Reconciliation: tools/toolsmith.py in the Hermes tree is working prior art
(propose → scan → sandbox-test → staged; never auto-activates).  Migrate its
logic INTO this organ as TS matures; keep it running until then.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_VALID_STATES = frozenset({
    "proposed", "caged", "tested", "promoted", "quarantined", "deprecated",
})


class Toolsmith:
    ORGAN = "TS"

    def __init__(
        self,
        db_path: str = ":memory:",
        el: Optional[Any] = None,
    ) -> None:
        self._el = el
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS tools ("
            "  tool_id TEXT PRIMARY KEY,"
            "  state TEXT NOT NULL,"
            "  spec_json TEXT NOT NULL,"
            "  result_json TEXT,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL,"
            "  quarantine_reason TEXT"
            ");"
            "CREATE INDEX IF NOT EXISTS idx_tools_state ON tools(state);"
        )
        self._db.commit()

    # -- helpers ------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _get(self, tool_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM tools WHERE tool_id=?", (tool_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def _update(self, tool_id: str, **kwargs: Any) -> None:
        kwargs["updated_at"] = self._now()
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [tool_id]
        self._db.execute(f"UPDATE tools SET {sets} WHERE tool_id=?", vals)
        self._db.commit()

    def _log(self, action: str, tool_id: str, detail: Dict[str, Any]) -> None:
        if self._el is None:
            return
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": self._now(),
            "source_organ": "TS",
            "action_type": "PROMOTION",
            "object_ids": [tool_id],
            "payload": {"ts_action": action, **detail},
            "evidence_confidence": 1.0,
            "evidence_source": "TS",
            "prev_hash": "",
            "hash": "",
        }
        self._el.append(event)

    def _require_tool(self, tool_id: str) -> Dict[str, Any]:
        record = self._get(tool_id)
        if record is None:
            raise KeyError(f"TS: no tool {tool_id!r}")
        return record

    # -- lifecycle ----------------------------------------------------------

    def propose(self, spec: Any) -> Dict[str, Any]:
        """Register a proposed tool.  Returns the tool record."""
        spec_dict = spec if isinstance(spec, dict) else {"spec": spec}
        tool_id = spec_dict.get("tool_id") or str(uuid.uuid4())
        now = self._now()
        spec_json = json.dumps(spec_dict, sort_keys=True, ensure_ascii=False)
        self._db.execute(
            "INSERT OR REPLACE INTO tools"
            " (tool_id, state, spec_json, result_json, created_at, updated_at)"
            " VALUES (?, 'proposed', ?, NULL, ?, ?)",
            (tool_id, spec_json, now, now),
        )
        self._db.commit()
        self._log("propose", tool_id, {"spec": spec_dict})
        return self._get(tool_id)  # type: ignore[return-value]

    def build_caged(self, spec: Any) -> Dict[str, Any]:
        """Build (or transition an existing proposed tool) to caged state."""
        spec_dict = spec if isinstance(spec, dict) else {"spec": spec}
        tool_id = spec_dict.get("tool_id")
        if tool_id and self._get(tool_id) is not None:
            self._update(tool_id, state="caged")
        else:
            record = self.propose(spec)
            tool_id = record["tool_id"]
            self._update(tool_id, state="caged")
        self._log("build_caged", tool_id, {})
        return self._get(tool_id)  # type: ignore[return-value]

    def test(self, tool: Any) -> Dict[str, Any]:
        """Run (scaffolded) tests on a caged tool.  Returns test result dict."""
        tool_id = (
            tool.get("tool_id") if isinstance(tool, dict) else str(tool)
        )
        self._require_tool(tool_id)
        result = {"passed": True, "failures": [], "tool_id": tool_id}
        result_json = json.dumps(result, ensure_ascii=False)
        self._update(tool_id, state="tested", result_json=result_json)
        self._log("test", tool_id, {"result": result})
        return result

    def promote(self, tool: Any, *, approved_by: Optional[str] = None) -> Dict[str, Any]:
        """Transition to promoted.  HUMAN_GATE — approved_by is required."""
        if approved_by is None:
            raise PermissionError("TS.promote is HUMAN_GATE (approved_by required)")
        tool_id = (
            tool.get("tool_id") if isinstance(tool, dict) else str(tool)
        )
        record = self._require_tool(tool_id)
        if record["state"] not in ("tested", "quarantined"):
            raise ValueError(
                f"TS: cannot promote from state {record['state']!r} "
                f"(expected 'tested' or 'quarantined')"
            )
        self._update(tool_id, state="promoted", quarantine_reason=None)
        self._log("promote", tool_id, {"approved_by": approved_by})
        return self._get(tool_id)  # type: ignore[return-value]

    def quarantine(self, tool_id: str, reason: str) -> None:
        """Move tool to quarantined state (requires per-invocation approval until recovered)."""
        self._require_tool(tool_id)
        self._update(tool_id, state="quarantined", quarantine_reason=reason)
        self._log("quarantine", tool_id, {"reason": reason})

    def unquarantine(self, tool_id: str, *, approved_by: Optional[str] = None) -> None:
        """Lift quarantine back to 'tested'.  HUMAN_GATE — approved_by is required."""
        if approved_by is None:
            raise PermissionError("TS.unquarantine is HUMAN_GATE (approved_by required)")
        record = self._require_tool(tool_id)
        if record["state"] != "quarantined":
            raise ValueError(
                f"TS: tool {tool_id!r} is not quarantined (state={record['state']!r})"
            )
        self._update(tool_id, state="tested", quarantine_reason=None)
        self._log("unquarantine", tool_id, {"approved_by": approved_by})

    def deprecate(self, tool_id: str) -> None:
        """Mark a tool as deprecated (soft-delete)."""
        self._require_tool(tool_id)
        self._update(tool_id, state="deprecated")
        self._log("deprecate", tool_id, {})

    # -- query --------------------------------------------------------------

    def list_tools(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return tool records, optionally filtered by state."""
        if state:
            rows = self._db.execute(
                "SELECT * FROM tools WHERE state=? ORDER BY created_at",
                (state,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM tools ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        return self._get(tool_id)
