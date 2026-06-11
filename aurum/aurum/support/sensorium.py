"""SEN — Sensorium. Pluggable watchers (folder/inbox/repo/webhook/RSS) wake the agent.

SEN is the INGESTION PATH; PK is the enforcement. Content arriving via a watcher is ingested
content: SEN tags it **untrusted** and its provenance `source` is the watcher id — NEVER the
operator channel — so anything it would "justify" is denied binding by PK's injection boundary
(AURUM_ERR_008). The security-critical guarantee: an inbound event cannot *claim* operator trust;
a spoofed `source: "operator"` is rewritten to an untrusted token. Only the operator's own direct
channel carries operator trust, and SEN never mints it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..base import unbuilt  # noqa: F401 — kept so a future method can mark itself unbuilt

UNTRUSTED = "untrusted"
# A spoofed `source: "operator"` on inbound content must NOT bind; remap it to this untrusted token.
_SPOOFED_OPERATOR = "sen:spoofed-operator"


class Sensorium:
    """In-memory watcher registry + event intake. `watch(source, handler)` registers a handler for
    a named source; `on_event(event)` tags provenance untrusted and dispatches to that source's
    handlers. Fail-safe: a watcher that raises is isolated, never crashing the sensorium."""

    ORGAN = "SEN"

    def __init__(self, screener: Optional[Callable[[str], Dict[str, Any]]] = None) -> None:
        self._watchers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        # Optional injection SCREENER: a Callable[text -> verdict dict]. Run on every ingested
        # event, it attaches `ev["screen"]` = {exploit_confidence, is_malicious_override, signals}.
        # A SENSOR only — it never rewrites the payload (provenance stays untrusted) and its failure
        # is isolated (no verdict → the base structural taint still applies). See injection_screen.
        self._screener = screener

    def watch(self, source: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        """Register `handler` for events from `source` (a watcher id like 'gmail-2fa',
        'folder:artwork', 'webhook:n8n'). Multiple handlers per source are allowed."""
        if not isinstance(source, str) or not source:
            raise ValueError("SEN.watch: source must be a non-empty string id")
        if source == "operator":
            raise ValueError("SEN.watch: 'operator' is reserved for the direct channel, not a watcher")
        if not callable(handler):
            raise TypeError("SEN.watch: handler must be callable")
        self._watchers.setdefault(source, []).append(handler)

    def on_event(self, event: Any) -> Dict[str, Any]:
        """Ingest an inbound event. Tags it untrusted, sets its provenance source (rewriting any
        spoofed 'operator' claim), exposes `justification_sources` carrying that untrusted source
        (so a derived action is denied binding by PK), and dispatches to the source's handlers.
        Returns the tagged event. A handler that raises is recorded in `errors`, never propagated."""
        ev: Dict[str, Any] = dict(event) if isinstance(event, dict) else {"payload": event}
        claimed = str(ev.get("source") or "sen")
        source = _SPOOFED_OPERATOR if claimed == "operator" else claimed
        ev["source"] = source
        ev["trust"] = UNTRUSTED                       # SEN content is ALWAYS untrusted
        ev["justification_sources"] = [source]        # never 'operator' → PK denies binding (008)
        errors: List[str] = []
        # Screen the inbound content (sensor): attach a verdict, NEVER rewrite the payload. A
        # screener failure is isolated — no verdict means the base provenance taint still applies.
        if self._screener is not None:
            try:
                ev["screen"] = dict(self._screener(self._screen_text(ev)))
            except Exception as e:  # noqa: BLE001 — a flaky screener must not crash ingestion
                errors.append(f"screener {type(e).__name__}: {e}")
        dispatched = 0
        for handler in self._watchers.get(source, []):
            try:
                handler(ev)
                dispatched += 1
            except Exception as e:  # noqa: BLE001 — isolate a bad watcher; the sensorium survives
                errors.append(f"{type(e).__name__}: {e}")
        ev["dispatched"] = dispatched
        if errors:
            ev["errors"] = errors
        return ev

    @staticmethod
    def _screen_text(ev: Dict[str, Any]) -> str:
        """The inbound text to screen, pulled from the event payload (str, or a text-bearing key
        of a dict, else its repr). Screening reads content; it never mutates it."""
        p = ev.get("payload")
        if isinstance(p, str):
            return p
        if isinstance(p, dict):
            for k in ("text", "body", "content", "message"):
                if isinstance(p.get(k), str):
                    return p[k]
        return "" if p is None else str(p)
