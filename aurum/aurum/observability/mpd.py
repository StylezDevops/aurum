"""MPD — Memory Poisoning Detector.  DERIVED VIEW over EL (no own state, never deletes).

Scans EL for poisoning signatures and flags suspects for OWNER REVIEW only — it never
auto-deletes (a false positive would erase a real lesson) and never blocks. Two
signatures are computable from EL today (pulled forward before BB/OI land):

  • success-then-contradiction — an event recording success on an object that a LATER
    event on the SAME object contradicts (EXCEPTION / failure outcome). When OI lands,
    its verdicts sharpen this; the EL-lineage version runs from day one.
  • uniform-confidence cluster — a cluster of events from one source sharing an
    identical evidence_confidence (suspiciously uniform; organic evidence varies).

Also surfaces the day-one GOVERNANCE-EVENT-RATE baseline (`governance_event_rate`): the
spec build order requires this from the first task so AG/EG have history to tune against.
This is a read-only telemetry read pulled forward alongside scan/explain — still no
authority, no state, never blocks.

Findings feed back via TWO consumers now that OI/BB exist (the loop the early note deferred):
the kernel's between-turn scan surfaces suspects to BB for OWNER REVIEW (never auto-deletes), and
`quarantined_evidence()` (the success-then-contradiction subset — a recorded success a later
outcome on the same object contradicts) drives `effective_confidence()`, a READ-TIME overlay (EL
is append-only, so a poisoned row's stored confidence cannot be mutated) that the kernel's
verify-gated rehydration honours — a contradicted grounded 'success' does NOT silently rebuild
authority/scope on --rm. The uniform-confidence signature stays OWNER-REVIEW only (scan()): a
single trusted source can legitimately emit a run of identical confidence (the spine's own
decisions are all 1.0), so it is never an automatic evidence discount.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_FAILURE_OUTCOMES = {"fail", "failure", "error", "contradicted", "reverted",
                     "rolled_back", "regression"}


class MemoryPoisoningDetector:
    ORGAN = "MPD"

    def __init__(self, el: Any = None, min_cluster: int = 4) -> None:
        # Derived view over EL; el optional so the organ instantiates bare.
        self._el = el
        self.min_cluster = min_cluster

    # -- ledger access (read-only) -----------------------------------------
    def _events(self) -> List[Dict[str, Any]]:
        if self._el is None:
            raise RuntimeError(
                "MPD is a derived view over EL; an EvidenceLedger is required")
        # read THROUGH EL's public surface (never EL._db). EL returns
        # 'evidence_confidence'; MPD's signatures use the shorter 'confidence' alias.
        return [{**e, "confidence": e["evidence_confidence"]}
                for e in self._el.iter_events(ascending=True)]

    @staticmethod
    def _is_success(ev: Dict[str, Any]) -> bool:
        p = ev["payload"]
        return (ev["action_type"] in ("PROMOTION", "PROMOTE")
                or p.get("outcome") == "success" or p.get("success") is True
                # the grounded-outcome TRUST stream (kernel GOVERNANCE_DECISION): a satisfied
                # verdict is a recorded success — the exact memory that builds AG/TL/familiarity.
                or (p.get("outcome") == "outcome_verdict" and p.get("satisfied") is True))

    @staticmethod
    def _is_contradiction(ev: Dict[str, Any]) -> bool:
        p = ev["payload"]
        return (ev["action_type"] == "EXCEPTION"
                or p.get("outcome") in _FAILURE_OUTCOMES or p.get("success") is False
                # a later verdict on the SAME object that flips to unsatisfied contradicts it.
                or (p.get("outcome") == "outcome_verdict" and p.get("satisfied") is False))

    # -- signatures ---------------------------------------------------------
    def _success_contradictions(self, events: List[Dict[str, Any]]
                                ) -> Dict[str, List[str]]:
        """suspect success event_id -> [contradicting later event_ids on a shared object].

        Indexed (object_id -> ordered postings) so the scan is ~linear in total postings
        instead of O(n^2) over the whole ledger; each later contradiction is collected once
        and the results stay in seq order."""
        postings: Dict[str, List[int]] = {}
        for i, ev in enumerate(events):
            for oid in ev["object_ids"]:
                postings.setdefault(oid, []).append(i)
        out: Dict[str, List[str]] = {}
        for i, ev in enumerate(events):
            if not self._is_success(ev):
                continue
            later_idx = set()
            for oid in ev["object_ids"]:
                for j in postings.get(oid, ()):
                    if j > i and self._is_contradiction(events[j]):
                        later_idx.add(j)
            if later_idx:
                out[ev["event_id"]] = [events[j]["event_id"] for j in sorted(later_idx)]
        return out

    def _uniform_clusters(self, events: List[Dict[str, Any]]
                          ) -> Dict[str, List[str]]:
        """suspect event_id -> [sibling event_ids] for (source, confidence) buckets whose
        size >= min_cluster (suspiciously uniform confidence)."""
        buckets: Dict[tuple, List[str]] = {}
        for ev in events:
            if ev["confidence"] is None:
                continue
            buckets.setdefault((ev["source_organ"], ev["confidence"]), []).append(
                ev["event_id"])
        out: Dict[str, List[str]] = {}
        for ids in buckets.values():
            if len(ids) >= self.min_cluster:
                idset = set(ids)
                for eid in ids:
                    out[eid] = sorted(idset - {eid})
        return out

    # -- public read-only API ----------------------------------------------
    def scan(self) -> List[str]:
        events = self._events()
        suspects = set(self._success_contradictions(events))
        suspects |= set(self._uniform_clusters(events))
        return sorted(suspects)

    def explain(self, evidence_id: str) -> Dict[str, Any]:
        """-> {signature, contradicting_events}"""
        events = self._events()
        sc = self._success_contradictions(events)
        if evidence_id in sc:
            return {"signature": "success_contradicted_by_later_outcome",
                    "contradicting_events": sc[evidence_id]}
        uc = self._uniform_clusters(events)
        if evidence_id in uc:
            return {"signature": "suspiciously_uniform_confidence_cluster",
                    "contradicting_events": uc[evidence_id]}
        return {"signature": None, "contradicting_events": []}

    # -- auto-actionable quarantine + read-time evidence overlay ------------
    def quarantined_evidence(self) -> set:
        """Event ids in the SUCCESS-THEN-CONTRADICTION signature ONLY — the subset safe to act on
        automatically. A recorded success that a later outcome on the same object contradicts is
        unambiguously an unreliable 'lesson', whatever its source. The uniform-confidence cluster
        is DELIBERATELY EXCLUDED: a single legitimate source can emit a run of identical confidence
        (the spine's own decisions are all 1.0), so that signature is OWNER-REVIEW only (scan()),
        never an automatic discount. Returns a set of suspect event_ids."""
        return set(self._success_contradictions(self._events()))

    def effective_confidence(self, event: Dict[str, Any], *, discount: float = 0.0,
                             quarantined: Optional[set] = None) -> float:
        """Read-time evidence-confidence OVERLAY — how a finding feeds back when EL is append-only
        (a poisoned row's stored confidence cannot be mutated in place). Returns the event's stored
        `evidence_confidence`, REPLACED by `discount` (default 0.0 = fully distrusted) when the
        event is in the auto-quarantine set. Pass a precomputed `quarantined` set to apply the
        overlay across many events without re-scanning the ledger each call."""
        q = quarantined if quarantined is not None else self.quarantined_evidence()
        if event.get("event_id") in q:
            return float(discount)
        return float(event.get("evidence_confidence") or 0.0)

    def governance_event_rate(self, window_seconds: Optional[float] = None,
                              now: Optional[datetime] = None) -> Dict[str, Any]:
        """Day-one governance-activity baseline from EL — the history AG/EG tune against.
        Read-only counts of ledger events by action_type and source_organ (+ decisions /
        conflicts totals), with an events/hour rate over the observed (or windowed) span."""
        if self._el is None:
            raise RuntimeError(
                "MPD is a derived view over EL; an EvidenceLedger is required")
        events = self._events()
        cutoff_iso: Optional[str] = None
        if window_seconds is not None:
            cutoff = (now or datetime.now(timezone.utc)).timestamp() - window_seconds
            # _epoch() returns None for an empty/unparsable timestamp — exclude those
            # rather than comparing None to a float (would raise TypeError).
            kept = []
            for e in events:
                ep = self._epoch(e["timestamp"])
                if ep is not None and ep >= cutoff:
                    kept.append(e)
            events = kept
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

        by_action: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for e in events:
            by_action[e["action_type"]] = by_action.get(e["action_type"], 0) + 1
            by_source[e["source_organ"]] = by_source.get(e["source_organ"], 0) + 1

        # Rate denominator: honour the REQUESTED window when one is given, else the
        # observed min..max span — so a windowed call reports a window-consistent rate
        # rather than one derived from whatever events happened to fall inside it.
        if window_seconds is not None:
            span = float(window_seconds)
        else:
            stamps = sorted(s for s in (self._epoch(e["timestamp"]) for e in events)
                            if s is not None)
            span = (stamps[-1] - stamps[0]) if len(stamps) >= 2 else 0.0
        per_hour = (len(events) / (span / 3600.0)) if span > 0 else 0.0
        # decisions/conflicts are windowed consistently with the ledger counts (their ts
        # are ISO-8601 UTC, so EL's lexicographic >= against the cutoff is correct).
        dec = self._el.count_decisions(since=cutoff_iso)
        con = self._el.count_conflicts(since=cutoff_iso)
        return {"ledger_total": len(events), "by_action_type": by_action,
                "by_source_organ": by_source, "decisions": dec, "conflicts": con,
                "span_seconds": span, "events_per_hour": per_hour,
                "windowed": window_seconds is not None}

    @staticmethod
    def _epoch(ts: Optional[str]) -> Optional[float]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return None
