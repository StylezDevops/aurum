"""GR — Goal Registry. Covers WHY the agent acts. Goals decay (health).

active() is filtered by health; low-health goals stop attracting resources before
formal expiry. Goals tied to rare-but-critical workflows are protected.
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from ..base import unbuilt


class GoalHealth(TypedDict):
    score: float
    importance: float
    progress: float
    recent_activity: float
    owner_interest: float


class GoalRegistry:
    ORGAN = "GR"

    def add(self, goal: Dict[str, Any]) -> str:
        raise unbuilt(self.ORGAN, "add")

    def get(self, goal_id: str) -> Dict[str, Any]:
        raise unbuilt(self.ORGAN, "get")

    def active(self) -> List[Dict[str, Any]]:
        """Filtered by health threshold."""
        raise unbuilt(self.ORGAN, "active")

    def expire(self, goal_id: str) -> None:
        raise unbuilt(self.ORGAN, "expire")

    def depends(self, goal_id: str) -> List[str]:
        raise unbuilt(self.ORGAN, "depends")

    def health(self, goal_id: str) -> GoalHealth:
        raise unbuilt(self.ORGAN, "health")

    def touch(self, goal_id: str) -> None:
        raise unbuilt(self.ORGAN, "touch")
