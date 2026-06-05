"""EG — Epistemic Governor. Internal-state reroute before errors compound.

U is a composite of five OBSERVABLE signals (never model self-rating), bounded
[0,1] with weights summing to 1.0. calibrate() fits weights against EL failure
history and is validated against a holdout failure set to avoid overfitting.
freeze_and_branch forks from the last good step rather than halting.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..base import unbuilt
from ..types import EGComponent, EGScore


class EpistemicGovernor:
    ORGAN = "EG"

    def score_step(self, step: dict, traj: dict) -> EGScore:
        # Sparse historical components (tool_failure_rate, historical_failure_
        # similarity) must be EWMA-smoothed before entering U to avoid low-volume
        # oscillation; instantaneous signals enter directly. See smooth_sparse.
        raise unbuilt(self.ORGAN, "score_step")

    def smooth_sparse(self, component: str, raw_value: float) -> float:
        """EWMA / time-decay smoothing for sparse historical signals so a one-off
        blip doesn't whipsaw U and trigger needless freeze_and_branch."""
        raise unbuilt(self.ORGAN, "smooth_sparse")

    def should_branch(self, traj: dict) -> bool:
        raise unbuilt(self.ORGAN, "should_branch")

    def freeze_and_branch(self, traj: dict) -> List[Any]:
        raise unbuilt(self.ORGAN, "freeze_and_branch")

    def select(self, branches: List[Any]) -> Any:
        raise unbuilt(self.ORGAN, "select")

    def calibrate(self) -> Dict[EGComponent, float]:
        """Fit weights vs EL failure history; reject if it fails the holdout."""
        raise unbuilt(self.ORGAN, "calibrate")
