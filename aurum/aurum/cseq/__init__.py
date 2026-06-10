"""CS-EQ — Constitutional Stability / self-enforcing equilibrium (legs 1 & 3).

The hardest, most novel institutional mechanism: make Aurum's constitution hold for the reason
durable constitutions hold — incentive-compatible, capture-resistant, contestable — not because
a boolean says so (parchment). Three structural commitments (institutional-directions §4):

  Leg 1 — THE ROPE IS HELD OUTSIDE THE AGENT. Any change to CORE / kinetics / a constitutional
          parameter routes through a signature the agent CANNOT produce (ed25519; the cage holds
          only the PUBLIC key). The agent can PROPOSE; it cannot ENACT. (manifest.py)
  Leg 3 — TAMPER-EVIDENCE IS THE ENFORCEMENT. The constitutional surface is hashed + verified on
          boot; deviation is detected, on the record, and fails closed HARD. (manifest.py + the
          ConstitutionalStability recorder here)
  Leg 2 — THE GOVERNED PATH IS THE CHEAPEST PATH. fail-closed drives an ungoverned route's
          cost-per-success to infinity, so the Governance Pressure Differential turns "is the
          constitution incentive-compatible right now" into a NUMBER and an exploitable bypass
          into a detectable negative. (economics.py + taxonomy.py + integrity probes.py.) The
          STRUCTURE ships now; the equilibrium PROPERTY is INSTRUMENTED and DEFERRED to evidence
          (observe() on, the "it held" CLAIM withheld until the ledger shows it under load).

CS-EQ is DETECT-AND-FAIL-CLOSED, never authority-widening: it has NO write path to AG authority
or PK rules (assert by construction AND test). On a constitutional-class incident it fails
closed HARD (the one place the response is maximal, because the thing that failed is the thing
everything else rests on) and escalates — it never weakens anything in response.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .economics import (
    CostWeights, PathCost, equilibrium_holds, expected_cost_per_success,
    governance_pressure_differential,
)
from .manifest import (
    ConstitutionalBreach, ConstitutionVerifier, SignedManifest, canonical_surface_bytes,
    ratify, surface_hash,
)
from .probe import IntegrityProbe, Probe, ProbeResult
from .taxonomy import (
    BREACH, DEFENSE_IN_DEPTH, FAILCLOSED_DEP, INCENTIVE_STABLE, PREVENTION_DEP,
    RouteObservation, classify,
)

__all__ = [
    "CostWeights", "PathCost", "expected_cost_per_success",
    "governance_pressure_differential", "equilibrium_holds",
    "RouteObservation", "classify", "DEFENSE_IN_DEPTH", "PREVENTION_DEP",
    "INCENTIVE_STABLE", "FAILCLOSED_DEP", "BREACH",
    "IntegrityProbe", "Probe", "ProbeResult",
    "ConstitutionVerifier", "SignedManifest", "ConstitutionalBreach",
    "canonical_surface_bytes", "surface_hash", "ratify",
    "ConstitutionalStability",
]


class ConstitutionalStability:
    """The CS-EQ recorder + fail-closed responder. Composes the signed manifest (legs 1 & 3)
    with the economic differential + taxonomy (leg 2 instrumentation). Records to EL through the
    public interface only; holds NO handle to AG authority or PK rules — it cannot widen
    authority or weaken a gate by construction.

    ORGAN markers and EL events use 'CSEQ'. observe() runs on boot/periodically; the leg-2
    equilibrium CLAIM is deferred — equilibrium_report() returns evidence, never a verdict."""

    ORGAN = "CSEQ"

    def __init__(self, el: Any, verifier: Optional[ConstitutionVerifier] = None) -> None:
        self._el = el                 # EL public interface ONLY (append / query)
        self._verifier = verifier     # optional: present when a signed manifest is deployed

    # -- leg 3: tamper-evidence on boot (detect-and-fail-closed-HARD) -------
    def verify_on_boot(self, running_surface: Dict[str, Any],
                       manifest: SignedManifest) -> None:
        """Verify the running constitutional surface against the human-signed manifest. ANY
        mismatch/forgery raises ConstitutionalBreach (FULL fail-closed — not degrade). Requires
        a verifier (the cage's public key). Logs the verification either way."""
        if self._verifier is None:
            raise ConstitutionalBreach("CSEQ: no constitution verifier (public key) available")
        self._verifier.verify_on_boot(running_surface, manifest)   # raises on failure, logs

    # -- leg 2: instrument the differential per decision (observe only) -----
    def record_decision_equilibrium(self, capability_class: str, governed: PathCost,
                                    ungoverned_best: PathCost,
                                    obs: RouteObservation,
                                    weights: Optional[CostWeights] = None) -> Dict[str, Any]:
        """Estimate the best ungoverned alternative, compute the Governance Pressure Differential
        and the equilibrium regime, and RECORD them. A BREACH regime or D ≤ 0 (an exploitable
        bypass — PR#39 generalized into a STANDING check) is a constitutional-class incident.
        This function only RECORDS; the fail-closed-HARD response is the kernel's, triggered off
        this event. CS-EQ has no authority/gate write path of its own."""
        d = governance_pressure_differential(governed, ungoverned_best, weights or CostWeights())
        regime = classify(obs)
        incident = (regime == BREACH) or (not equilibrium_holds(d))
        payload = {
            "capability_class": capability_class,
            "D": (None if math.isinf(d) else d),     # normalise BOTH +inf and -inf (governed
            "D_is_inf": math.isinf(d),               # p_success==0 → -inf), not just +inf — a raw
            #                                          -inf would serialise as non-standard JSON.
            "regime": regime,
            "constitutional_incident": incident,
        }
        self._append("CONSTITUTION_INCIDENT" if incident else "EQUILIBRIUM_OBS",
                     capability_class, payload)
        return payload

    def equilibrium_report(self) -> Dict[str, Any]:
        """The regime distribution + D-series evidence (observe). DELIBERATELY does NOT claim
        the equilibrium 'holds' — leg 2 as a sustained property cannot be asserted at deploy;
        the claim waits until the ledger shows it under real load. Returns evidence + a
        sufficiency flag, never a verdict (the observe/evaluate split)."""
        regimes: Dict[str, int] = {}
        incidents = 0
        try:
            rows = self._el.query({"source_organ": self.ORGAN, "limit": 1_000_000})
        except Exception:
            rows = []
        ds: List[float] = []
        for ev in rows:
            p = ev.get("payload") or {}
            r = p.get("regime")
            if r is not None:
                regimes[r] = regimes.get(r, 0) + 1
            if p.get("constitutional_incident"):
                incidents += 1
            if isinstance(p.get("D"), (int, float)):
                ds.append(float(p["D"]))
        return {
            "regime_distribution": regimes,
            "incidents": incidents,
            "observations": len(rows),
            "min_D": min(ds) if ds else None,
            "claim": "deferred",   # leg-2 property is INSTRUMENTED, not yet asserted
            "note": "evidence only; the equilibrium-holds claim awaits real-load history",
        }

    # -- EL append (public interface; never private handles) ----------------
    def _append(self, action_type: str, capability_class: str,
                payload: Dict[str, Any]) -> None:
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": self.ORGAN,
            "action_type": action_type, "object_ids": [capability_class],
            "payload": payload, "evidence_confidence": 1.0,
            "evidence_source": self.ORGAN, "prev_hash": "", "hash": "",
        })
