"""CG — Cost Governor. Cost-aware model routing under budget."""
from __future__ import annotations

from typing import Any

from ..base import unbuilt


class CostGovernor:
    ORGAN = "CG"

    def route(self, step: Any) -> str:
        raise unbuilt(self.ORGAN, "route")

    @property
    def budget(self) -> float:
        raise unbuilt(self.ORGAN, "budget")

    def spend(self, amount: float) -> None:
        raise unbuilt(self.ORGAN, "spend")
