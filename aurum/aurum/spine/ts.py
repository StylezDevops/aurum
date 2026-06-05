"""TS — Toolsmith. Tier 0 spine (stubbed mock).

Governed tool lifecycle: propose -> build-in-cage -> test -> review -> promote
-> version -> QUARANTINE -> deprecate. Quarantine is the missing middle state for
a tool that isn't bad enough to delete but isn't trusted. promote is HUMAN_GATE.

Reconciliation (scaffold canonical): `tools/toolsmith.py` in the Hermes tree is working
prior art (propose -> scan -> sandbox-test -> staged; never auto-activates) and the live
implementation today. Migrate it INTO this organ as TS is built to spec, and keep it
running until then. `build_state.BUILT['TS']` stays False until this stub is spec-complete
— don't flip-to-True-and-wire the existing file.
"""
from __future__ import annotations

from typing import Any

from ..base import unbuilt


class Toolsmith:
    ORGAN = "TS"

    def propose(self, spec: Any) -> Any:
        raise unbuilt(self.ORGAN, "propose")

    def build_caged(self, spec: Any) -> Any:
        raise unbuilt(self.ORGAN, "build_caged")

    def test(self, tool: Any) -> Any:
        raise unbuilt(self.ORGAN, "test")

    def promote(self, tool: Any) -> Any:  # HUMAN_GATE
        raise unbuilt(self.ORGAN, "promote")

    def quarantine(self, tool_id: str, reason: str) -> None:
        raise unbuilt(self.ORGAN, "quarantine")

    def unquarantine(self, tool_id: str) -> None:  # HUMAN_GATE
        raise unbuilt(self.ORGAN, "unquarantine")

    def deprecate(self, tool_id: str) -> None:
        raise unbuilt(self.ORGAN, "deprecate")
