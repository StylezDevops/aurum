"""TCM — Tool Catalog Manager. Manages tool-ecosystem growth.

Capability taxonomy over promoted tools; detects overlap, recommends domain
extension (AA calls recommend_domain pre-synthesis), serves selection by
capability not flat list.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..base import unbuilt


class ToolCatalogManager:
    ORGAN = "TCM"

    def check_overlap(self, tool_or_spec: Any) -> Dict[str, Any]:
        """-> {overlaps:[tool_id], recommend: build|merge|reuse}"""
        raise unbuilt(self.ORGAN, "check_overlap")

    def recommend_domain(self, gap: Any) -> Dict[str, Any]:
        """-> {domain:str, extend: tool_id|None}. Used by AA.should_bundle."""
        raise unbuilt(self.ORGAN, "recommend_domain")

    def taxonomy(self) -> Any:
        raise unbuilt(self.ORGAN, "taxonomy")

    def select(self, capability: str) -> List[str]:
        raise unbuilt(self.ORGAN, "select")

    def retirement_candidates(self) -> List[str]:
        raise unbuilt(self.ORGAN, "retirement_candidates")
