"""KVE — Knowledge Validity Engine. Volatility decay, staleness, re-verify, invalidate."""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.durability.knowledge_validity_engine import KnowledgeValidityEngine
from aurum.durability.memory_garbage_collector import MemoryGarbageCollector

DAY = 86400.0


def _kve(**kw):
    return KnowledgeValidityEngine(os.path.join(tempfile.mkdtemp(), "kve.db"), **kw)


# -- classification --------------------------------------------------------
def test_classify_by_source_type():
    kve = _kve()
    assert kve.classify({"source_type": "external_api"}) == "FAST"
    assert kve.classify({"source_type": "syntax"}) == "STATIC"
    assert kve.classify({"source_type": "internal_process"}) == "SLOW"
    assert kve.classify({"volatility_class": "FAST"}) == "FAST"  # explicit wins


# -- accept (a): FAST decays and goes stale --------------------------------
def test_fast_artifact_decays_and_flags_stale():
    kve = _kve()
    t0 = 1_000_000.0
    kve.register({"artifact_id": "d365", "source_type": "external_api",
                  "confidence": 1.0, "last_verified": t0}, now=t0)
    assert kve.confidence("d365", now=t0) == 1.0
    # ~2 half-lives (28d) later -> well below threshold
    later = t0 + 28 * DAY
    assert kve.confidence("d365", now=later) < 0.5
    assert "d365" in kve.stale(now=later)


# -- accept (c): STATIC does not decay -------------------------------------
def test_static_artifact_does_not_decay():
    kve = _kve()
    t0 = 1_000_000.0
    kve.register({"artifact_id": "syntax", "source_type": "syntax",
                  "confidence": 1.0, "last_verified": t0}, now=t0)
    far = t0 + 365 * DAY
    assert kve.confidence("syntax", now=far) > 0.99      # effectively no decay
    assert kve.stale(now=far) == []


# -- accept (b): re-verify that finds a change invalidates -----------------
def test_reverify_invalidates_changed_source():
    kve = _kve(prober=lambda prov: False)   # source no longer exists
    kve.register({"artifact_id": "ep", "source_type": "external_api"})
    res = kve.reverify("ep")
    assert res == {"valid": False, "confidence": 0.0}
    assert kve.confidence("ep") == 0.0       # invalidated


def test_reverify_valid_restores_confidence():
    kve = _kve(prober=lambda prov: True)
    t0 = 1_000_000.0
    kve.register({"artifact_id": "ep", "source_type": "external_api",
                  "confidence": 0.3, "last_verified": t0 - 30 * DAY}, now=t0)
    res = kve.reverify("ep", now=t0)
    assert res["valid"] is True and res["confidence"] == 1.0
    assert kve.confidence("ep", now=t0) == 1.0  # clock reset


# -- accept (d): invalidate archives via MGC (never deletes) ---------------
def test_invalidate_archives_via_mgc():
    mgc = MemoryGarbageCollector(os.path.join(tempfile.mkdtemp(), "mgc.db"))
    kve = _kve(mgc=mgc)
    kve.register({"artifact_id": "k1", "source_type": "external_api"})
    kve.invalidate("k1")
    assert mgc.restore("k1") == {"organ": "KVE"}  # archived, recoverable


# -- provenance ------------------------------------------------------------
def test_provenance_is_answerable():
    kve = _kve()
    kve.register({"artifact_id": "k1", "source_type": "external_api",
                  "source_uri": "https://d365/v9", "observed_at": "2026-01-01",
                  "verified_at": "2026-01-01"})
    prov = kve.provenance("k1")
    assert prov["source_uri"] == "https://d365/v9" and prov["source_type"] == "external_api"


def test_invalidation_logged_to_el():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    kve = _kve(el=el)
    kve.register({"artifact_id": "k1", "source_type": "external_api"})
    kve.invalidate("k1")
    rows = el.query({"source_organ": "KVE"})
    assert rows and rows[0]["payload"]["note"] == "invalidated"


def test_unknown_artifact_raises():
    with pytest.raises(KeyError):
        _kve().confidence("nope")
