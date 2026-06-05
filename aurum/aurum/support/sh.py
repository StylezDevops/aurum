"""SH — Shadow Mode. Simulated execution + diff before commit (no side effects)."""
from __future__ import annotations

from typing import Any

from ..base import unbuilt


class ShadowMode:
    ORGAN = "SH"

    def simulate(self, action: Any) -> Any:
        raise unbuilt(self.ORGAN, "simulate")

    def commit(self, action: Any) -> Any:  # gated by verdict
        raise unbuilt(self.ORGAN, "commit")
