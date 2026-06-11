"""CG — Cost Governor. Cost-aware model routing under budget.

The budget primitive everything else draws on (build order #5): EG branch compute and
HVP verification both spend from this budget. Trivial steps route to cheap models, hard
steps to the top model — but routing degrades GRACEFULLY under budget pressure: as the
remaining budget shrinks, the max affordable tier is capped so the agent keeps operating
cheaply rather than overspending. Pure primitive: no EL writes, no authority, never blocks.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# cheap -> expensive. `levels` are the complexity labels a tier is the natural home for.
DEFAULT_TIERS: List[Dict[str, Any]] = [
    {"name": "cheap", "model": "anthropic/claude-haiku-4.5", "cost": 1.0,
     "levels": {"trivial", "routine", "low"}},
    {"name": "mid", "model": "anthropic/claude-sonnet-4.6", "cost": 5.0,
     "levels": {"moderate", "medium"}},
    {"name": "top", "model": "anthropic/claude-opus-4.8", "cost": 15.0,
     "levels": {"hard", "high", "high_stakes", "complex"}},
]


class CostGovernor:
    ORGAN = "CG"

    def __init__(self, budget: float = 100.0,
                 tiers: Optional[List[Dict[str, Any]]] = None) -> None:
        self._initial = float(budget)
        self._remaining = float(budget)
        self._tiers = tiers if tiers is not None else DEFAULT_TIERS
        # level -> index of its natural tier
        self._level_index: Dict[str, int] = {}
        for i, t in enumerate(self._tiers):
            for lvl in t.get("levels", ()):
                self._level_index[lvl] = i

    # -- routing ------------------------------------------------------------
    def route(self, step: Any) -> str:
        """Cost-aware model choice for a step, capped by remaining budget."""
        desired = self._desired_index(step)
        idx = min(desired, self._budget_cap_index())
        return self._tiers[idx]["model"]

    def _desired_index(self, step: Any) -> int:
        level = self._level(step)
        # unknown label defaults to the middle tier (or the top if only two exist)
        return self._level_index.get(level, min(1, len(self._tiers) - 1))

    def _level(self, step: Any) -> str:
        if isinstance(step, str):
            return step
        if isinstance(step, dict):
            if step.get("complexity"):
                return str(step["complexity"])
            d = step.get("difficulty")
            if isinstance(d, (int, float)):
                return "trivial" if d < 0.34 else "moderate" if d < 0.67 else "hard"
        return "moderate"

    def _budget_cap_index(self) -> int:
        """The most expensive tier allowed at the current budget ratio. Under pressure
        the cap drops so spending can't blow the budget."""
        top = len(self._tiers) - 1
        ratio = (self._remaining / self._initial) if self._initial > 0 else 0.0
        if ratio <= 0.10:
            return 0
        if ratio <= 0.30:
            return min(1, top)
        return top

    # -- budget -------------------------------------------------------------
    @property
    def budget(self) -> float:
        """Remaining budget."""
        return self._remaining

    def spend(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("CG.spend: amount must be non-negative")
        self._remaining = max(0.0, self._remaining - float(amount))
