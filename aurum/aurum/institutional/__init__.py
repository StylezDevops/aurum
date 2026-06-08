"""Institutional mechanisms — capture-resistance grounded in institutional theory.

Monitors and brakes, never engines: they record from day one and act only as the evidence
ledger earns it (observe() on, evaluate()/act() deferred behind evidence-sufficiency). None may
widen authority, weaken a gate, or relax arbitration — detect-and-escalate (CPD), detect-and-
demote-only (FC), or detect-and-fail-closed (CS-EQ, in ../cseq). CS-EQ ships in aurum.cseq; FC
here; CPD and MAA join here in later Phase-E checkpoints.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from .fc import ForcedContestability, Scrutiny

__all__ = ["ForcedContestability", "Scrutiny"]
