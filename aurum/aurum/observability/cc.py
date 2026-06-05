"""CC — Concentration Check.

DERIVED VIEW over EL (not a first-class organ): no independent state, no authority,
never blocks. Reads usage distribution from EL and flags any artifact servicing a
disproportionate share of workflows/writes/promotions as systemic risk. Feeds MGC
(do-not-retire) and TCM (harden-or-split). First to defer under resource constraints.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import unbuilt


class ConcentrationCheck:
    ORGAN = "CC"

    def concentration(self) -> Dict[str, float]:
        raise unbuilt(self.ORGAN, "concentration")

    def systemic_risks(self) -> List[str]:
        raise unbuilt(self.ORGAN, "systemic_risks")
