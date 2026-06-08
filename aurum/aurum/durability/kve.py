"""KVE — Knowledge Validity Engine. Stored knowledge is not true forever.

BB/EL/RR/MGC all assume a stored lesson stays useful; it doesn't (a D365 endpoint, an Azure
auth flow, a Power Platform feature all drift). KVE gives every knowledge artifact
{confidence, last_verified, volatility_class} + provenance {source_type, source_uri,
observed_at, verified_at}. Confidence decays by age x volatility:
  STATIC — math/syntax/fundamentals (decays ~never)
  SLOW   — internal conventions, stable processes
  FAST   — external APIs / cloud auth / vendor metadata (D365, Azure, Power Platform)
A FAST artifact past its decay window drops below threshold, is flagged STALE and QUARANTINED
from planning until re-verified. reverify() runs a cheap probe (injected seam — real probes
go through PK + AG); a probe that finds the source changed INVALIDATES the artifact (archived
via MGC, never deleted) before it can contaminate planning. Invalidation is logged to EL.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, TypedDict

from ..types import VolatilityClass

# decay half-lives (seconds) per volatility class
_HALFLIFE = {"FAST": 14 * 86400.0, "SLOW": 180 * 86400.0, "STATIC": 100 * 365 * 86400.0}
_FAST_SOURCES = {"external_api", "cloud_auth", "vendor_metadata", "api_contract"}
_STATIC_SOURCES = {"math", "syntax", "fundamental"}


class Provenance(TypedDict):
    source_type: str
    source_uri: str
    observed_at: str
    verified_at: str


class ReverifyResult(TypedDict):
    valid: bool
    confidence: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kve_artifacts (
    artifact_id     TEXT PRIMARY KEY,
    volatility_class TEXT NOT NULL,
    base_confidence REAL NOT NULL DEFAULT 1.0,
    last_verified   REAL NOT NULL,
    source_type     TEXT,
    source_uri      TEXT,
    observed_at     TEXT,
    verified_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
);
"""


class KnowledgeValidityEngine:
    ORGAN = "KVE"

    def __init__(self, path: str = "aurum_kve.db", el: Any = None, mgc: Any = None,
                 prober: Optional[Callable[[Dict[str, Any]], bool]] = None,
                 stale_threshold: float = 0.5) -> None:
        self._el, self._mgc, self._prober = el, mgc, prober
        self.stale_threshold = stale_threshold
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- classification -----------------------------------------------------
    def classify(self, artifact: object) -> VolatilityClass:
        a = artifact if isinstance(artifact, dict) else {}
        if a.get("volatility_class") in _HALFLIFE:
            return a["volatility_class"]  # type: ignore[return-value]
        st = a.get("source_type")
        if st in _FAST_SOURCES:
            return "FAST"  # type: ignore[return-value]
        if st in _STATIC_SOURCES:
            return "STATIC"  # type: ignore[return-value]
        return "SLOW"  # type: ignore[return-value]

    # -- registration (ingest seam) ----------------------------------------
    def register(self, artifact: Dict[str, Any], now: Optional[float] = None) -> str:
        aid = artifact.get("artifact_id") or uuid.uuid4().hex
        now = time.time() if now is None else now
        self._db.execute(
            "INSERT INTO kve_artifacts(artifact_id,volatility_class,base_confidence,"
            "last_verified,source_type,source_uri,observed_at,verified_at,status) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET "
            "volatility_class=excluded.volatility_class, "
            "base_confidence=excluded.base_confidence, last_verified=excluded.last_verified",
            (aid, self.classify(artifact), float(artifact.get("confidence", 1.0)),
             artifact.get("last_verified", now), artifact.get("source_type"),
             artifact.get("source_uri"), artifact.get("observed_at"),
             artifact.get("verified_at"), "active"),
        )
        return aid

    # -- confidence decay ---------------------------------------------------
    def confidence(self, artifact_id: str, now: Optional[float] = None) -> float:
        r = self._get(artifact_id)
        if r is None:
            raise KeyError(f"KVE: no artifact {artifact_id!r}")
        if r["status"] == "invalid":
            return 0.0
        now = time.time() if now is None else now
        age = max(0.0, now - r["last_verified"])
        halflife = _HALFLIFE[r["volatility_class"]]
        return r["base_confidence"] * (0.5 ** (age / halflife))

    def volatility(self, artifact_id: str) -> Optional[VolatilityClass]:
        """The stored volatility class for a registered artifact, or None if unknown.
        Read-only accessor used by AG's familiarity time-decay (it picks the per-volatility
        λ). Unknown → None, so the caller applies its own fail-safe (fastest decay)."""
        r = self._get(artifact_id)
        return r["volatility_class"] if r is not None else None  # type: ignore[return-value]

    def provenance(self, artifact_id: str) -> Provenance:
        r = self._get(artifact_id)
        if r is None:
            raise KeyError(f"KVE: no artifact {artifact_id!r}")
        return {"source_type": r["source_type"], "source_uri": r["source_uri"],
                "observed_at": r["observed_at"], "verified_at": r["verified_at"]}

    def stale(self, now: Optional[float] = None) -> List[str]:
        """Active artifacts whose decayed confidence is below threshold — quarantined
        from planning until re-verified."""
        out = []
        for r in self._db.execute(
                "SELECT artifact_id FROM kve_artifacts WHERE status='active'").fetchall():
            if self.confidence(r[0], now=now) < self.stale_threshold:
                out.append(r[0])
        return out

    # -- re-verification + invalidation ------------------------------------
    def reverify(self, artifact_id: str, now: Optional[float] = None) -> ReverifyResult:
        """Cheap probe of the source. valid -> confidence restored + clock reset; invalid
        -> the artifact is invalidated before it can be reused."""
        r = self._get(artifact_id)
        if r is None:
            raise KeyError(f"KVE: no artifact {artifact_id!r}")
        now = time.time() if now is None else now
        valid = True if self._prober is None \
            else bool(self._prober(self.provenance(artifact_id)))
        if valid:
            self._db.execute(
                "UPDATE kve_artifacts SET base_confidence=1.0, last_verified=?, "
                "status='active' WHERE artifact_id=?", (now, artifact_id))
            return {"valid": True, "confidence": 1.0}
        self.invalidate(artifact_id)
        return {"valid": False, "confidence": 0.0}

    def invalidate(self, artifact_id: str) -> None:
        self._db.execute("UPDATE kve_artifacts SET status='invalid' WHERE artifact_id=?",
                         (artifact_id,))
        if self._mgc is not None:  # archive, never delete
            self._mgc.archive(artifact_id, content={"organ": "KVE"}, kind="knowledge")
        self._audit(artifact_id)

    # -- internals ----------------------------------------------------------
    def _get(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT artifact_id,volatility_class,base_confidence,last_verified,"
            "source_type,source_uri,observed_at,verified_at,status "
            "FROM kve_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if row is None:
            return None
        cols = ["artifact_id", "volatility_class", "base_confidence", "last_verified",
                "source_type", "source_uri", "observed_at", "verified_at", "status"]
        return dict(zip(cols, row))

    def _audit(self, artifact_id: str) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "KVE",
            "action_type": "ARCHIVE", "object_ids": [artifact_id],
            "payload": {"capability_class": "knowledge", "note": "invalidated"},
            "evidence_confidence": 1.0, "evidence_source": "KVE",
            "prev_hash": "", "hash": ""})
