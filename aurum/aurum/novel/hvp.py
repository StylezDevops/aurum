"""HVP — Heterogeneous Verifier Panel. Checks homework across independent models.

Every verifier (and the main model) is an OpenAI-compatible endpoint. Independence
is judged by FAMILY (prior), corrected by measured agreement-on-errors. Aspect
verifiers return booleans. ROI-instrumented so verification doesn't become a tax.
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from ..base import unbuilt
from ..types import EndpointEntry, Stakes


class RouteCheck(TypedDict):
    payload_hash: str
    is_sensitive: bool
    stakes: Stakes
    required_aspects: List[str]


class FamilyROI(TypedDict):
    false_positive_rate: float
    marginal_benefit: float
    cost: float
    keep: bool


class FamilyCorrelation(TypedDict):
    agreement_on_errors: float
    effectively_independent: bool


class HeterogeneousVerifierPanel:
    ORGAN = "HVP"

    def configure(self, roster: List[EndpointEntry]) -> None:
        raise unbuilt(self.ORGAN, "configure")

    def add_endpoint(self, entry: EndpointEntry) -> None:
        raise unbuilt(self.ORGAN, "add_endpoint")

    def route(self, check: RouteCheck) -> List[EndpointEntry]:
        raise unbuilt(self.ORGAN, "route")

    def call(self, entry: EndpointEntry, messages: Any) -> Any:
        raise unbuilt(self.ORGAN, "call")

    def verify(self, output: str, aspects: List[str]) -> Dict[str, bool]:
        raise unbuilt(self.ORGAN, "verify")

    def aggregate(self, results: Any, policy: Any) -> Any:
        raise unbuilt(self.ORGAN, "aggregate")

    def roi(self, family: str) -> FamilyROI:
        raise unbuilt(self.ORGAN, "roi")

    def correlation(self, family_a: str, family_b: str,
                    domain: str | None = None) -> FamilyCorrelation:
        """Measured agreement-on-errors. With domain set, returns per-domain
        correlation (families may collude on one domain, be independent on another)."""
        raise unbuilt(self.ORGAN, "correlation")
