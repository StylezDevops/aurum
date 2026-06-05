"""RR — Reproducibility Runner. Git-checkout for reasoning states; pairs with EL.

Guarantees DECISION replay (reasoning reproduces given the same context). Does NOT
guarantee ENVIRONMENT replay — a dead external API can't be resurrected. Every
replay is labelled with environment_fidelity so determinism is never overpromised.
"""
from __future__ import annotations

from typing import Any, Dict, TypedDict

from ..base import unbuilt
from ..types import EnvironmentFidelity


class ReplayRun(TypedDict):
    run: Any
    environment_fidelity: EnvironmentFidelity


class ReproducibilityRunner:
    ORGAN = "RR"

    def replay(self, event_id: str) -> ReplayRun:
        raise unbuilt(self.ORGAN, "replay")

    def diff(self, run_a: Any, run_b: Any) -> Any:
        raise unbuilt(self.ORGAN, "diff")

    def context(self, event_id: str) -> Dict[str, Any]:
        raise unbuilt(self.ORGAN, "context")

    def verify_pointer(self, object_id: str, cs: Any) -> bool:
        """Stale-pointer guard: before re-executing a historical step, confirm the
        artifact survived (cs.is_leased still holds / node exists). False => the
        trajectory must force a full replan, not a blind retry."""
        raise unbuilt(self.ORGAN, "verify_pointer")
