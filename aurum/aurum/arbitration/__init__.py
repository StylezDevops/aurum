"""Arbitration layer (MECHANISMS, not organs): Conflict Arbiter + Deadlock Detector.

Per aurum_arbitration_spec.md. CA is synchronous + deliberately DUMB (deterministic
most-conservative-wins, no model call, fully replayable); DD is asynchronous, reads the
conflict log, and ESCALATES governance deadlock — it never self-resolves and never widens
authority. Neither holds organ-level state beyond the conflict log they read/write.
"""
from .conflict_arbiter import ArbitrationError, ConflictArbiter
from .deadlock_detector import DeadlockDetector

__all__ = ["ConflictArbiter", "ArbitrationError", "DeadlockDetector"]
