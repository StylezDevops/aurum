"""KVE — Knowledge Validity Engine. Stored knowledge is not true forever.

Each artifact carries {confidence, last_verified, volatility_class} + provenance
{source_type, source_uri, observed_at, verified_at}. FAST-volatility artifacts
(external APIs, cloud flows) decay and are re-verified or quarantined before they
contaminate planning.
"""
from __future__ import annotations

from typing import Dict, List, TypedDict

from ..base import unbuilt
from ..types import VolatilityClass


class Provenance(TypedDict):
    source_type: str
    source_uri: str
    observed_at: str
    verified_at: str


class ReverifyResult(TypedDict):
    valid: bool
    confidence: float


class KnowledgeValidityEngine:
    ORGAN = "KVE"

    def classify(self, artifact: object) -> VolatilityClass:
        raise unbuilt(self.ORGAN, "classify")

    def confidence(self, artifact_id: str) -> float:
        raise unbuilt(self.ORGAN, "confidence")

    def provenance(self, artifact_id: str) -> Provenance:
        raise unbuilt(self.ORGAN, "provenance")

    def stale(self) -> List[str]:
        raise unbuilt(self.ORGAN, "stale")

    def reverify(self, artifact_id: str) -> ReverifyResult:
        raise unbuilt(self.ORGAN, "reverify")

    def invalidate(self, artifact_id: str) -> None:
        raise unbuilt(self.ORGAN, "invalidate")
