"""OI — Outcome Interpreter. Task completion != goal satisfaction.

Judges outcome quality against the originating goal + preferences. A completed-
but-unsatisfying outcome does NOT raise trust and is written to BB as a learning
case. Carries operational-effectiveness (the over-caution / governance-cost guard).
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from ..base import unbuilt


class OutcomeVerdict(TypedDict):
    completed: bool
    satisfied: bool
    quality: float
    satisfaction_source: str   # "proxy" | "human"
    signals: Dict[str, Any]


class Effectiveness(TypedDict):
    ratio: float
    completion: float
    satisfaction: float
    overhead: float
    interruptions: float
    latency: float


class OutcomeInterpreter:
    ORGAN = "OI"

    def interpret(self, task_result: Any, goal_id: str) -> OutcomeVerdict:
        raise unbuilt(self.ORGAN, "interpret")

    def trend(self, capability_class: str) -> Any:
        raise unbuilt(self.ORGAN, "trend")

    def effectiveness(self) -> Effectiveness:
        raise unbuilt(self.ORGAN, "effectiveness")

    def sample_for_human(self, rate: float) -> List[str]:
        """Async delayed sampling of completed tasks for owner ground-truth."""
        raise unbuilt(self.ORGAN, "sample_for_human")

    def record_human_verdict(self, task_id: str, verdict: Any) -> None:
        raise unbuilt(self.ORGAN, "record_human_verdict")

    def proxy_calibration(self) -> Dict[str, Any]:
        """-> {proxy_vs_human_agreement}. Down-weight proxy if it diverges."""
        raise unbuilt(self.ORGAN, "proxy_calibration")
