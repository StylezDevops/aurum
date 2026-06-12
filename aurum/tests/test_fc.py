"""Phase E (checkpoint 2) — FC forced contestability: AURUM_ERR_070/071/072.

Inverted scrutiny for the irreversible class (scrutiny rises with authority), periodic
re-justification of elite pathways (observe on, evaluate evidence-gated, failed re-justification
reuses the existing graduated demotion), and the by-construction no-authority-widening guarantee.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.build_state import is_built
from aurum.durability.clock import DAY
from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.institutional import ForcedContestability
from aurum.novel.authority_governor import AuthorityGovernor

pytestmark = pytest.mark.skipif(not is_built("FC"), reason="FC not built")


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


# ── AURUM_ERR_070 — inverted scrutiny for the irreversible class ─────────────────────────────

def test_AURUM_ERR_070_inverted_scrutiny_irreversible_rises_with_authority():
    fc = ForcedContestability(_el())
    irreversible = {"irreversible": True}
    reversible = {}

    # IRREVERSIBLE: scrutiny RISES monotonically with acting authority (trust-but-verify-MORE).
    rates = [fc.scrutiny(irreversible, a).sampling_rate for a in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert all(b >= a for a, b in zip(rates, rates[1:]))
    assert rates[-1] > rates[0]
    hi = fc.scrutiny(irreversible, 0.95)
    assert hi.inverted is True and hi.logging_depth == "full" and hi.sampling_rate >= 0.9
    assert fc.scrutiny(irreversible, 1.0).sampling_rate == pytest.approx(1.0)   # saturates at 1.0

    # REVERSIBLE: "authority buys speed" — scrutiny does NOT rise with authority, but never 0.
    rev_rates = [fc.scrutiny(reversible, a).sampling_rate for a in (0.0, 0.5, 1.0)]
    assert all(b <= a for a, b in zip(rev_rates, rev_rates[1:]))
    assert fc.scrutiny(reversible, 1.0).inverted is False
    assert fc.scrutiny(reversible, 1.0).sampling_rate >= 0.05   # uniform audit floor, never zero


# ── AURUM_ERR_071 — periodic re-justification + demotion via the existing path ───────────────

def test_AURUM_ERR_071_rejustification_schedules_and_failed_demotes():
    el = _el()
    ag = AuthorityGovernor(el=el)
    ag.set_authority("dataverse", 0.95)
    assert ag.band("dataverse") == "full"

    # FC's ONLY authority-side handle is a DEMOTE-only callback delegating to AG's EXISTING
    # graduated-sanction path — it is wired to demote, and has no way to widen authority.
    fc = ForcedContestability(
        el, demote=lambda cc, sev: ag.apply_outcome(cc, good=False, grounded=False, severity=sev))
    fc.observe("dataverse", 0.95, now=0.0)

    # Fresh pathway (trust-age 0) → not yet scheduled.
    assert fc.evaluate(now=0.0)["sufficient"] is False
    # Past the trust-age threshold → a re-justification schedule is emitted.
    sched = fc.evaluate(now=31 * DAY)
    assert sched["sufficient"] is True
    assert sched["scheduled"][0]["capability_class"] == "dataverse"
    first_n = sched["scheduled"][0]["n_invocations"]

    # PASS preserves authority (no demotion), and the NEXT burden GROWS (age+success → more).
    fc.record_rejustification_outcome("dataverse", passed=True, now=31 * DAY)
    assert ag.band("dataverse") == "full"
    grown = fc.evaluate(now=62 * DAY)
    assert grown["scheduled"][0]["n_invocations"] > first_n

    # FAIL → graduated demotion via the reused AG path (full → one band down).
    fc.record_rejustification_outcome("dataverse", passed=False, now=62 * DAY)
    assert ag.band("dataverse") != "full"


def test_fc_has_no_authority_widening_path():
    el = _el()
    ag = AuthorityGovernor(el=el)
    ag.set_authority("dataverse", 0.95)
    fc = ForcedContestability(
        el, demote=lambda cc, sev: ag.apply_outcome(cc, good=False, grounded=False, severity=sev))
    fc.observe("dataverse", 0.95, now=0.0)

    # By construction: FC holds no AuthorityGovernor and exposes no authority-raising method —
    # its only authority effect is the demote-only callback.
    assert not hasattr(fc, "ag")
    # However many times it passes, FC never raises authority above what AG set.
    for i in range(5):
        fc.record_rejustification_outcome("dataverse", passed=True, now=float(i))
    assert ag.authority("dataverse") <= 0.95
    assert ag.band("dataverse") == "full"            # passing never promotes


# ── AURUM_ERR_072 — early-life no-op + constitutional params ─────────────────────────────────

def test_AURUM_ERR_072_early_life_noop_and_constitutional_params():
    fc = ForcedContestability(_el())
    # No observations → nothing scheduled (clean no-op; quiet ≠ broken).
    assert fc.evaluate(now=0.0) == {"scheduled": [], "sufficient": False}
    # A pathway BELOW the authority threshold is never forced to re-justify, however old.
    fc.observe("low_trust", 0.50, now=0.0)
    assert fc.evaluate(now=365 * DAY)["sufficient"] is False

    # Constitutional params: the agent cannot lower its own threshold to exempt elite pathways.
    with pytest.raises(PermissionError):
        fc.set_parameters({"authority_threshold": 0.10})
    fc.set_parameters({"authority_threshold": 0.80}, human_gate=True)
