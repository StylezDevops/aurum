"""TL — Trust Ladder. Earned autonomy, auto-fed from HVP/TS/BB/OI.

TL tier is ONE input to AG, not the final authority word — the action-vs-scope
split. Tier-up within ceilings is automatic; high-risk ceilings need HUMAN_GATE.
Tier-down is automatic and immediate.
"""
from __future__ import annotations

from typing import Any

from ..base import unbuilt


class TrustLadder:
    ORGAN = "TL"

    def tier(self, capability: str) -> int:
        raise unbuilt(self.ORGAN, "tier")

    def can(self, action: Any) -> bool:
        raise unbuilt(self.ORGAN, "can")

    def ingest(self, metric_event: Any) -> None:
        raise unbuilt(self.ORGAN, "ingest")

    def grant(self, capability: str, evidence: Any) -> None:  # HUMAN_GATE
        raise unbuilt(self.ORGAN, "grant")
