"""SM — Substrate Mapper (cross-domain). Scopes self-improvement to the right substrate.

Label work -> skill/prompt edits; infra work -> tool/policy edits. Proposals
targeting an unmapped substrate are rejected pre-gate.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# domain -> valid modification primitives. The self-improvement substrate DIFFERS by domain:
# label work edits skill text + prompt structure; infra work edits tool code + policy rules.
_DEFAULT_MAP: Dict[str, List[str]] = {
    "label": ["skill", "prompt"],
    "infra": ["tool", "policy"],
}


class SubstrateMapper:
    """Maps a task domain to the modification primitives that are valid there, and scopes (or
    rejects) self-improvement proposals against that map — so evolution can't target the wrong
    substrate (the DGM/SICA failure where self-improvement assumes task-domain == modification-
    substrate). An unmapped domain is rejected PRE-GATE; a proposal whose target primitive isn't in
    the domain's substrate is a cross-substrate reject."""

    ORGAN = "SM"

    def __init__(self, mapping: Optional[Dict[str, List[str]]] = None) -> None:
        self._map: Dict[str, List[str]] = {d: list(p) for d, p in (mapping or _DEFAULT_MAP).items()}

    def substrate(self, domain: str) -> List[str]:
        """Valid modification primitives for `domain` (empty if the domain is unmapped)."""
        return list(self._map.get(domain, []))

    def scope(self, proposal: Any, domain: str) -> Dict[str, Any]:
        """-> scoped_proposal | reject. Rejects an unmapped domain pre-gate; rejects a proposal
        whose declared target primitive is outside the domain's substrate (cross-substrate);
        otherwise returns the proposal confined to the mapped substrate."""
        prims = self.substrate(domain)
        if not prims:
            return {"scoped": False, "domain": domain,
                    "reason": f"unmapped substrate for domain {domain!r} — rejected pre-gate"}
        target = None
        if isinstance(proposal, dict):
            target = proposal.get("target") or proposal.get("substrate")
        if target is not None and target not in prims:
            return {"scoped": False, "domain": domain, "substrate": prims,
                    "reason": (f"proposal targets {target!r} but domain {domain!r} substrate is "
                               f"{prims} (cross-substrate) — rejected")}
        return {"scoped": True, "domain": domain, "substrate": prims,
                "target": target, "proposal": proposal}
