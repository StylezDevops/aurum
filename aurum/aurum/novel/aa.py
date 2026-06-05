"""AA — API Archaeologist. Extension-first tool acquisition.

Default posture: EXTEND an existing tool; creating a new one is the justified
exception. Discovery is budget-bounded. Synthesis stops at minimal coherent
capability surface, with on-demand path-depth expansion on missing-relation
errors. should_bundle (via TCM) runs first and short-circuits to extension.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict

from ..base import unbuilt


class DiscoveryBudget(TypedDict):
    max_tokens: int
    max_depth: int
    timeout_sec: int


class BundleDecision(TypedDict):
    extend: Optional[str]      # tool_id to extend, or None
    recommend: str             # "extend" | "create"


class APIArchaeologist:
    ORGAN = "AA"

    def __init__(self) -> None:
        self.discovery_budget: Optional[DiscoveryBudget] = None
        self.max_search_depth: int = 0
        self.max_candidate_specs: int = 0
        self.max_expansion_attempts: int = 0

    def detect_gap(self, task_ctx: Any) -> Optional[Any]:
        raise unbuilt(self.ORGAN, "detect_gap")

    def should_bundle(self, gap: Any) -> BundleDecision:
        """Pre-synthesis: extend an existing domain tool vs create new."""
        raise unbuilt(self.ORGAN, "should_bundle")

    def discover(self, gap: Any, budget: DiscoveryBudget) -> Optional[Any]:
        raise unbuilt(self.ORGAN, "discover")

    def synthesize(self, spec: Dict[str, Any], capability_surface: list[str]) -> Any:
        """Minimal coherent surface; on-demand depth expansion on missing relations."""
        raise unbuilt(self.ORGAN, "synthesize")

    def cage_test(self, server: Any) -> Any:
        raise unbuilt(self.ORGAN, "cage_test")
