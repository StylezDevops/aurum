"""Phase C — Familiarity Factor with atrophy: AURUM_ERR_029 / 032 / 038.

effective_authority = base × familiarity(domain); familiarity = grounded-good outcomes weighted
by KVE validity and per-volatility time-decay; recovery via gated human-grounded outcomes;
proxy never counts. Driven through the real kernel (PK→AG→CA→EL + KVE + familiarity) with an
injected DomainClock so decay is deterministic and fast-forwardable with no real waiting.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.durability.clock import DAY, DomainClock
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB", "KVE"),
    reason="governance organs not all built",
)

FAST_HALF_LIFE_DAYS = 14


def _k(tmp_path, clock):
    return GovernanceKernel(home=str(tmp_path), domain_clock=clock)


def _register_domain(k, domain, clock, *, volatility="FAST", confidence=1.0):
    """Register a domain's knowledge artifact in KVE so it has a validity + volatility."""
    k.kve.register({"artifact_id": domain, "volatility_class": volatility,
                    "confidence": confidence, "last_verified": clock.now()}, now=clock.now())


# ── AURUM_ERR_029 — atrophy decay: KVE invalidation collapses familiarity, tightening the gate ─

def test_AURUM_ERR_029_atrophy_decay_tightens_gate(tmp_path):
    clock = DomainClock(1_000_000.0)
    k = _k(tmp_path, clock)
    domain = "d365"
    _register_domain(k, domain, clock)                 # validity 1.0, FAST
    for _ in range(50):                                # build deep familiarity in the domain
        k.ag.record_familiarity(domain, clock.now())

    # Familiar: a domain-scoped write is permitted (effective authority clears the code band).
    eff_n = k.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0, volatility="FAST")
    assert eff_n == pytest.approx(50.0)
    d_ok = k.govern(to_action("write_file", {"path": "/x", "content": "y"}, domain=domain))
    assert d_ok.allow is True, f"familiar domain should permit: {d_ok}"

    # KVE invalidates the domain's knowledge (an API revamp) → validity → 0 → effective_n → 0 →
    # familiarity floors → the SAME action is now refused, despite the 50 historical successes.
    k.kve.invalidate(domain)
    assert k.kve.confidence(domain, now=clock.now()) == 0.0
    assert k.ag.familiarity_effective_n(domain, now=clock.now(), validity=0.0,
                                        volatility="FAST") == 0.0
    d_bad = k.govern(to_action("write_file", {"path": "/x", "content": "y"}, domain=domain))
    assert d_bad.allow is False and d_bad.rule_id == "ag:ceiling"


# ── AURUM_ERR_032 — recovery without a proxy backdoor ─────────────────────────────────────────

def test_AURUM_ERR_032_recovery_grounded_only_never_proxy(tmp_path):
    clock = DomainClock(1_000_000.0)
    k = _k(tmp_path, clock)
    domain = "d365"
    _register_domain(k, domain, clock)
    cc = "file_write"

    def factor():
        return k.ag.familiarity_factor(domain, now=clock.now(), validity=1.0, volatility="FAST")

    assert factor() == pytest.approx(0.25)             # floor — brand-new/floored domain

    # PROXY success must NOT build familiarity (the forbidden-feedback guard).
    for i in range(15):
        k.observe_outcome(to_action("write_file", {"path": "/x"}, domain=domain),
                          {"completed": True, "quality": 1.0})
    assert k.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0,
                                        volatility="FAST") == 0.0
    assert factor() == pytest.approx(0.25), "proxy success must not move familiarity"

    # A grounded BAD verdict must NOT build familiarity either.
    k.record_outcome_verdict("bad-1", cc, satisfied=False, domain=domain)
    assert k.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0,
                                        volatility="FAST") == 0.0

    # GATED human-grounded-GOOD outcomes rebuild familiarity — even from the floor (recovery is
    # always open; the domain is never permanently bricked). 15 → the reference's ~0.70.
    for i in range(15):
        k.record_outcome_verdict(f"good-{i}", cc, satisfied=True, domain=domain)
    assert k.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0,
                                        volatility="FAST") == pytest.approx(15.0)
    assert factor() == pytest.approx(0.70, abs=1e-9)
    assert factor() > 0.25                              # climbed back above the floor


# ── AURUM_ERR_038 — experience time-decay (validity AND age), per volatility class ────────────

def test_AURUM_ERR_038_experience_time_decay(tmp_path):
    clock = DomainClock(0.0)
    k = _k(tmp_path, clock)
    domain = "x"
    for _ in range(100):
        k.ag.record_familiarity(domain, clock.now())   # 100 outcomes at t0

    def eff_n(volatility):
        return k.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0,
                                            volatility=volatility)

    assert eff_n("FAST") == pytest.approx(100.0)        # all fresh

    clock.advance(365 * DAY)                            # a fake YEAR of Domain Time, no waiting
    assert eff_n("FAST") < 1e-3                          # FAST experience has faded to ~0
    assert eff_n("STATIC") == pytest.approx(50.0, abs=1.0)  # STATIC (365d h-l) ~half remains

    # Recent practice preserves familiarity: fresh grounded outcomes at the new 'now' restore it.
    for _ in range(100):
        k.ag.record_familiarity(domain, clock.now())
    assert eff_n("FAST") > 78.0                          # continuous practice keeps it high


# ── Integration: the headline — competent in one domain, gated in an unfamiliar one ───────────

def test_familiarity_gates_unfamiliar_domain_but_not_familiar(tmp_path):
    clock = DomainClock(1_000_000.0)
    k = _k(tmp_path, clock)
    _register_domain(k, "d365", clock)
    for _ in range(50):
        k.ag.record_familiarity("d365", clock.now())

    # Same base authority (file_write, code band) — familiar domain permits, unfamiliar refuses.
    assert k.govern(to_action("write_file", {"path": "/x"}, domain="d365")).allow is True
    denied = k.govern(to_action("write_file", {"path": "/x"}, domain="k8s_internals"))
    assert denied.allow is False and denied.rule_id == "ag:ceiling"
    # A domain-LESS action is unaffected (base band; no familiarity penalty) — back-compat.
    assert k.govern(to_action("write_file", {"path": "/x"})).allow is True


def test_unknown_domain_validity_is_failsafe_low(tmp_path):
    clock = DomainClock(1_000_000.0)
    k = _k(tmp_path, clock)
    # 'mars' is never registered in KVE → unknown validity → treated as decayed (0.0) → floor →
    # a domain-scoped consequential action is refused (fail-safe: unknown knowledge = stale).
    d = k.govern(to_action("write_file", {"path": "/x"}, domain="mars"))
    assert d.allow is False and d.rule_id == "ag:ceiling"


# ── Persistence-as-projection: familiarity survives --rm by replaying the ledger ──────────────

def test_familiarity_rehydrates_across_restart(tmp_path):
    home = str(tmp_path)
    clock = DomainClock(1_000_000.0)
    domain, cc = "d365", "file_write"
    k1 = GovernanceKernel(home=home, domain_clock=clock)
    _register_domain(k1, domain, clock)
    for i in range(20):
        k1.record_outcome_verdict(f"t-{i}", cc, satisfied=True, domain=domain)
    n1 = k1.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0, volatility="FAST")
    assert n1 == pytest.approx(20.0)

    # A fresh kernel on the same durable home rebuilds familiarity by REPLAYING the EL
    # grounded-outcome stream — a projection, not a stored mutable scalar.
    k2 = GovernanceKernel(home=home, domain_clock=clock)
    n2 = k2.ag.familiarity_effective_n(domain, now=clock.now(), validity=1.0, volatility="FAST")
    assert n2 == pytest.approx(20.0)


def test_cold_start_familiarity_is_empty_noop(tmp_path):
    clock = DomainClock(1_000_000.0)
    k = _k(tmp_path, clock)
    # No grounded outcomes anywhere → every domain is at the floor, effective_n 0 (clean no-op).
    assert k.ag.familiarity_effective_n("anything", now=clock.now(), validity=1.0,
                                        volatility="FAST") == 0.0
    assert k.ag.familiarity_factor("anything", now=clock.now(), validity=1.0,
                                   volatility="FAST") == pytest.approx(0.25)
