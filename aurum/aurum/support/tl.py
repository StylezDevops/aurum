"""TL — Trust Ladder. Earned autonomy, auto-fed from HVP/TS/BB/OI.

TL tier is ONE input to AG, not the final authority word — the action-vs-scope
split. Tier-up within ceilings is automatic; high-risk ceilings need HUMAN_GATE.
Tier-down is automatic and immediate.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_POSITIVE = {"success", "satisfied", "pass", "passed", "completed_satisfied"}
_NEGATIVE = {"failure", "fail", "error", "dissatisfied", "regression", "reverted", "rolled_back"}


class TrustLadder:
    """Earned-autonomy tiers per capability, asymmetric by design:
      • tier-UP requires GROUNDED evidence and is capped at the capability's ceiling — proxy-only
        success never raises a tier (the promote-slow / grounded rule, mirroring OI→AG);
      • tier-DOWN is immediate on ANY negative signal (demote-fast), proxy or grounded;
      • crossing the ceiling is HUMAN_GATE (`grant`).
    `tier` is ONE input to AG, never the final word: `can()` is a pure tier check (SCOPE); AG holds
    live authority (the action-vs-scope split — a high tier does NOT by itself permit an action)."""

    ORGAN = "TL"

    def __init__(self, *, default_ceiling: int = 2, max_tier: int = 3,
                 ceilings: Optional[Dict[str, int]] = None) -> None:
        self._tier: Dict[str, int] = {}
        self._default_ceiling = int(default_ceiling)
        self._max_tier = int(max_tier)
        self._ceilings: Dict[str, int] = dict(ceilings or {})

    def ceiling(self, capability: str) -> int:
        return min(self._ceilings.get(capability, self._default_ceiling), self._max_tier)

    def tier(self, capability: str) -> int:
        return self._tier.get(str(capability), 0)

    def can(self, action: Any) -> bool:
        """Pure TIER check (scope): earned tier >= the action's required_tier. ONE input to AG —
        True here does NOT by itself permit the action; AG's live authority is the real ceiling."""
        if not isinstance(action, dict):
            return False
        return self.tier(action.get("capability", "")) >= int(action.get("required_tier", 1))

    def ingest(self, metric_event: Any) -> None:
        """Auto-fed from HVP/TS/BB/OI. GROUNDED positive → tier-up by 1, capped at the ceiling.
        Negative (any source) → tier-down by 1, immediate. Proxy-only positive → NO change."""
        if not isinstance(metric_event, dict):
            return
        cap = metric_event.get("capability")
        if not cap:
            return
        cap = str(cap)
        outcome = str(metric_event.get("outcome", "")).lower()
        cur = self.tier(cap)
        if outcome in _NEGATIVE:
            self._tier[cap] = max(cur - 1, 0)                      # demote-fast: immediate, any source
        elif outcome in _POSITIVE and bool(metric_event.get("grounded", False)):
            self._tier[cap] = min(cur + 1, self.ceiling(cap))      # promote-slow: grounded only, capped
        # proxy-positive / unknown outcome: deliberately no change (autonomy is EARNED, not assumed)

    def grant(self, capability: str, evidence: Any, *, approved_by: Optional[str] = None) -> None:
        """Manual override to cross a high-risk ceiling — HUMAN_GATE (`approved_by` required). Sets
        the tier to `evidence['tier']` (or ceiling+1), capped at max_tier. The ONLY way past the
        auto ceiling; capability growth like this is exactly what pauses on owner absence / freeze."""
        if approved_by is None:
            raise PermissionError(
                "TL.grant is HUMAN_GATE: crossing a high-risk ceiling requires approved_by")
        cap = str(capability)
        if isinstance(evidence, dict) and isinstance(evidence.get("tier"), int):
            target = evidence["tier"]
        else:
            target = self.ceiling(cap) + 1
        self._tier[cap] = max(0, min(target, self._max_tier))
