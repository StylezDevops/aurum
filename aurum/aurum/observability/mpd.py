"""MPD — Memory Poisoning Detector.

DERIVED VIEW over EL (not a first-class organ): no independent state, no authority,
never auto-deletes — flags for review only. Catches slow corpus poisoning (recorded
success contradicted by later OI verdicts, suspiciously uniform confidence clusters)
by reading EL/BB. Complements PK's ingest-time injection guard. Runs passively from
the moment EL is live.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..base import unbuilt


class MemoryPoisoningDetector:
    ORGAN = "MPD"

    def scan(self) -> List[str]:
        raise unbuilt(self.ORGAN, "scan")

    def explain(self, evidence_id: str) -> Dict[str, Any]:
        """-> {signature, contradicting_events}"""
        raise unbuilt(self.ORGAN, "explain")
