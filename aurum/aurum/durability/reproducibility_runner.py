"""RR — Reproducibility Runner. Git-checkout for reasoning states; pairs with EL.

Guarantees DECISION replay (the recorded reasoning is reconstructed from the exact
context EL captured), NOT ENVIRONMENT replay — a dead external API can't be resurrected.
Every replay is labelled with `environment_fidelity` so determinism is never overpromised.

Pulled forward per the build order: replay reconstructs from whatever the producing
organ recorded in the EL event payload (inputs, tool_versions, pk_version, ls_version,
verifier votes) plus the governance snapshot linked via the decisions table. As TS/PK/LS
versioning matures, producers record richer context and replay sharpens automatically —
RR reads the ledger, it does not need those organs built.

Side-effect-free (spec accept c): replay reads EL and reproduces the decision; it appends
NOTHING (the "replays logged as RR events" audit needs an RR ELActionType the frozen type
set doesn't yet define — deferred rather than smuggled onto an unrelated action_type).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, TypedDict

from ..types import EnvironmentFidelity


class ReplayRun(TypedDict):
    run: Any
    environment_fidelity: EnvironmentFidelity


class ReproducibilityRunner:
    ORGAN = "RR"

    def __init__(self, el: Any = None,
                 api_checker: Optional[Callable[[str], bool]] = None) -> None:
        # Pairs with EL (read-only). api_checker(api_id)->bool lets a caller report
        # whether a recorded external dependency is still live; absent it, historical
        # external state is treated as unrecoverable (fidelity downgrades, never lies).
        self._el = el
        self._api_checker = api_checker

    # -- EL access (read-only, through EL's public surface) ----------------
    def _event(self, event_id: str) -> Dict[str, Any]:
        if self._el is None:
            raise RuntimeError("RR pairs with EL; an EvidenceLedger is required")
        ev = self._el.get_event(event_id)
        if ev is None:
            raise KeyError(f"RR: no EL event {event_id!r}")
        return ev

    def _linked_decision(self, seq: int) -> Optional[Dict[str, Any]]:
        return self._el.decision_for_el_seq(seq)

    # -- public API ---------------------------------------------------------
    def context(self, event_id: str) -> Dict[str, Any]:
        """-> {inputs, tool_versions, pk_version, ls_version, votes, ...} reconstructed
        from the EL event payload plus the governance snapshot linked via decisions."""
        ev = self._event(event_id)
        p = ev["payload"]
        ctx: Dict[str, Any] = {
            "event_id": event_id, "action_type": ev["action_type"],
            "object_ids": ev["object_ids"], "timestamp": ev["timestamp"],
            "inputs": p.get("inputs"),
            "tool_versions": p.get("tool_versions", {}),
            "pk_version": p.get("pk_version"),
            "ls_version": p.get("ls_version"),
            "votes": p.get("votes", []),
        }
        dec = self._linked_decision(ev["seq"])
        if dec is not None:
            ctx["decision"] = dec
        return ctx

    def _fidelity(self, payload: Dict[str, Any]) -> EnvironmentFidelity:
        explicit = payload.get("environment_fidelity")
        if explicit in ("full", "partial", "unavailable"):
            return explicit  # producer already labelled it
        ext = payload.get("external_apis") or []
        if not ext:
            return "full"  # no external dependency — fully reproducible
        if self._api_checker is not None:
            live = [bool(self._api_checker(a)) for a in ext]
            if all(live):
                return "full"
            return "partial" if any(live) else "unavailable"
        return "unavailable"  # historical external state can't be resurrected

    def replay(self, event_id: str) -> ReplayRun:
        """Reconstruct and deterministically reproduce a past decision. Read-only and
        side-effect-free; the decision reasoning is reproduced even when the environment
        is gone (fidelity then reports 'unavailable')."""
        ev = self._event(event_id)
        ctx = self.context(event_id)
        decision = ctx["decision"]["final_decision"] if "decision" in ctx \
            else ev["payload"].get("decision")
        run = {"event_id": event_id, "reproduced": True,
               "decision": decision, "context": ctx}
        return {"run": run, "environment_fidelity": self._fidelity(ev["payload"])}

    @staticmethod
    def _as_context(x: Dict[str, Any]) -> Dict[str, Any]:
        # accept a ReplayRun ({"run": {"context": ...}}), a run ({"context": ...}),
        # or a raw context dict.
        if "context" in x:
            return x["context"]
        run = x.get("run")
        if isinstance(run, dict) and "context" in run:
            return run["context"]
        return x

    def diff(self, run_a: Any, run_b: Any) -> Dict[str, Any]:
        """Which versioned components changed between two runs (or two contexts)."""
        ca = self._as_context(run_a)
        cb = self._as_context(run_b)
        changes: Dict[str, Any] = {}
        for field in ("inputs", "tool_versions", "pk_version", "ls_version", "votes"):
            if ca.get(field) != cb.get(field):
                changes[field] = {"a": ca.get(field), "b": cb.get(field)}
        da, db = ca.get("decision") or {}, cb.get("decision") or {}
        for field in ("final_decision", "authority_score", "active_rules",
                      "active_goals", "knowledge_state_hash"):
            if da.get(field) != db.get(field):
                changes.setdefault("decision", {})[field] = {
                    "a": da.get(field), "b": db.get(field)}
        return changes

    def verify_pointer(self, object_id: str, cs: Any) -> bool:
        """Stale-pointer guard: before re-executing a historical step, confirm the
        artifact survived (still a node in CS). False => the trajectory must force a full
        replan, not a blind retry. No CS => cannot confirm survival => False (fail safe)."""
        if cs is None:
            return False
        node_ids = {n["node_id"] for n in cs.graph().get("nodes", [])}
        return object_id in node_ids
