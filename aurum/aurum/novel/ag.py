"""AG — Authority Governor. Authority as a live, damped control variable.

Computes current authority from TL/EG/HVP/CB/OI. Maps to bands with hysteresis
(dual promote/demote thresholds + dwell). Recovery kinetics: falls fast, rises
slowly, floor prevents collapse, gain capped per window. PK enforces the ceiling;
AG never grants.
"""
from __future__ import annotations

from typing import Any, Dict, TypedDict

from ..base import unbuilt
from ..types import AuthorityBand


class Kinetics(TypedDict):
    rise_rate: float
    fall_rate: float
    floor: float
    max_gain_per_window: float


class AuthorityGovernor:
    ORGAN = "AG"

    def authority(self, capability_class: str) -> float:
        raise unbuilt(self.ORGAN, "authority")

    def band(self, capability_class: str) -> AuthorityBand:
        raise unbuilt(self.ORGAN, "band")

    def permits(self, action: Any) -> bool:
        raise unbuilt(self.ORGAN, "permits")

    def explain(self, capability_class: str) -> Dict[str, Any]:
        raise unbuilt(self.ORGAN, "explain")

    def kinetics(self) -> Kinetics:
        raise unbuilt(self.ORGAN, "kinetics")
