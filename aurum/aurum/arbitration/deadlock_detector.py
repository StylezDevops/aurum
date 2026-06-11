"""DD — Deadlock Detector (asynchronous, background). Reads the conflict log; ESCALATES.

Distinguishes WISE CAUTION from GOVERNANCE DEADLOCK and escalates the latter — it never
acts to resolve, never weakens arbitration, never widens authority (the only autonomous
action is escalation). The discriminator is historical: did the JUSTIFYING evidence change
while the resolution stayed stuck?

D = w1·resolution_homogeneity + w2·evidence_divergence + w3·effectiveness_slope_neg
(Σw=1.0). The discriminating product is homogeneity × evidence_divergence:
  stuck-contract + justification still elevated → wise caution (low D, no flag)
  stuck-contract + justification abated         → deadlock (high D, flag)

Constitutional exclusion (the most important guard): contractions whose winner is a
Core/PK/standing-PM rule (constitutional=true) are EXCLUDED entirely — settled policy is not
stuck governance. DD's own parameters are CONSTITUTIONAL: human-gated only, never auto-tuned
(else a Goodharting loop learns to silence its own alarm). Escalations are per-signature
deduplicated — recurrences increment a counter, never spawn new gate items.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# constitutional seeds (Class-C human gate to change; never auto-learned)
_SEEDS = {"window_days": 30.0, "min_recurrence": 5, "homogeneity_threshold": 0.80,
          "evidence_divergence_delta": 0.20, "w1": 0.40, "w2": 0.40, "w3": 0.20,
          "d_flag": 0.70}


class DeadlockDetector:
    ORGAN = "DD"

    def __init__(self, ca: Any = None, el: Any = None, oi: Any = None,
                 params: Optional[Dict[str, Any]] = None) -> None:
        self._ca, self._el, self._oi = ca, el, oi
        self._params = dict(_SEEDS)
        if params:
            self._params.update(params)
        self._open: Dict[Tuple[str, frozenset], Dict[str, Any]] = {}  # dedup registry

    # -- the only autonomous action: detect + escalate ---------------------
    def scan(self) -> List[Dict[str, Any]]:
        """Group conflicts by signature, score D, escalate signatures past the flag.
        Returns the CURRENT open escalations (deduplicated per signature)."""
        records = self._ca.conflicts() if self._ca is not None else []
        by_sig: Dict[Tuple[str, frozenset], List[Dict[str, Any]]] = {}
        for r in records:
            tier1 = frozenset(p["signal"] for p in r["participants"]
                              if p["signal"] in ("AG", "HVP"))
            by_sig.setdefault((r["capability_class"], tier1), []).append(r)

        for sig, recs in by_sig.items():
            if len(recs) < self._params["min_recurrence"]:
                continue
            # constitutional exclusion: settled policy is never deadlock
            if self._settled_policy(recs):
                continue
            D = self._score(recs)
            if D >= self._params["d_flag"]:
                self._escalate(sig, recs, D)
        return list(self._open.values())

    @staticmethod
    def _settled_policy(recs: List[Dict[str, Any]]) -> bool:
        """Constitutional exclusion: a signature is SETTLED POLICY (never a deadlock) iff EVERY
        contraction in it had at least ONE CONSTITUTIONAL contracting signal — read from the
        participants, NOT the named winner. CA names the winner by a fixed most-restrictive order
        (AG before HVP), so a constitutional HVP contracting ALONGSIDE a non-constitutional AG
        would be named loser and missed by a winner-only check — falsely escalating settled policy
        as a governance deadlock. Keying on 'any constitutional contractor' fixes that without
        touching CA, the persisted winner_constitutional field, the schema, or DD's frozen params."""
        contracts = [r for r in recs if r["resolution"] == "contract"]
        if not contracts:
            return False
        return all(
            any(p.get("directive") == "contract" and p.get("constitutional")
                for p in r.get("participants", []))
            for r in contracts)

    def _score(self, recs: List[Dict[str, Any]]) -> float:
        n = len(recs)
        contracts = [r for r in recs if r["resolution"] == "contract"]
        homogeneity = len(contracts) / n if n else 0.0
        evidence_divergence = self._divergence(contracts)
        eff_slope = self._effectiveness_slope_neg()
        p = self._params
        return (p["w1"] * homogeneity + p["w2"] * evidence_divergence
                + p["w3"] * eff_slope)

    def _divergence(self, contracts: List[Dict[str, Any]]) -> float:
        """How far the winner's justification ABATED across the window while resolution
        stayed contract. justification ∈ [0,1] in each record's risk_snapshot
        (higher = more reason to contract); a drop ≥ delta = full divergence."""
        if len(contracts) < 2:
            return 0.0
        ordered = sorted(contracts, key=lambda r: r["timestamp"])
        first = self._justification(ordered[0])
        last = self._justification(ordered[-1])
        if first is None or last is None:
            return 0.0
        drop = first - last  # justification fell => justification abated
        delta = self._params["evidence_divergence_delta"]
        return max(0.0, min(1.0, drop / delta)) if delta > 0 else 0.0

    @staticmethod
    def _justification(rec: Dict[str, Any]) -> Optional[float]:
        snap = rec["risk_snapshot"].get(rec.get("winner") or "", {})
        j = snap.get("justification")
        return float(j) if isinstance(j, (int, float)) else None

    def _effectiveness_slope_neg(self) -> float:
        if self._oi is None:
            return 0.0
        try:
            ratio = self._oi.effectiveness().get("ratio", 1.0)
        except Exception:
            return 0.0
        return 1.0 if ratio < 0.5 else 0.0  # crude: a low effectiveness ratio contributes

    def _escalate(self, sig, recs, D: float) -> None:
        item = self._open.get(sig)
        if item is None:
            self._open[sig] = {
                "signature": {"capability_class": sig[0], "signals": sorted(sig[1])},
                "D": D, "recurrences": 1, "open": True,
                "evidence": {"homogeneity_records": len(recs),
                             "representative_conflicts": [r["conflict_id"]
                                                          for r in recs[:3]],
                             "rr_replay_handles": [r["conflict_id"] for r in recs[:3]]}}
            self._audit(sig, D)
        else:
            item["recurrences"] += 1   # dedup: same problem, growing weight
            item["D"] = D

    # -- DD never self-resolves; its params are constitutional -------------
    def set_parameters(self, params: Dict[str, Any], human_gate: bool = False) -> None:
        """DD's parameters are CONSTITUTIONAL (Class-C human gate). An automatic retune
        attempt is rejected pre-gate — the watcher must not be self-modifiable."""
        if not human_gate:
            raise PermissionError(
                "DD parameters are constitutional; recalibration is a Class-C human gate")
        self._params.update(params)

    def _audit(self, sig, D: float) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "DD",
            "action_type": "EXCEPTION", "object_ids": [sig[0]],
            "payload": {"capability_class": sig[0], "note": "governance_deadlock",
                        "D": D, "signals": sorted(sig[1])},
            "evidence_confidence": 1.0, "evidence_source": "DD",
            "prev_hash": "", "hash": ""})
