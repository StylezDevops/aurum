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

Findings are RETURNED, not written: feeding them back into evidence_confidence requires
downstream consumers (and EL is append-only), so that loop is deferred until OI/BB exist.
"""
from __future__ import annotations

import json
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
        rows = self._el._db.execute(
            "SELECT seq, event_id, source_organ, action_type, object_ids, payload, "
            "evidence_confidence, timestamp FROM evidence_ledger ORDER BY seq ASC"
        ).fetchall()
        return [
            {"seq": r[0], "event_id": r[1], "source_organ": r[2], "action_type": r[3],
             "object_ids": json.loads(r[4] or "[]"), "payload": json.loads(r[5] or "{}"),
             "confidence": r[6], "timestamp": r[7]}
            for r in rows
        ]

    @staticmethod
    def _is_success(ev: Dict[str, Any]) -> bool:
        p = ev["payload"]
        return (ev["action_type"] == "PROMOTION"
                or p.get("outcome") == "success" or p.get("success") is True)

    @staticmethod
    def _is_contradiction(ev: Dict[str, Any]) -> bool:
        p = ev["payload"]
        return (ev["action_type"] == "EXCEPTION"
                or p.get("outcome") in _FAILURE_OUTCOMES or p.get("success") is False)

    # -- signatures ---------------------------------------------------------
    def _success_contradictions(self, events: List[Dict[str, Any]]
                                ) -> Dict[str, List[str]]:
        """suspect success event_id -> [contradicting later event_ids on a shared object]."""
        out: Dict[str, List[str]] = {}
        for i, ev in enumerate(events):
            if not self._is_success(ev):
                continue
            objs = set(ev["object_ids"])
            contradictions = [
                later["event_id"] for later in events[i + 1:]
                if self._is_contradiction(later) and objs & set(later["object_ids"])
            ]
            if contradictions:
                out[ev["event_id"]] = contradictions
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

    def governance_event_rate(self, window_seconds: Optional[float] = None,
                              now: Optional[datetime] = None) -> Dict[str, Any]:
        """Day-one governance-activity baseline from EL — the history AG/EG tune against.
        Read-only counts of ledger events by action_type and source_organ (+ decisions /
        conflicts totals), with an events/hour rate over the observed (or windowed) span."""
        if self._el is None:
            raise RuntimeError(
                "MPD is a derived view over EL; an EvidenceLedger is required")
        events = self._events()
        if window_seconds is not None:
            cutoff = (now or datetime.now(timezone.utc)).timestamp() - window_seconds
            events = [e for e in events if self._epoch(e["timestamp"]) >= cutoff]

        by_action: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for e in events:
            by_action[e["action_type"]] = by_action.get(e["action_type"], 0) + 1
            by_source[e["source_organ"]] = by_source.get(e["source_organ"], 0) + 1

        stamps = sorted(s for s in (self._epoch(e["timestamp"]) for e in events)
                        if s is not None)
        span = (stamps[-1] - stamps[0]) if len(stamps) >= 2 else 0.0
        per_hour = (len(events) / (span / 3600.0)) if span > 0 else 0.0
        dec = self._el._db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        con = self._el._db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
        return {"ledger_total": len(events), "by_action_type": by_action,
                "by_source_organ": by_source, "decisions": dec, "conflicts": con,
                "span_seconds": span, "events_per_hour": per_hour}

    @staticmethod
    def _epoch(ts: Optional[str]) -> Optional[float]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return None
