"""IDM — Identity Drift Monitor.

DERIVED VIEW over EL (not a first-class organ): no independent state, no authority,
never blocks. Everything it reports is computable from EL + LS version history + RR
replay; it renders "how different am I now from six months ago?" on demand so the
drift signal is surfaced rather than merely derivable. First to defer under resource
constraints. Runs passively from the moment EL is live.
"""
from __future__ import annotations

from typing import Any, Dict

from ..base import unbuilt


class IdentityDriftMonitor:
    ORGAN = "IDM"

    def distance(self, version_a: Any, version_b: Any) -> float:
        raise unbuilt(self.ORGAN, "distance")

    def trend(self) -> Any:
        raise unbuilt(self.ORGAN, "trend")

    def report(self) -> Dict[str, Any]:
        """-> {current_vs_baseline, fastest_drifting_region}"""
        raise unbuilt(self.ORGAN, "report")
