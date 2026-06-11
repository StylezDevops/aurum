"""CC — Concentration Check.

DERIVED VIEW over EL (not a first-class organ): no independent state, no authority,
never blocks. Reads usage distribution from EL and flags any artifact servicing a
disproportionate share of workflows/writes/promotions as systemic risk. Feeds MGC
(do-not-retire) and TCM (harden-or-split). First to defer under resource constraints.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ConcentrationCheck:
    """Read-only concentration view. `concentration()` = each artifact's share of ledger events
    that reference it (the usage distribution). `systemic_risks()` = the artifacts whose share
    crosses `threshold` (single points of concentration), suppressed below `min_events` so a tiny
    sample can't flag a 1/1 artifact as 100% systemic. Returns findings; never writes, never blocks."""

    ORGAN = "CC"

    def __init__(self, el: Any = None, *, threshold: float = 0.5, min_events: int = 4,
                 window: Optional[int] = None) -> None:
        # Derived view over EL; el optional so the organ instantiates bare. `window` (L4): when
        # set, concentration is measured over the most RECENT `window` events — both a bound on
        # the working set for a large ledger and a more useful "recent concentration" semantic.
        self._el = el
        self.threshold = float(threshold)
        self.min_events = int(min_events)
        self._window = int(window) if window else None

    def _events(self) -> List[Dict[str, Any]]:
        if self._el is None:
            raise RuntimeError("CC is a derived view over EL; an EvidenceLedger is required")
        # Read THROUGH EL's public surface (never EL._db), same as IDM/MPD.
        events = list(self._el.iter_events(ascending=True))
        if self._window is not None and self._window > 0:
            return events[-self._window:]            # recency window (L4)
        return events

    @staticmethod
    def _shares(events: List[Dict[str, Any]]) -> Dict[str, float]:
        total = len(events)
        if total == 0:
            return {}
        counts: Dict[str, int] = {}
        for ev in events:
            # set() so multiple references to one artifact in a single event count once —
            # we measure "in what fraction of events does this artifact appear".
            for oid in set(ev.get("object_ids") or []):
                counts[oid] = counts.get(oid, 0) + 1
        return {oid: n / total for oid, n in counts.items()}

    def concentration(self) -> Dict[str, float]:
        """artifact_id -> share of all ledger events that reference it (0..1)."""
        return self._shares(self._events())

    def systemic_risks(self) -> List[str]:
        """Artifacts whose share >= threshold — disproportionate single points of concentration,
        ordered by share desc. Empty below `min_events`. Feeds MGC (do-not-retire) + TCM
        (harden-or-split); never blocks."""
        events = self._events()
        if len(events) < self.min_events:
            return []
        shares = self._shares(events)
        ranked = sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))
        return [oid for oid, share in ranked if share >= self.threshold]
