"""PM — Preference Model. Separates enduring preferences from goals and identity.

Source of truth for "how the owner likes things done". LS may NOT encode a
preference as a constitution rule; preference-shaped LS proposals route here.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..base import unbuilt


class PreferenceModel:
    ORGAN = "PM"

    def get(self, scope: str | None = None) -> List[Dict[str, Any]]:
        raise unbuilt(self.ORGAN, "get")

    def add(self, preference: Dict[str, Any], provenance: str) -> None:
        raise unbuilt(self.ORGAN, "add")

    def applies(self, action: Any) -> List[Dict[str, Any]]:
        raise unbuilt(self.ORGAN, "applies")

    def check(self, output: Any) -> Dict[str, List[str]]:
        """-> {respected:[id], violated:[id]}"""
        raise unbuilt(self.ORGAN, "check")
