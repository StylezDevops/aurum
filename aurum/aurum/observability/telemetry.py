"""GovernanceTelemetry — the day-one operational dashboard, derived from EL + AG.

Pulled forward per the build brief: "you need these to see the system behave." A DERIVED VIEW
(Tier 3.5 discipline) — read-only, holds no state, never blocks, never writes. Three signals:

  • LEDGER LATENCY + DEPTH — append-latency percentiles and the in-flight depth gauge (the
    GIL-vs-ledger canary the runtime reference says to watch: if depth/latency climb, the
    synchronous decision path is starving and a writer/process-pool scale-up is indicated).
  • GOVERNANCE-EVENT RATE — gates, denials, conflicts, demotions, proposals, invalidations per
    window. The "human OVERWHELMED" threshold (how many governance events/day before even
    batched review overloads the owner) must be DISCOVERED from this, not guessed — so it is
    instrumented from the first task.
  • AUTHORITY DISTRIBUTION — the live band of every capability class + a band histogram, so
    silent drift toward (or away from) autonomy is visible.

None of this blocks an action or moves authority — observability ≠ control. It exists so drift,
saturation, and over-/under-caution become visible BEFORE they become incidents.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

# Outcome kinds the rate view reports (stable schema: 0, not absent). Per-action DECISIONS
# (allow|deny|needs_gate) come from the `decisions` table — the by-value replay surface; the
# OUTCOME/health kinds (outcome_*, degraded, fail_closed, integrity_alarm) remain
# GOVERNANCE_DECISION events. Two streams, one merged view.
_DECISION_KINDS = ("allow", "deny", "needs_gate")
_EVENT_OUTCOME_KINDS = (
    "outcome_demote", "outcome_hold", "outcome_verdict",
    "degraded", "fail_closed", "integrity_alarm",
)
_OUTCOME_KINDS = _DECISION_KINDS + _EVENT_OUTCOME_KINDS


def _parse_ts(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


class GovernanceTelemetry:
    """Read-only telemetry over a GovernanceKernel (its EL + AG). Construct cheaply per-call;
    every method is a fresh derived read, so it never goes stale or holds a lock."""

    def __init__(self, kernel: Any) -> None:
        self._k = kernel

    # -- ledger health -----------------------------------------------------
    def ledger_latency(self) -> Dict[str, Any]:
        """Append latency percentiles + in-flight depth gauge for the live EL path."""
        return self._k.el.append_metrics()

    # -- authority distribution -------------------------------------------
    def authority_distribution(self) -> Dict[str, Any]:
        return self._k.ag.distribution()

    # -- governance event rate --------------------------------------------
    def governance_event_rate(self, since: Optional[str] = None) -> Dict[str, Any]:
        """Counts of governance activity over the (optionally time-bounded) ledger, plus an
        events/day estimate from the observed time span. Cold/empty ledger → all-zero, never
        an error (clean no-op)."""
        by_outcome: Dict[str, int] = {k: 0 for k in _OUTCOME_KINDS}
        # Per-action DECISIONS from the by-value replay surface (the `decisions` table).
        decision_counts = self._k.el.decision_outcome_counts(since=since)
        for final, n in decision_counts.items():
            by_outcome[final] = by_outcome.get(final, 0) + n
        n_decisions = sum(decision_counts.values())
        # OUTCOME + health kinds remain GOVERNANCE_DECISION events (learning loop + fail-safe).
        ev_filters: Dict[str, Any] = {"action_type": "GOVERNANCE_DECISION", "limit": 1_000_000}
        if since is not None:
            ev_filters["since"] = since
        for ev in self._k.el.query(ev_filters):
            outcome = (ev.get("payload") or {}).get("outcome")
            if outcome is not None:
                by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

        # Broader governance activity across organs (TRUST_CHANGE, VOTE, PROPOSAL, ARCHIVE, …).
        all_filters: Dict[str, Any] = {"limit": 1_000_000}
        if since is not None:
            all_filters["since"] = since
        events = self._k.el.query(all_filters)
        by_action_type: Dict[str, int] = {}
        stamps: List[datetime] = []
        for ev in events:
            at = ev.get("action_type", "?")
            by_action_type[at] = by_action_type.get(at, 0) + 1
            ts = _parse_ts(ev.get("timestamp"))
            if ts is not None:
                stamps.append(ts)

        per_day = None
        if len(stamps) >= 2:
            span_s = (max(stamps) - min(stamps)).total_seconds()
            if span_s > 0:
                per_day = round(len(events) / (span_s / 86400.0), 2)

        return {
            "total_decisions": n_decisions,
            "total_events": len(events),
            "by_outcome": by_outcome,
            "by_action_type": by_action_type,
            "events_per_day": per_day,        # None until ≥2 timestamped events span > 0s
            "since": since,
        }

    # -- one-shot dashboard line ------------------------------------------
    def snapshot(self, since: Optional[str] = None) -> Dict[str, Any]:
        return {
            "ledger": self.ledger_latency(),
            "authority": self.authority_distribution(),
            "governance": self.governance_event_rate(since=since),
        }
