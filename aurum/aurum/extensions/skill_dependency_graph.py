"""SDG — Skill Dependency Graph (extends Skill-CI). Cross-skill interference guard.

On patch/promote of a skill, re-run the golden scenarios of every dependent skill;
promotion blocked on any red, including transitive dependents.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set


class SkillDependencyGraph:
    """A dependency graph over skills + a regression guard. `affected(change)` returns the changed
    skill plus ALL its transitive DEPENDENTS (skills that reference it, directly or through a
    chain) — the set whose golden scenarios must re-run. `regress(affected)` runs each one's
    goldens via an injected runner → {skill: passed}; `promotion_allowed` is False on ANY red — the
    gate: promotion blocked on any red, including transitive dependents."""

    ORGAN = "SDG"

    def __init__(self, dependencies: Optional[Dict[str, List[str]]] = None,
                 golden_runner: Optional[Callable[[str], bool]] = None) -> None:
        self._deps: Dict[str, List[str]] = {}
        self._dependents: Dict[str, Set[str]] = {}
        self._runner = golden_runner
        for skill, deps in (dependencies or {}).items():
            self.add(skill, deps)

    def add(self, skill: str, deps: List[str]) -> None:
        """Register `skill` with its direct dependencies (skills/assumptions it builds on)."""
        self._deps[skill] = list(deps)
        for d in deps:
            self._deps.setdefault(d, [])                       # ensure the dep node exists
            self._dependents.setdefault(d, set()).add(skill)   # reverse edge: d <- skill

    def deps(self, skill: str) -> List[str]:
        return list(self._deps.get(skill, []))

    def affected(self, change: Any) -> List[str]:
        """The changed skill + all transitive DEPENDENTS — every skill whose goldens must re-run
        because it (transitively) depends on the changed one."""
        skill = str(change.get("skill") if isinstance(change, dict) else change)
        seen = {skill}
        q = deque([skill])
        while q:
            cur = q.popleft()
            for dependent in self._dependents.get(cur, ()):
                if dependent not in seen:
                    seen.add(dependent)
                    q.append(dependent)
        return sorted(seen)

    def regress(self, affected: List[str], *,
                runner: Optional[Callable[[str], bool]] = None) -> Dict[str, bool]:
        """Re-run each affected skill's golden scenarios → {skill: passed}. Needs a golden runner
        (`skill -> bool`); injectable per call or at construction."""
        run = runner or self._runner
        if run is None:
            raise RuntimeError("SDG.regress needs a golden_runner (skill -> bool: do its goldens pass?)")
        return {skill: bool(run(skill)) for skill in affected}

    @staticmethod
    def promotion_allowed(results: Dict[str, bool]) -> bool:
        """The gate: promotion is allowed only if EVERY affected skill's goldens are green."""
        return all(results.values())
