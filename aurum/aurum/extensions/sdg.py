"""SDG — Skill Dependency Graph (extends Skill-CI). Cross-skill interference guard.

On patch/promote of a skill, re-run the golden scenarios of every dependent skill;
promotion blocked on any red, including transitive dependents.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import unbuilt


class SkillDependencyGraph:
    ORGAN = "SDG"

    def deps(self, skill: str) -> List[str]:
        raise unbuilt(self.ORGAN, "deps")

    def affected(self, change: object) -> List[str]:
        raise unbuilt(self.ORGAN, "affected")

    def regress(self, affected: List[str]) -> Dict[str, bool]:
        raise unbuilt(self.ORGAN, "regress")
