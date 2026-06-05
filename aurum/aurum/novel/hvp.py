"""HVP — Heterogeneous Verifier Panel. Checks homework across independent models.

Every verifier (and the main model) is the SAME shape: an OpenAI-compatible chat endpoint
(a roster entry). Independence is judged by FAMILY (a prior), corrected by measured
agreement-on-errors: a family pair that empirically colludes is treated as ONE family for
diversity purposes. The roster is user-owned config — the agent routes within it, never
adds endpoints itself.

The transport (`call`, a single OpenAI-compatible POST) is an injected seam; this module
implements the governance core: family-diversity routing that FAILS CLOSED when a
high-stakes check can't span `min_families` (never silently proceeds with correlated
verifiers — AURUM_ERR_005), sensitivity filtering (PK-flagged payloads only reach
sees_sensitive entries), cost routing (trivial → lowest cost_class), boolean aspect
verification, policy aggregation, ROI and per-(domain) correlation read-outs.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from ..types import EndpointEntry, Stakes

POLICIES: Dict[str, Dict[str, Any]] = {
    "high_stakes": {"min_families": 2, "aggregate": "unanimous"},
    "routine": {"min_families": 1, "aggregate": "majority"},
}


class HVPRoutingError(RuntimeError):
    """Raised when routing can't satisfy the diversity/sensitivity policy (fail closed)."""


class HeterogeneousVerifierPanel:
    ORGAN = "HVP"

    def __init__(self, el: Any = None,
                 caller: Optional[Callable[[EndpointEntry, Any], Any]] = None,
                 collusions: Optional[List[Tuple[str, str]]] = None,
                 correlation_stats: Optional[Dict[Any, float]] = None,
                 roi_stats: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        self._el = el
        self._caller = caller
        self._roster: List[EndpointEntry] = []
        # measured family pairs that collude (effectively one family). Keyed both
        # globally (a,b) and per-domain (a,b,domain) in correlation_stats.
        self._collusions = {frozenset(p) for p in (collusions or [])}
        self._corr = correlation_stats or {}
        self._roi = roi_stats or {}

    # -- roster (user-owned config) ----------------------------------------
    def configure(self, roster: List[EndpointEntry]) -> None:
        self._roster = list(roster)

    def add_endpoint(self, entry: EndpointEntry) -> None:
        """Adding a verifier is ONE roster entry — no code change (accept a)."""
        self._roster.append(entry)

    # -- effective-family grouping (family prior + measured collusion) ------
    def _group_of(self, family: str, parent: Dict[str, str]) -> str:
        while parent.get(family, family) != family:
            family = parent[family]
        return family

    def _family_groups(self, entries: List[EndpointEntry]) -> Dict[str, List[EndpointEntry]]:
        # union-find: merge families measured to collude into one diversity group.
        parent: Dict[str, str] = {}
        fams = {e["family"] for e in entries}
        for f in fams:
            parent.setdefault(f, f)
        for pair in self._collusions:
            a, b = tuple(pair)
            if a in fams and b in fams:
                parent[self._group_of(a, parent)] = self._group_of(b, parent)
        groups: Dict[str, List[EndpointEntry]] = {}
        for e in entries:
            groups.setdefault(self._group_of(e["family"], parent), []).append(e)
        return groups

    # -- routing (the governance heart) ------------------------------------
    def route(self, check: Dict[str, Any]) -> List[EndpointEntry]:
        candidates = [e for e in self._roster
                      if not check.get("is_sensitive") or e.get("sees_sensitive")]
        if not candidates:
            raise HVPRoutingError("no eligible verifiers after sensitivity filter")
        stakes: Stakes = check.get("stakes", "routine")
        policy = POLICIES[stakes]
        min_families = policy["min_families"]
        groups = self._family_groups(candidates)
        if len(groups) < min_families:
            raise HVPRoutingError(
                f"min_families violation: {stakes} needs {min_families} independent "
                f"families, roster offers {len(groups)}")
        if stakes == "routine":
            chosen = [min(candidates, key=lambda e: (e["cost_class"], -e["trust_tier"]))]
        else:
            # span min_families groups, best (highest trust, then cheapest) per group
            reps = sorted(groups, key=lambda g: -max(e["trust_tier"] for e in groups[g]))
            chosen = [min(groups[g], key=lambda e: (-e["trust_tier"], e["cost_class"]))
                      for g in reps[:min_families]]
        self._audit_route(check, chosen)
        return chosen

    # -- verification (transport via injected seam) ------------------------
    def call(self, entry: EndpointEntry, messages: Any) -> Any:
        if self._caller is None:
            raise RuntimeError("HVP.call: no transport configured (inject `caller`)")
        return self._caller(entry, messages)

    def verify(self, output: str, aspects: List[str],
               entries: Optional[List[EndpointEntry]] = None,
               policy: str = "majority") -> Dict[str, bool]:
        """Dispatch each verifier as an Aspect Verifier (binary per aspect), then
        aggregate per policy. Each vote is logged to EL (accept f)."""
        panel = entries if entries is not None else self._roster
        results: List[Dict[str, bool]] = []
        for e in panel:
            votes = self.call(e, {"output": output, "aspects": aspects})
            votes = {a: bool(votes.get(a, False)) for a in aspects}
            results.append(votes)
            self._audit_vote(e, votes)
        return self.aggregate(results, policy)["per_aspect"]

    def aggregate(self, results: List[Dict[str, bool]], policy: Any) -> Dict[str, Any]:
        mode = policy.get("aggregate") if isinstance(policy, dict) else policy
        aspects = sorted({a for r in results for a in r})
        per_aspect: Dict[str, bool] = {}
        for a in aspects:
            votes = [bool(r.get(a, False)) for r in results]
            if not votes:
                per_aspect[a] = False
            elif mode == "unanimous":
                per_aspect[a] = all(votes)
            else:  # majority
                per_aspect[a] = sum(votes) * 2 > len(votes)
        return {"per_aspect": per_aspect, "passed": all(per_aspect.values())
                if per_aspect else False}

    # -- ROI / correlation read-outs ---------------------------------------
    def roi(self, family: str) -> Dict[str, Any]:
        s = self._roi.get(family, {})
        fpr = s.get("false_positive_rate", 0.0)
        benefit = s.get("marginal_benefit", 1.0)
        cost = s.get("cost", 0.0)
        # drop from routine if it rarely changes a verdict yet costs (kept for high-stakes)
        keep = benefit >= cost or benefit > 0.05
        return {"false_positive_rate": fpr, "marginal_benefit": benefit,
                "cost": cost, "keep": keep}

    def correlation(self, family_a: str, family_b: str,
                    domain: Optional[str] = None) -> Dict[str, Any]:
        key = (frozenset((family_a, family_b)), domain)
        agreement = self._corr.get(key, self._corr.get(frozenset((family_a, family_b)), 0.0))
        colludes = frozenset((family_a, family_b)) in self._collusions or agreement >= 0.5
        return {"agreement_on_errors": agreement, "effectively_independent": not colludes}

    # -- audit --------------------------------------------------------------
    def _audit_route(self, check: Dict[str, Any], chosen: List[EndpointEntry]) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "HVP",
            "action_type": "VOTE", "object_ids": [check.get("payload_hash", "check")],
            "payload": {"capability_class": "verify", "kind": "routing",
                        "routed": [e["id"] for e in chosen],
                        "families": sorted({e["family"] for e in chosen})},
            "evidence_confidence": 1.0, "evidence_source": "HVP",
            "prev_hash": "", "hash": ""})

    def _audit_vote(self, entry: EndpointEntry, votes: Dict[str, bool]) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "HVP",
            "action_type": "VOTE", "object_ids": [entry["id"]],
            "payload": {"capability_class": "verify", "kind": "vote",
                        "family": entry["family"], "votes": votes,
                        "cost_class": entry.get("cost_class")},
            "evidence_confidence": 1.0, "evidence_source": "HVP",
            "prev_hash": "", "hash": ""})
