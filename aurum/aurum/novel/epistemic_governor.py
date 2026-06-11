"""EG — Epistemic Governor. Internal-state reroute before errors compound.

U is a COMPOSITE of five OBSERVABLE signals (never a model self-rating), each bounded
[0,1] with weights summing to 1.0. The two historically-sparse components
(tool_failure_rate, historical_failure_similarity) are EWMA-smoothed so a one-off blip
doesn't whipsaw U; instantaneous signals (verifier_disagreement, retrieval_conflict,
policy_ambiguity) enter directly. On U crossing threshold EG freeze_and_branches from the
last good step (a reroute, NOT a halt — that's CB). calibrate() fits weights against EL
failure history and is VALIDATED against a held-out partition; an overfit set is rejected
and the prior weights kept. Branch decisions + component breakdowns are logged to EL.

The raw per-step signals are supplied by the caller (computed from HVP vote spread, BB
similarity, PK ambiguity, tool stats) — EG is the composition/control layer over them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..types import EGComponent, EGScore

_COMPONENTS = ("verifier_disagreement", "retrieval_conflict", "tool_failure_rate",
               "policy_ambiguity", "historical_failure_similarity")
# only the historically-sparse components are smoothed; the rest enter directly.
_SPARSE = ("tool_failure_rate", "historical_failure_similarity")
_DEFAULT_WEIGHTS: Dict[str, float] = {c: 1.0 / len(_COMPONENTS) for c in _COMPONENTS}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def _normalize(weights: Dict[str, float]) -> Dict[str, float]:
    """Enforce Σwᵢ = 1.0 (a calibration that doesn't renormalise is a bug)."""
    total = sum(max(0.0, w) for w in weights.values())
    if total <= 0:
        return dict(_DEFAULT_WEIGHTS)
    return {c: max(0.0, weights.get(c, 0.0)) / total for c in _COMPONENTS}


class EpistemicGovernor:
    ORGAN = "EG"

    def __init__(self, el: Any = None, cg: Any = None,
                 weights: Optional[Dict[str, float]] = None,
                 threshold: float = 0.6, ewma_alpha: float = 0.4) -> None:
        self._el, self._cg = el, cg
        self.weights = _normalize(weights or dict(_DEFAULT_WEIGHTS))
        self.threshold = threshold
        self.ewma_alpha = ewma_alpha
        self._ewma: Dict[str, float] = {}  # per-sparse-component running value

    # -- smoothing ----------------------------------------------------------
    def smooth_sparse(self, component: str, raw_value: float) -> float:
        raw = _clamp01(raw_value)
        if component not in _SPARSE:
            return raw  # instantaneous signal enters directly
        prev = self._ewma.get(component)
        val = raw if prev is None else self.ewma_alpha * raw + (1 - self.ewma_alpha) * prev
        self._ewma[component] = val
        return val

    # -- composite uncertainty (STATELESS — deterministic, idempotent) ------
    def _replay_components(self, steps: List[dict]) -> Dict[str, float]:
        """Fold the sparse-component EWMA from a CLEAN baseline over `steps` and return the LAST
        step's components. Pure: it does NOT touch self._ewma. This is the fix for the
        re-score-mutates-state bug — U is now a deterministic function of the trajectory prefix,
        so re-scoring (freeze_and_branch / should_branch / audit) never double-folds the EWMA."""
        ewma: Dict[str, float] = {}
        comps: Dict[str, float] = {c: 0.0 for c in _COMPONENTS}
        for s in steps:
            comps = {}
            for c in _COMPONENTS:
                raw = _clamp01(s.get(c, 0.0))
                if c in _SPARSE:
                    prev = ewma.get(c)
                    raw = raw if prev is None else self.ewma_alpha * raw + (1 - self.ewma_alpha) * prev
                    ewma[c] = raw
                comps[c] = raw
        return comps

    def _u_series(self, steps: List[dict]) -> List[float]:
        """Per-step U over the trajectory, EWMA replayed once from baseline (stateless)."""
        ewma: Dict[str, float] = {}
        series: List[float] = []
        for s in steps:
            comps: Dict[str, float] = {}
            for c in _COMPONENTS:
                raw = _clamp01(s.get(c, 0.0))
                if c in _SPARSE:
                    prev = ewma.get(c)
                    raw = raw if prev is None else self.ewma_alpha * raw + (1 - self.ewma_alpha) * prev
                    ewma[c] = raw
                comps[c] = raw
            series.append(_clamp01(sum(self.weights[c] * comps[c] for c in _COMPONENTS)))
        return series

    def score_step(self, step: dict, traj: dict) -> EGScore:
        """U + components for `step`. If `step` is in `traj["steps"]`, the sparse EWMA is replayed
        over the trajectory up to (and including) it for context; otherwise the step is scored
        standalone. STATELESS — never mutates self._ewma (so repeated scoring is idempotent)."""
        steps = (traj or {}).get("steps") or []
        prefix = steps[:steps.index(step) + 1] if step in steps else [step]
        comps = self._replay_components(prefix)
        U = sum(self.weights[c] * comps[c] for c in _COMPONENTS)
        return {"U": _clamp01(U), "components": comps}  # type: ignore[return-value]

    def should_branch(self, traj: dict) -> bool:
        if "U" in traj:
            return traj["U"] >= self.threshold
        steps = traj.get("steps", [])
        if not steps:
            return False
        return self._u_series(steps)[-1] >= self.threshold

    # -- reroute (fork from last good step, NOT a halt) --------------------
    def freeze_and_branch(self, traj: dict) -> List[Any]:
        steps = traj.get("steps", [])
        # last high-confidence step = lowest-U step (U replayed deterministically from baseline)
        series = self._u_series(steps)
        good_idx = min(range(len(series)), key=lambda i: series[i]) if series else 0
        tip = len(steps) - 1 if steps else 0
        branch_a = {"kind": "continue", "from_step": tip}
        branch_b = {"kind": "alternate", "from_step": good_idx}
        self._audit_branch(traj, good_idx)
        return [branch_a, branch_b]

    def select(self, branches: List[Any]) -> Any:
        """Pick the branch that avoids the poisoned trajectory (lowest projected U)."""
        return min(branches, key=lambda b: b.get("U", b.get("projected_U", 1.0)))

    # -- calibration (validated against a holdout) -------------------------
    def calibrate(self, history: Optional[List[Dict[str, Any]]] = None, *,
                  human_ratified: bool = False) -> Dict[EGComponent, float]:
        """Fit w1..w5 from EL failure history (samples of {components, failed}) so the components
        that preceded failures carry more weight; VALIDATE on a holdout and REJECT (keep prior
        weights) if it doesn't generalise.

        M4 HARD CONSTRAINT — PROPOSE-ONLY by default: the system MUST NOT auto-apply a weight/
        threshold change. A self-tuning loss function optimises toward 'whatever fires least' — the
        OPPOSITE of the declared direction; evidence finds the optimum GIVEN the human's direction,
        it never CHOOSES the loss function. So calibrate returns the PROPOSED weights and applies
        them ONLY when human_ratified=True (the propose-then-ratify asymmetry, same as #104)."""
        samples = history if history is not None else self._history_from_el()
        if len(samples) < 4:
            return dict(self.weights)  # not enough evidence — keep prior
        mid = len(samples) // 2
        fit, holdout = samples[:mid], samples[mid:]
        candidate = self._fit_weights(fit)
        if self._predictive_power(candidate, holdout) <= self._predictive_power(
                self.weights, holdout):
            return dict(self.weights)  # overfit / no generalisation — reject (a no-op proposal)
        if human_ratified:             # APPLY only on explicit human ratification
            self.weights = candidate
            self._audit_calibrate(candidate)
        return dict(candidate)         # the PROPOSAL (applied above iff ratified)  # type: ignore[return-value]

    @staticmethod
    def _fit_weights(samples: List[Dict[str, Any]]) -> Dict[str, float]:
        # weight each component by how much higher it ran before failures vs successes.
        fails = [s["components"] for s in samples if s.get("failed")]
        oks = [s["components"] for s in samples if not s.get("failed")]
        raw: Dict[str, float] = {}
        for c in _COMPONENTS:
            mf = sum(d.get(c, 0.0) for d in fails) / len(fails) if fails else 0.0
            mo = sum(d.get(c, 0.0) for d in oks) / len(oks) if oks else 0.0
            raw[c] = max(0.0, mf - mo)
        return _normalize(raw)

    @staticmethod
    def _predictive_power(weights: Dict[str, float], samples: List[Dict[str, Any]]
                          ) -> float:
        # mean-U(failures) - mean-U(successes): higher = U separates failure better.
        def u(d: Dict[str, float]) -> float:
            return sum(weights[c] * d.get(c, 0.0) for c in _COMPONENTS)
        fails = [u(s["components"]) for s in samples if s.get("failed")]
        oks = [u(s["components"]) for s in samples if not s.get("failed")]
        mf = sum(fails) / len(fails) if fails else 0.0
        mo = sum(oks) / len(oks) if oks else 0.0
        return mf - mo

    def _history_from_el(self) -> List[Dict[str, Any]]:
        if self._el is None:
            return []
        out = []
        for e in self._el.query({"source_organ": "EG", "limit": 10000}):
            p = e.get("payload", {})
            if "components" in p and "failed" in p:
                out.append({"components": p["components"], "failed": p["failed"]})
        return out

    # -- audit --------------------------------------------------------------
    def _audit_branch(self, traj: dict, good_idx: int) -> None:
        if self._el is None:
            return
        steps = traj.get("steps", [])
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "EG",
            "action_type": "BRANCH", "object_ids": [str(traj.get("id", "traj"))],
            "payload": {"capability_class": "epistemic", "from_good_step": good_idx,
                        "components": self._replay_components(steps)},
            "evidence_confidence": 1.0, "evidence_source": "EG",
            "prev_hash": "", "hash": ""})

    def _audit_calibrate(self, weights: Dict[str, float]) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "EG",
            "action_type": "TRUST_CHANGE", "object_ids": ["eg_weights"],
            "payload": {"capability_class": "epistemic", "weights": weights},
            "evidence_confidence": 1.0, "evidence_source": "EG",
            "prev_hash": "", "hash": ""})
