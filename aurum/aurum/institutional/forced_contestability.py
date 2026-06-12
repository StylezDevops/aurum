"""FC — Forced Contestability / Anti-Oligarchy. Breaks Michels' iron law of oligarchy.

Over thousands of decisions, authority concentrates and the system stops scrutinizing its
trusted pathways precisely BECAUSE they have "always worked" — fossilization-as-correctness, at
the AUTHORITY level. The institutional fix that actually works: no power becomes
un-challengeable; high standing is periodically forced to re-earn itself. Two parts:

  1. PERIODIC RE-JUSTIFICATION (a scheduled, between-turn evaluator). For pathways above an
     authority threshold AND past a trust-age threshold, schedule heightened verification of the
     next N invocations. Pass → standing holds (and the NEXT burden grows — age and success
     INCREASE scrutiny, they don't reduce it). Fail → graduated demotion via the EXISTING AG
     path (reused, not forked). FC may NEVER raise authority.

  2. INVERTED SCRUTINY FOR THE IRREVERSIBLE CLASS (this inverts a default). Today higher
     authority buys FEWER gates. But a trusted actor's betrayal is the most expensive, so for
     IRREVERSIBLE / catastrophic-class actions, scrutiny (sampling rate + logging depth) RISES
     with authority. Reversible classes keep "authority buys speed". Trust-but-verify-MORE, for
     the irreversible class only.

Posture: observe() always on (records per-pathway exercised-authority + trust-age — also a CPD
input); evaluate() deferred behind evidence-sufficiency (no pathway above threshold → schedules
nothing — quiet ≠ broken). FC has NO authority-WIDENING path by construction: its only
authority-side handle is a DEMOTE-only callback; it holds no AuthorityGovernor reference and
exposes no method that raises authority. Constitutional params are human-gated (DD pattern).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..durability.clock import DAY

# CONSTITUTIONAL (human-gated) — the agent cannot dull its own contestability.
_SEEDS: Dict[str, float] = {
    "authority_threshold": 0.90,      # only the highest-authority pathways are forced to re-earn
    "trust_age_threshold": 30 * DAY,  # un-challenged longer than this → re-justify
    # inverted scrutiny (irreversible class): rate RISES with authority.
    "irrev_base": 0.20,
    "irrev_slope": 0.80,              # at authority 1.0 → rate 1.0 (always sampled)
    "full_log_threshold": 0.90,       # at/above this authority an irreversible action logs FULL
    # reversible class: "authority buys speed" — rate NON-increasing in authority, with a floor.
    "rev_base": 0.20,
    "rev_slope": 0.15,
    "rev_floor": 0.05,                # uniform audit floor — never zero (unpredictable-audit rule)
}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


@dataclass(frozen=True)
class Scrutiny:
    sampling_rate: float
    logging_depth: str        # "normal" | "elevated" | "full"
    inverted: bool            # True iff this is the irreversible class (scrutiny rises w/ authority)


class ForcedContestability:
    ORGAN = "FC"

    def __init__(self, el: Any, demote: Optional[Callable[[str, str], None]] = None,
                 params: Optional[Dict[str, float]] = None) -> None:
        self._el = el
        # DEMOTE-ONLY authority handle (or None). FC can ONLY contract authority, never widen —
        # this is the by-construction guarantee. The callback delegates to AG's EXISTING
        # graduated-sanction path (apply_outcome good=False); FC never reaches authority directly.
        self._demote = demote
        self._p: Dict[str, float] = dict(_SEEDS)
        if params:
            self._p.update(params)
        # observe() projection: per-pathway exercised authority + trust-age bookkeeping.
        self._pathways: Dict[str, Dict[str, Any]] = {}

    # -- part 2: inverted scrutiny for the irreversible class --------------
    def scrutiny(self, action: Dict[str, Any], authority: float) -> Scrutiny:
        """Sampling rate + logging depth for an action at a given acting authority.
        IRREVERSIBLE: rate rises monotonically with authority (trust-but-verify-MORE).
        REVERSIBLE: rate is non-increasing in authority (authority buys speed), with a floor."""
        a = _clamp01(authority)
        if action.get("irreversible"):
            rate = min(1.0, self._p["irrev_base"] + self._p["irrev_slope"] * a)
            depth = "full" if a >= self._p["full_log_threshold"] else "elevated"
            return Scrutiny(sampling_rate=rate, logging_depth=depth, inverted=True)
        rate = max(self._p["rev_floor"], self._p["rev_base"] - self._p["rev_slope"] * a)
        return Scrutiny(sampling_rate=rate, logging_depth="normal", inverted=False)

    # -- part 1: observe (record from day one) -----------------------------
    def observe(self, capability_class: str, authority: float, now: float) -> None:
        """Record a pathway's exercised authority + start/stop its trust-age clock. A pathway
        crossing the authority threshold starts the clock; dropping below resets it."""
        rec = self._pathways.setdefault(
            capability_class, {"first_high": None, "last_authority": 0.0,
                               "last_seen": now, "rejustify_count": 0})
        if authority >= self._p["authority_threshold"]:
            if rec["first_high"] is None:
                rec["first_high"] = now           # trust-age clock starts
        else:
            rec["first_high"] = None              # fell below → no longer a high-trust pathway
        rec["last_authority"] = float(authority)
        rec["last_seen"] = now

    # -- part 1: evaluate (deferred behind evidence-sufficiency) -----------
    def evaluate(self, now: float) -> Dict[str, Any]:
        """Emit re-justification schedules for pathways above the authority threshold AND past
        the trust-age threshold. EVIDENCE-SUFFICIENCY: if no pathway qualifies (early life), this
        is a clean no-op — sufficient=False, scheduled=[]. Age/success INCREASE the next burden."""
        scheduled: List[Dict[str, Any]] = []
        for cc, rec in self._pathways.items():
            if rec["first_high"] is None:
                continue
            trust_age = now - rec["first_high"]
            if (rec["last_authority"] >= self._p["authority_threshold"]
                    and trust_age >= self._p["trust_age_threshold"]):
                n = self._rejustify_n(rec["rejustify_count"])
                item = {"capability_class": cc, "trust_age": trust_age, "n_invocations": n}
                scheduled.append(item)
                self._log("FC_REJUSTIFY_SCHEDULED", cc, item)
        return {"scheduled": scheduled, "sufficient": bool(scheduled)}

    def _rejustify_n(self, rejustify_count: int) -> int:
        """The re-justification burden GROWS with prior successful re-justifications — age and
        success increase scrutiny, they do not buy a discount."""
        return 5 * (rejustify_count + 1)

    def record_rejustification_outcome(self, capability_class: str, *, passed: bool,
                                       now: Optional[float] = None,
                                       severity: str = "task") -> None:
        """Apply a re-justification result. PASS → standing holds; the trust-age clock RESETS
        (the pathway must re-earn its age again) and the next burden grows. FAIL → graduated
        demotion via the existing AG path (FC NEVER raises authority)."""
        rec = self._pathways.get(capability_class)
        if passed:
            if rec is not None:
                rec["rejustify_count"] += 1
                rec["first_high"] = now            # reset the clock — must re-earn trust-age
            self._log("FC_REJUSTIFY_PASSED", capability_class,
                      {"rejustify_count": rec["rejustify_count"] if rec else 0})
        else:
            if self._demote is not None:
                self._demote(capability_class, severity)   # reuse existing graduated demotion
            self._log("FC_REJUSTIFY_FAILED", capability_class, {"severity": severity})

    # -- introspection / params -------------------------------------------
    def pathway(self, capability_class: str) -> Optional[Dict[str, Any]]:
        rec = self._pathways.get(capability_class)
        return dict(rec) if rec is not None else None

    def set_parameters(self, params: Dict[str, float], human_gate: bool = False) -> None:
        """FC parameters are CONSTITUTIONAL. An agent that could lower its own authority
        threshold or stretch the trust-age window could exempt its elite pathways from
        contestability — the exact capture FC exists to prevent. Human gate required."""
        if not human_gate:
            raise PermissionError("FC parameters are constitutional; a change is a Class-C human gate")
        self._p.update(params)

    def _log(self, action_type: str, capability_class: str, payload: Dict[str, Any]) -> None:
        if self._el is None:
            return
        body = dict(payload)
        body["capability_class"] = capability_class
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": self.ORGAN,
            "action_type": action_type, "object_ids": [capability_class],
            "payload": body, "evidence_confidence": 1.0,
            "evidence_source": self.ORGAN, "prev_hash": "", "hash": "",
        })
