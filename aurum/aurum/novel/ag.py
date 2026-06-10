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
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TypedDict

from ..types import AuthorityBand
from .familiarity import FamiliarityLedger


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
# Land just under a band's demote line so a single bad outcome drops exactly one band.
_DEMOTE_EPSILON = 1e-3


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def _effective_band(value: float) -> str:
    """STATELESS band partition for a derived/transient authority value (the effective
    authority after the familiarity multiplier). [0.90,1]→full, [0.70,0.90)→code,
    [0.50,0.70)→readonly, [0,0.50)→advisory. Deliberately NO hysteresis/dwell: those exist to
    stop a STORED value flapping (AURUM_ERR_010); the effective ceiling is recomputed fresh per
    gate check from its inputs, so it is deterministic and a clean partition is correct here."""
    for name, _promote, demote in _BANDS:
        if value >= demote:
            return name
    return _BANDS[-1][0]  # advisory (demote 0.0) — unreachable since value ≥ 0


def _band_index_for(value: float) -> int:
    """Index into _BANDS for the band a value REPRESENTS (stateless partition, mirrors
    _effective_band). Used by restore_authority so rehydration reconstructs the earned band
    rather than climbing from advisory on the promote thresholds."""
    for idx, (_name, _promote, demote) in enumerate(_BANDS):
        if value >= demote:
            return idx
    return len(_BANDS) - 1


class AuthorityGovernor:
    ORGAN = "AG"

    def __init__(self, el: Any = None, max_tl_tier: int = 3,
                 kinetics: Optional[Kinetics] = None, dwell_seconds: float = 60.0,
                 familiarity: Optional[FamiliarityLedger] = None) -> None:
        self._el = el
        self.max_tl_tier = max_tl_tier
        self.dwell_seconds = dwell_seconds
        self._k: Kinetics = kinetics or {"rise_rate": 0.05, "fall_rate": 1.0,
                                         "floor": 0.1, "max_gain_per_window": 0.2}
        self._authority: Dict[str, float] = {}
        self._band_idx: Dict[str, int] = {}     # index into _BANDS (0=full)
        self._last_promote: Dict[str, float] = {}
        self._signals: Dict[str, Dict[str, Any]] = {}
        # Familiarity factor (domain-keyed): effective authority = base × familiarity, applied
        # at the point of USE (the gate/ceiling check), NEVER inside the frozen scoring — so
        # AURUM_ERR_010 (no flapping of the stored band) and the frozen-scoring guarantees stay
        # intact. The ledger is a projection of the EL grounded-outcome stream (rehydrated on boot).
        self._familiarity = familiarity if familiarity is not None else FamiliarityLedger()
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

    def permits(self, action: Any, *, now: Optional[float] = None,
                validity: Optional[float] = None, volatility: Optional[str] = None) -> bool:
        """True iff the band for the action's class clears the band the action requires. AG
        computes the ceiling; PK/kernel enforces it.

        Familiarity is OPT-IN per action: when the caller supplies `validity` (the domain's
        current KVE validity) the EFFECTIVE band (base × familiarity, domain from
        `action['domain']`) is enforced — stricter, since familiarity ≤ 1. With no familiarity
        inputs (validity None), the BASE band is used (back-compatible: existing callers and the
        non-domain-scoped path are unchanged)."""
        cc = action.get("capability_class", "default")
        required = action.get("required_band") \
            or _ACTION_BAND.get(action.get("action_class", "advise"), "advisory")
        if validity is None:
            return _RANK[self.band(cc)] >= _RANK[required]
        eff = self.effective_band(cc, action.get("domain", "unknown"),
                                  now=now if now is not None else 0.0,
                                  validity=validity, volatility=volatility)
        return _RANK[eff] >= _RANK[required]

    # -- familiarity factor (domain-keyed; applied at point of use, never in scoring) -------
    def record_familiarity(self, domain: str, observed_at: float) -> None:
        """Record one human-grounded-good outcome in a domain. MUST be called only from the
        grounded path (proxy success never builds familiarity — the forbidden-loop guard)."""
        self._familiarity.record(domain, observed_at)

    def replay_familiarity(self, records: Any) -> None:
        """Rebuild the familiarity projection from durable (domain, observed_at) records on
        boot. Public so the kernel rehydrates without reaching into AG internals."""
        self._familiarity.replay(records)

    def familiarity_factor(self, domain: str, *, now: float, validity: float,
                           volatility: Optional[str]) -> float:
        return self._familiarity.factor(domain, now=now, validity=validity,
                                        volatility=volatility)

    def effective_authority(self, capability_class: str, domain: str, *, now: float,
                            validity: float, volatility: Optional[str]) -> float:
        """base_authority(class) × familiarity_factor(domain). base comes from the FROZEN
        scoring untouched; familiarity is a separate multiplier applied here, at use."""
        return self.authority(capability_class) * self._familiarity.factor(
            domain, now=now, validity=validity, volatility=volatility)

    def effective_band(self, capability_class: str, domain: str, *, now: float,
                       validity: float, volatility: Optional[str]) -> AuthorityBand:
        eff = self.effective_authority(capability_class, domain, now=now,
                                       validity=validity, volatility=volatility)
        return _effective_band(eff)  # type: ignore[return-value]

    def familiarity_effective_n(self, domain: str, *, now: float, validity: float,
                                volatility: Optional[str]) -> float:
        """The decay/validity-weighted experience count for a domain — for explain/tests."""
        return self._familiarity.effective_n(domain, now=now, validity=validity,
                                             volatility=volatility)

    def explain(self, capability_class: str) -> Dict[str, Any]:
        """Explain the current authority for a class. Beyond the live signals/contributions,
        `evidence` is the ordered ledger trail of every TRUST_CHANGE for this class (from→to,
        band, and the cause that moved it) — so the current level traces back through every
        prior level to the outcome/evidence that produced it. Cold-start (no ledger, or a
        class with no history) yields `evidence: []` — a clean no-op, never a fabricated trail.
        The durable source of truth is that append-only TRUST_CHANGE stream; the in-memory
        authority is a projection of it, never a separately-stored mutable scalar."""
        sig = self._signals.get(capability_class, {})
        return {"authority": self.authority(capability_class),
                "band": self.band(capability_class), "signals": sig,
                "contributions": self._contributions(sig),
                "earned_in": self.earned_in(capability_class),
                "evidence": self._evidence_trail(capability_class)}

    def _evidence_trail(self, capability_class: str) -> List[Dict[str, Any]]:
        """Ordered (oldest→newest) TRUST_CHANGE trail for a class, read from AG's own ledger.
        Each entry links an authority level to the cause that produced it. Returns [] when
        there is no ledger or no history (cold-start clean no-op); never raises."""
        if self._el is None:
            return []
        try:
            rows = self._el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE",
                                   "capability_class": capability_class, "limit": 1_000_000})
        except Exception:
            return []
        trail = [{"from": (p := ev.get("payload") or {}).get("prev_authority"),
                  "to": p.get("authority"), "band": p.get("band"),
                  "environment": p.get("environment"), "cause": p.get("cause"),
                  "timestamp": ev.get("timestamp")}
                 for ev in rows]
        trail.reverse()  # query returns newest-first; present oldest→newest
        return trail

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

    def distribution(self) -> Dict[str, Any]:
        """Authority distribution telemetry: per-class {authority, band} plus a band histogram,
        over every class AG currently tracks. Read-only; for the governance dashboard."""
        per_class = {cc: {"authority": self.authority(cc), "band": self.band(cc)}
                     for cc in sorted(self._authority)}
        histogram: Dict[str, int] = {name: 0 for name, _p, _d in _BANDS}
        for info in per_class.values():
            histogram[info["band"]] = histogram.get(info["band"], 0) + 1
        return {"classes": per_class, "band_histogram": histogram,
                "tracked_classes": len(per_class)}

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
                      environment: Optional[str] = None,
                      cause: Optional[Dict[str, Any]] = None) -> None:
        """Kinetics-free authority set + band re-evaluation with hysteresis/dwell.

        `environment` is recorded as provenance (NEVER affects `value` or the band —
        that's the frozen scoring). Unresolved provenance is recorded as 'unknown',
        never silently 'prod' (fail-safe: unknown should be treated as LESS trusted).
        `cause` is the WHY (the triggering outcome + its classification + evidence refs):
        recorded in the TRUST_CHANGE so a replay reconstructs *why* authority moved, not
        just *that* it did."""
        env = self._resolve_env(environment)
        value = max(self._k["floor"], _clamp01(value))
        prev = self._authority.get(capability_class)
        self._authority[capability_class] = value
        self._update_band(capability_class, value, now)
        self._record_env(capability_class, env)
        if prev != value:
            self._audit(capability_class, value, env, prev=prev, cause=cause)

    def apply_outcome(self, capability_class: str, *, good: bool, grounded: bool,
                      environment: Optional[str] = None, severity: str = "task",
                      cause: Optional[Dict[str, Any]] = None) -> float:
        """Outcome-driven authority move — the OI→AG loop primitive. ASYMMETRIC BY DESIGN
        (see outcome_gated_authority_design.md); the asymmetry is the whole safety of it:

          bad outcome  → DEMOTE, immediately, NO grounding required (a reflex — contraction
                         is always safe). SEVERITY-TIERED: a TASK failure ("confidence too
                         high") drops ONE band (repeated ones compound); a GOVERNANCE failure
                         (a "must never" that happened — credential exfil / tenant breach /
                         constitutional) drops to the FLOOR immediately. Some categories you
                         cannot afford to learn about gradually.
          good outcome → PROMOTE only if `grounded` (out-of-loop / human-confirmed), and
                         then only by a small capped step (slow rise via existing kinetics).
                         A good-but-UNGROUNDED (proxy "it worked") outcome NEVER promotes —
                         returns unchanged. This is the forbidden-feedback-loop guard:
                         success the agent itself reports must not widen its own authority.

        Uses the existing band structure + recovery kinetics; does NOT alter the multi-signal
        scoring (_target/_contributions) or the kinetics constants — additive outcome path.
        Returns the resulting authority for the class."""
        cur = self.authority(capability_class)
        if not good:
            if severity == "governance":
                # a "must never" breach that nonetheless happened — not a confidence
                # update, an attempt at the forbidden. FLOOR immediately, not one band.
                # (The architecture is trend-over-spike EXCEPT here: some categories you
                # cannot afford to learn about gradually.)
                self.set_authority(capability_class, self._k["floor"],
                                   environment=environment, cause=cause)
            else:
                # TASK failure — "our confidence was too high": a one-band Bayesian nudge.
                idx = self._band_idx.get(capability_class, len(_BANDS) - 1)
                demote_to = _BANDS[idx][2] - _DEMOTE_EPSILON  # just under this band's demote line
                self.set_authority(capability_class, min(cur, demote_to),
                                   environment=environment, cause=cause)
            return self.authority(capability_class)
        if not grounded:
            return cur  # CONSTITUTIONAL: never promote on ungrounded/proxy success
        gain = min(self._k["rise_rate"], self._k["max_gain_per_window"])
        self.set_authority(capability_class, cur + gain, environment=environment, cause=cause)
        return self.authority(capability_class)

    def restore_authority(self, capability_class: str, value: float,
                          earned_in: Optional[List[str]] = None) -> None:
        """Rehydration projection — set authority for a class from DURABLE history WITHOUT
        re-auditing. The value's provenance already lives in EL (it was logged when first
        set); replaying it on boot must NOT write phantom TRUST_CHANGE events. Used only by
        the kernel's verify_chain-gated rehydration, never on the live path. Does not alter
        the scoring; just rebuilds the in-memory authority projection from the ledger."""
        value = max(self._k["floor"], _clamp01(value))
        self._authority[capability_class] = value
        # Restore the band the value REPRESENTS (the stateless partition, same as _effective_band),
        # NOT the promote-from-advisory path of _update_band. _update_band starts a fresh class at
        # advisory and only climbs on the higher PROMOTE thresholds, so a value resting in a
        # hysteresis GAP (e.g. exactly where a task-failure demote lands it) would be restored a
        # band LOWER than it earned — silently forgetting standing on every --rm.
        self._band_idx[capability_class] = _band_index_for(value)
        if earned_in:
            self._earned_in[capability_class] = list(earned_in)

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

    def _audit(self, cc: str, authority: float, environment: str = "unknown",
               prev: Optional[float] = None, cause: Optional[Dict[str, Any]] = None) -> None:
        if self._el is None:
            return
        payload: Dict[str, Any] = {
            "capability_class": cc, "authority": authority,
            "prev_authority": prev,        # the WHY chain: before -> after ...
            "band": self.band(cc),
            "environment": environment,
            "earned_in": list(self._earned_in.get(cc, [])),
            "contributions": self._contributions(self._signals.get(cc, {}))}
        if cause is not None:
            payload["cause"] = cause       # ... triggered by which outcome + classification + evidence
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "AG",
            "action_type": "TRUST_CHANGE", "object_ids": [cc],
            "payload": payload,
            "evidence_confidence": 1.0, "evidence_source": "AG",
            "prev_hash": "", "hash": ""})
