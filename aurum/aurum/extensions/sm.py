"""SM — Substrate Mapper (cross-domain). Scopes self-improvement to the right substrate.

Label work -> skill/prompt edits; infra work -> tool/policy edits. Proposals
targeting an unmapped substrate are rejected pre-gate.
"""
from __future__ import annotations

from typing import List

from ..base import unbuilt


class SubstrateMapper:
    ORGAN = "SM"

    def substrate(self, domain: str) -> List[str]:
        raise unbuilt(self.ORGAN, "substrate")

    def scope(self, proposal: object, domain: str) -> object:
        """-> scoped_proposal | reject"""
        raise unbuilt(self.ORGAN, "scope")
