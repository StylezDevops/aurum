"""SH — Shadow Mode. Simulated execution + diff before commit (no side effects).

The proper home for the shadow-containment we've applied ad hoc to gated irreversible actions:
`simulate(action)` runs the action's mutator against a deep COPY of the target state and returns a
diff (the real state is never touched); `commit(action)` is GATED BY VERDICT — it refuses unless
that action was just simulated with verdict 'ok', then applies the mutator for real and CONSUMES
the verdict (so a commit can't be replayed off one simulation). No simulate → no commit.

An `action` is `{"id", "state", "apply"}` where `apply(state)` mutates `state` in place (and may
raise — a sim that raises yields verdict 'error', which blocks commit). SH executes the REAL action
in simulation; CS (whatif/project) reasons about consequences WITHOUT executing — both are useful.
"""
from __future__ import annotations

import copy
from typing import Any, Dict


class ShadowMode:
    ORGAN = "SH"

    def __init__(self) -> None:
        self._verdicts: Dict[str, Dict[str, Any]] = {}

    def simulate(self, action: Any) -> Dict[str, Any]:
        """Run `action['apply']` on a deep copy of `action['state']` — NO side effects. Returns
        `{id, before, after, changed, verdict}` (verdict 'ok' if apply succeeded, else 'error' +
        the message). Records the verdict so a later commit of the same id can be gated on it."""
        aid = str(action.get("id", "")) if isinstance(action, dict) else ""
        apply = action.get("apply") if isinstance(action, dict) else None
        state = action.get("state") if isinstance(action, dict) else None
        before = copy.deepcopy(state)
        shadow = copy.deepcopy(state)
        verdict, error = "ok", None
        try:
            if callable(apply):
                apply(shadow)
        except Exception as e:  # noqa: BLE001 — a failing sim is a verdict, not a crash
            verdict, error, shadow = "error", f"{type(e).__name__}: {e}", before
        diff: Dict[str, Any] = {"id": aid, "before": before, "after": shadow,
                                "changed": shadow != before, "verdict": verdict}
        if error is not None:
            diff["error"] = error
        self._verdicts[aid] = diff
        return diff

    def commit(self, action: Any) -> Dict[str, Any]:
        """GATED BY VERDICT: refuses unless this action's id was just simulated with verdict 'ok'.
        On commit, applies the mutator to the REAL state (side effect) and CONSUMES the verdict —
        re-committing requires a fresh simulate."""
        aid = str(action.get("id", "")) if isinstance(action, dict) else ""
        rec = self._verdicts.get(aid)
        if rec is None:
            return {"committed": False, "reason": "not simulated — commit is gated by a verdict"}
        if rec["verdict"] != "ok":
            return {"committed": False, "reason": f"simulate verdict={rec['verdict']!r}"}
        apply = action.get("apply") if isinstance(action, dict) else None
        state = action.get("state") if isinstance(action, dict) else None
        # CONSUME the verdict BEFORE the real side effect. If apply() RAISES after partially firing
        # an irreversible effect, the exception must not leave a replayable 'ok' that a retry could
        # re-commit (double-firing the irreversible op) — no-replay must hold in exactly that case.
        del self._verdicts[aid]
        if callable(apply):
            apply(state)                       # real side effect, only after an OK simulation
        return {"committed": True, "state": state, "diff": rec}
