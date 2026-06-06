"""AG — Authority Governor. Authority as a live, damped control variable.

Makes execution authority a runtime SCALAR (not a fixed tier) computed from live signals
(TL tier, EG uncertainty, HVP pass rate, CB freeze, OI outcome trend). The scalar maps to
bands via HYSTERESIS — each boundary has separate promote/demote thresholds plus a promotion
dwell time — so an authority hovering near a line (0.81/0.79/...) does NOT flap the band
(AURUM_ERR_010). RECOVERY KINETICS damp the OI↔AG feedback loop: authority falls fast
(safety, no dwell), rises slowly and rate-limited (earn it back), and never collapses below a
FLOOR (escape the death spiral).

AG is READ-ONLY over its inputs and never grants — it computes a ceiling that PK enforces.
`observe()` is the per-cycle update tick; `set_authority()` is the kinetics-free primitive it
builds on (and the substrate for the hysteresis tests). Authority changes are logged to EL.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TypedDict

from ..types import AuthorityBand


class Kinetics(TypedDict):
    rise_rate: float
    fall_rate: float
    floor: float
    max_gain_per_window: float


# bands high -> low: (name, promote_at, demote_at). advisory is the floor band.
_BANDS: List[Tuple[str, float, float]] = [
    ("full", 0.95, 0.90),
    ("code", 0.80, 0.70),
    ("readonly", 0.60, 0.50),
    ("advisory", 0.0, 0.0),
]
_RANK = {name: i for i, (name, _, _) in enumerate(reversed(_BANDS))}  # advisory=0..full=3
# action_class -> the minimum band it requires
_ACTION_BAND = {"commit_outward": "full", "code_edit": "code", "propose": "readonly",
                "advise": "advisory"}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


class AuthorityGovernor:
    ORGAN = "AG"

    def __init__(self, el: Any = None, max_tl_tier: int = 3,
                 kinetics: Optional[Kinetics] = None, dwell_seconds: float = 60.0) -> None:
        self._el = el
        self.max_tl_tier = max_tl_tier
        self.dwell_seconds = dwell_seconds
        self._k: Kinetics = kinetics or {"rise_rate": 0.05, "fall_rate": 1.0,
                                         "floor": 0.1, "max_gain_per_window": 0.2}
        self._authority: Dict[str, float] = {}
        self._band_idx: Dict[str, int] = {}     # index into _BANDS (0=full)
        self._last_promote: Dict[str, float] = {}
        self._signals: Dict[str, Dict[str, Any]] = {}
        # Environment provenance (structural, 2026-06-06): which evidence domains
        # contributed authority for a class. RECORDED + EXPOSED, NOT YET ACTED ON —
        # environment is metadata on the authority record, never an input to the
        # scoring (that per-environment computation is the deferred ALM/calibration
        # work). Plumbed in now so the dev-vs-prod distinction is recoverable from the
        # first authority record instead of retrofitted onto a provenance-blind scalar.
        self._earned_in: Dict[str, List[str]] = {}  # capability_class -> ordered uniq envs

    # -- reads --------------------------------------------------------------
    def authority(self, capability_class: str) -> float:
        return self._authority.get(capability_class, self._k["floor"])

    def band(self, capability_class: str) -> AuthorityBand:
        idx = self._band_idx.get(capability_class, len(_BANDS) - 1)
        return _BANDS[idx][0]  # type: ignore[return-value]

    def permits(self, action: Any) -> bool:
        """True iff the current band for the action's class clears the band the action
        requires. AG computes the ceiling; PK is what actually enforces it."""
        cc = action.get("capability_class", "default")
        required = action.get("required_band") \
            or _ACTION_BAND.get(action.get("action_class", "advise"), "advisory")
        return _RANK[self.band(cc)] >= _RANK[required]

    def explain(self, capability_class: str) -> Dict[str, Any]:
        sig = self._signals.get(capability_class, {})
        return {"authority": self.authority(capability_class),
                "band": self.band(capability_class), "signals": sig,
                "contributions": self._contributions(sig),
                "earned_in": self.earned_in(capability_class)}

    def earned_in(self, capability_class: str) -> List[str]:
        """Provenance: the environments that have contributed authority for this class.

        RECORDED, NOT YET ACTED ON. v1 tags authority with its environment and stores
        it; it does NOT change the computed authority based on environment (that — the
        prod-resets-to-floor / per-environment value — is the ALM/calibration work).
        Per-environment keyed authority (`authority(class, env)`) is deferred; this read
        is the provenance plumbing so the distinction is recoverable later.
        """
        return list(self._earned_in.get(capability_class, []))

    def kinetics(self) -> Kinetics:
        return dict(self._k)  # type: ignore[return-value]

    # -- update tick --------------------------------------------------------
    def observe(self, capability_class: str, signals: Dict[str, Any],
                now: Optional[float] = None, environment: Optional[str] = None) -> float:
        """Compute a target authority from live signals and move toward it under the
        recovery kinetics (fast fall, slow rate-limited rise, floor). Returns new authority.

        `environment` is PROVENANCE ONLY — it tags which evidence domain this update came
        from; it does NOT enter the scoring (target/kinetics are unchanged by it)."""
        self._signals[capability_class] = signals
        target = self._target(signals)
        cur = self.authority(capability_class)
        if target < cur:
            new = max(self._k["floor"], cur - min(cur - target, self._k["fall_rate"]))
        else:
            gain = min(target - cur, self._k["rise_rate"], self._k["max_gain_per_window"])
            new = cur + gain
        self.set_authority(capability_class, new, now, environment=environment)
        return self.authority(capability_class)

    def set_authority(self, capability_class: str, value: float,
                      now: Optional[float] = None,
                      environment: Optional[str] = None) -> None:
        """Kinetics-free authority set + band re-evaluation with hysteresis/dwell.

        `environment` is recorded as provenance (NEVER affects `value` or the band —
        that's the frozen scoring). Unresolved provenance is recorded as 'unknown',
        never silently 'prod' (fail-safe: unknown should be treated as LESS trusted)."""
        env = self._resolve_env(environment)
        value = max(self._k["floor"], _clamp01(value))
        prev = self._authority.get(capability_class)
        self._authority[capability_class] = value
        self._update_band(capability_class, value, now)
        self._record_env(capability_class, env)
        if prev != value:
            self._audit(capability_class, value, env)

    # -- provenance helpers (additive; never touch scoring) -----------------
    @staticmethod
    def _resolve_env(environment: Optional[str]) -> str:
        # Fail-safe direction: unresolved provenance is 'unknown', NEVER 'prod'.
        # Unknown provenance should later be treated as LESS trusted, not more.
        return environment if environment else "unknown"

    def _record_env(self, capability_class: str, env: str) -> None:
        lst = self._earned_in.setdefault(capability_class, [])
        if env not in lst:
            lst.append(env)

    # -- internals ----------------------------------------------------------
    def _target(self, s: Dict[str, Any]) -> float:
        c = self._contributions(s)
        base = sum(c.values()) / len(c) if c else 0.0
        if s.get("cb_frozen"):
            base = min(base, 0.3)  # a CB freeze demotes growth authority
        return _clamp01(base)

    def _contributions(self, s: Dict[str, Any]) -> Dict[str, float]:
        return {
            "tl": _clamp01(s.get("tl_tier", 0) / self.max_tl_tier),
            "eg": _clamp01(1.0 - s.get("eg_uncertainty", 0.0)),
            "hvp": _clamp01(s.get("hvp_pass_rate", 1.0)),
            "oi": _clamp01(s.get("oi_trend", 1.0)),
        }

    def _update_band(self, cc: str, authority: float, now: Optional[float]) -> None:
        idx = self._band_idx.get(cc, len(_BANDS) - 1)
        # demotion: immediate, no dwell (safety reactions stay instant)
        while idx < len(_BANDS) - 1 and authority < _BANDS[idx][2]:
            idx += 1
        # promotion: a single promotion EVENT (gated once by dwell) may raise one or
        # more bands; demotion above already ran without dwell.
        if idx > 0 and authority >= _BANDS[idx - 1][1]:
            t = 0.0 if now is None else now
            last = self._last_promote.get(cc)
            dwell_ok = (now is None or self.dwell_seconds <= 0 or last is None
                        or (t - last) >= self.dwell_seconds)
            if dwell_ok:
                while idx > 0 and authority >= _BANDS[idx - 1][1]:
                    idx -= 1
                self._last_promote[cc] = t
        self._band_idx[cc] = idx

    def _audit(self, cc: str, authority: float, environment: str = "unknown") -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "AG",
            "action_type": "TRUST_CHANGE", "object_ids": [cc],
            "payload": {"capability_class": cc, "authority": authority,
                        "band": self.band(cc),
                        "environment": environment,
                        "earned_in": list(self._earned_in.get(cc, [])),
                        "contributions": self._contributions(self._signals.get(cc, {}))},
            "evidence_confidence": 1.0, "evidence_source": "AG",
            "prev_hash": "", "hash": ""})
