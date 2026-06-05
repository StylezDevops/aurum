"""AO — Agent Orchestrator. DEFERRED (post-v1, documented only).

Multi-agent coordination: authority inheritance, lease ownership/arbitration
across agents, canonical EL. Single-agent v1 must not hard-code assumptions AO
would have to unwind, so authority/leases/ledger ownership are per-entity.

This module intentionally remains a documented boundary, not an implementation.
"""
from __future__ import annotations

from ..base import unbuilt


class AgentOrchestrator:
    ORGAN = "AO"
    DEFERRED = True

    def __getattr__(self, name: str):
        raise unbuilt(self.ORGAN, name)
