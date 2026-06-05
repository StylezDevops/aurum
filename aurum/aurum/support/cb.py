"""CB — Circuit Breaker. External-anomaly trips + capability freeze.

freeze_growth("stop evolution, keep operation"): halts AA synth / TS promote /
LS revision / TL tier-up for a class while existing tools keep running.
"""
from __future__ import annotations

from typing import Any

from ..base import unbuilt


class CircuitBreaker:
    ORGAN = "CB"

    def trip(self, signal: Any) -> None:
        raise unbuilt(self.ORGAN, "trip")

    def state(self, capability: str) -> Any:
        raise unbuilt(self.ORGAN, "state")

    def reset(self) -> None:  # HUMAN_GATE
        raise unbuilt(self.ORGAN, "reset")

    def freeze_growth(self, capability_class: str) -> None:  # HUMAN_GATE
        raise unbuilt(self.ORGAN, "freeze_growth")

    def unfreeze_growth(self, capability_class: str) -> None:  # HUMAN_GATE
        raise unbuilt(self.ORGAN, "unfreeze_growth")

    def is_frozen(self, capability_class: str) -> bool:
        raise unbuilt(self.ORGAN, "is_frozen")
