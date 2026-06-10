"""MPD evidence-confidence loop — CLOSED now that OI + BB exist.

The detector's findings now FEED BACK (the loop the MPD docstring originally deferred
"until OI/BB exist"):
  • a grounded 'success' that a later outcome on the SAME object contradicts is
    auto-quarantined and DISTRUSTED by the verify-gated rehydration — a poisoned
    success does not rebuild familiarity / earned scope on --rm;
  • `scan_memory_integrity()` surfaces those suspects to BB for OWNER REVIEW
    (idempotent, never auto-deletes).
The uniform-confidence signature stays OWNER-REVIEW only (`scan()`): the spine's own
decisions are legitimately all confidence 1.0, so it is never an automatic discount.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.build_state import is_built
from aurum.durability.clock import DomainClock
from aurum.durability.el import EvidenceLedger
from aurum.kernel import GovernanceKernel
from aurum.observability.mpd import MemoryPoisoningDetector

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB", "MPD", "OI", "TL"),
    reason="governance organs not all built",
)


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _outcome_verdict(oid, satisfied, *, source="GOV"):
    """Mirror the kernel's GOVERNANCE_DECISION `outcome_verdict` event shape."""
    return {"event_id": "", "timestamp": "", "source_organ": source,
            "action_type": "GOVERNANCE_DECISION", "object_ids": [oid],
            "payload": {"outcome": "outcome_verdict", "satisfied": satisfied,
                        "satisfaction_source": "human", "capability_class": "file_write"},
            "evidence_confidence": 1.0, "evidence_source": source,
            "prev_hash": "", "hash": ""}


def _success(el, oid):
    """The success outcome_verdict event for `oid` (the one MPD may quarantine)."""
    return next(e for e in el.query({"action_type": "GOVERNANCE_DECISION"})
                if e["object_ids"] == [oid] and e["payload"]["satisfied"] is True)


# ── MPD now recognizes the grounded-outcome trust stream ───────────────────────
def test_mpd_recognizes_contradicted_grounded_outcome():
    el = _el()
    el.append(_outcome_verdict("task-poison", True))    # recorded grounded success
    el.append(_outcome_verdict("task-other", True))     # unrelated success (never contradicted)
    el.append(_outcome_verdict("task-poison", False))   # later contradiction on the SAME task
    mpd = MemoryPoisoningDetector(el)
    quarantined = mpd.quarantined_evidence()
    assert _success(el, "task-poison")["event_id"] in quarantined
    # an unrelated, never-contradicted success is NOT quarantined
    assert _success(el, "task-other")["event_id"] not in quarantined


def test_effective_confidence_discounts_only_quarantined():
    el = _el()
    el.append(_outcome_verdict("t1", True))
    el.append(_outcome_verdict("t1", False))   # contradicts the t1 success
    el.append(_outcome_verdict("t2", True))    # clean — never contradicted
    mpd = MemoryPoisoningDetector(el)
    t1_ok, t2_ok = _success(el, "t1"), _success(el, "t2")
    assert mpd.effective_confidence(t1_ok) == 0.0            # poisoned → fully distrusted
    assert mpd.effective_confidence(t2_ok) == 1.0            # clean → stored confidence
    # custom discount + precomputed set (no re-scan)
    qset = mpd.quarantined_evidence()
    assert mpd.effective_confidence(t1_ok, discount=0.1, quarantined=qset) == pytest.approx(0.1)


def test_uniform_confidence_is_owner_review_not_auto_quarantined():
    el = _el()
    for i in range(6):                          # 6 successes, all confidence 1.0, none contradicted
        el.append(_outcome_verdict(f"t{i}", True))
    mpd = MemoryPoisoningDetector(el, min_cluster=4)
    assert len(mpd.scan()) == 6                 # owner-review surface flags the uniform cluster ...
    assert mpd.quarantined_evidence() == set()  # ... but NONE auto-quarantined (no contradiction)


# ── End-to-end: a poisoned grounded success does not survive --rm ──────────────
def test_poisoned_grounded_outcome_not_rehydrated(tmp_path):
    home = str(tmp_path)
    clock = DomainClock(1_000_000.0)
    domain, cc = "d365", "file_write"
    k1 = GovernanceKernel(home=home, domain_clock=clock)
    k1.kve.register({"artifact_id": domain, "volatility_class": "FAST",
                     "confidence": 1.0, "last_verified": clock.now()}, now=clock.now())
    for i in range(5):
        k1.record_outcome_verdict(f"good-{i}", cc, satisfied=True, domain=domain)
    k1.record_outcome_verdict("poison", cc, satisfied=True, domain=domain)   # a 6th success ...
    n1 = k1.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0, volatility="FAST")
    assert n1 == pytest.approx(6.0)                       # live: the poison success counted
    k1.record_outcome_verdict("poison", cc, satisfied=False, domain=domain)  # ... later contradicted

    # Fresh kernel on the same durable home: rehydration must DISTRUST the contradicted success.
    k2 = GovernanceKernel(home=home, domain_clock=clock)
    n2 = k2.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0, volatility="FAST")
    assert n2 == pytest.approx(5.0)   # only the 5 legit successes — the poisoned one is quarantined


def test_scan_memory_integrity_owner_review_idempotent(tmp_path):
    clock = DomainClock(1_000_000.0)
    k = GovernanceKernel(home=str(tmp_path), domain_clock=clock)
    k.record_outcome_verdict("poison", "file_write", satisfied=True)
    k.record_outcome_verdict("poison", "file_write", satisfied=False)   # contradicts → poisoned
    r1 = k.scan_memory_integrity()
    assert len(r1["quarantined"]) == 1
    assert len(r1["reviewed"]) == 1
    assert len(k.bb.search("memory-poisoning suspect")) == 1   # owner-review postmortem written
    # idempotent: a second scan does not re-write the same suspect (BB is append-only)
    r2 = k.scan_memory_integrity()
    assert r2["quarantined"] == r1["quarantined"]
    assert r2["reviewed"] == []
    assert len(k.bb.search("memory-poisoning suspect")) == 1


def test_clean_kernel_quarantines_nothing(tmp_path):
    clock = DomainClock(1_000_000.0)
    k = GovernanceKernel(home=str(tmp_path), domain_clock=clock)
    for i in range(5):
        k.record_outcome_verdict(f"good-{i}", "file_write", satisfied=True)
    assert k.scan_memory_integrity() == {"quarantined": [], "reviewed": []}
