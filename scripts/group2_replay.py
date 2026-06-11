#!/usr/bin/env python3
"""Group 2 scenario / replay harness — demonstrate the evidence-gated mechanisms WORKING.

The "Group 2" organs are BUILT, but their CLAIMS depend on real operational evidence that cannot
be synthesised (a 30-day-aged elite pathway; the owner's real judgments; hundreds of real probe
takes; runs across real environments). This harness proves the **mechanism + wiring** of each one
end-to-end, deterministically — clock advanced, a batch of owner verdicts, a scripted route-trend,
per-environment provenance — WITHOUT faking the calibration or flipping any build_state flag. It is
the difference between "prove the mechanism" (here) and "calibrate the threshold" (real cage turns):

  • FC.evaluate            — advance the clock +31d → a high-authority pathway is SCHEDULED for
                             re-justification (the calendar gate fires). Mechanism, not calibration.
  • OI.proxy_calibration   — a batch of owner verdicts → the proxy weight tracks proxy↔human
                             agreement (a divergent proxy is down-weighted). The arithmetic, on a
                             scripted batch; real calibration needs the owner's REAL judgments.
  • CS-EQ leg-2            — a scripted route-trend → the equilibrium STATISTIC computes (regime
                             distribution + the differential D), and equilibrium_report() STILL
                             returns claim="deferred". The honest non-assertion is the point.
  • AG per-environment    — observe a class across dev/staging/prod → the environment PROVENANCE is
                             recorded (earned_in). Recorded, not yet acted on (per-env scoring is
                             the deferred calibration work).

Run:  python scripts/group2_replay.py
The scenario functions return plain dicts and are asserted by tests/test_group2_harness.py.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os
import sys
import tempfile

# Resolve the organs package whether run from the repo root or under the test rootdir.
_PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "aurum"))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from aurum.cseq import (  # noqa: E402
    INCENTIVE_STABLE, ConstitutionalStability, CostWeights, PathCost, RouteObservation,
)
from aurum.durability.clock import DAY  # noqa: E402
from aurum.durability.evidence_ledger import EvidenceLedger  # noqa: E402
from aurum.institutional.forced_contestability import ForcedContestability  # noqa: E402
from aurum.novel.authority_governor import AuthorityGovernor  # noqa: E402
from aurum.novel.outcome_interpreter import OutcomeInterpreter  # noqa: E402


def _el() -> EvidenceLedger:
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _dbpath(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(), name)


# ── FC: a high-authority pathway is scheduled for re-justification at +31 days ──
def scenario_fc(now: float = 1_000_000.0) -> dict:
    fc = ForcedContestability(_el())
    cc = "network"
    fc.observe(cc, authority=0.95, now=now)          # elite pathway: the trust-age clock starts
    before = fc.evaluate(now=now)                    # day 0 — nothing due yet (quiet != broken)
    after = fc.evaluate(now=now + 31 * DAY)          # +31d — past the 30-day trust-age threshold
    return {
        "capability_class": cc,
        "before": before, "after": after,
        "mechanism_proven": (not before["sufficient"]) and after["sufficient"],
        "note": "MECHANISM: the calendar gate fires at +31d. CALIBRATION (a real 30-day-aged elite "
                "pathway) needs real operation — the clock here is injected, not waited out.",
    }


# ── OI: a batch of owner verdicts down-weights a divergent proxy ───────────────
def scenario_oi() -> dict:
    oi = OutcomeInterpreter(_dbpath("oi.db"), el=_el())
    for i in range(4):                               # 4 tasks the PROXY judged satisfied
        oi.interpret({"task_id": f"t{i}", "completed": True, "proxy_satisfied": True,
                      "capability_class": "network"}, goal_id="g")
    # the owner judges later: 3 agree, 1 diverges (proxy said yes, owner says no)
    for tid, satisfied in (("t0", True), ("t1", True), ("t2", True), ("t3", False)):
        oi.record_human_verdict(tid, {"satisfied": satisfied})
    cal = oi.proxy_calibration()
    return {
        "calibration": cal,
        "mechanism_proven": cal["samples"] == 4 and cal["proxy_weight"] < 1.0,
        "note": "MECHANISM: proxy_weight = proxy-vs-human agreement (here 3/4 = 0.75; a divergent "
                "proxy is down-weighted). CALIBRATION needs the owner's REAL judgments, not a batch.",
    }


# ── CS-EQ leg-2: the equilibrium statistic computes; the CLAIM stays deferred ──
def scenario_cseq(n: int = 10) -> dict:
    cs = ConstitutionalStability(_el())
    governed = PathCost(tokens=100, latency_s=1.0, steps=3, p_success=0.95)
    # an ungoverned route that IS available but is expensive-per-success → D finite & positive
    ungoverned = PathCost(tokens=5000, latency_s=2.0, steps=10, p_success=0.30)
    weights = CostWeights()
    for _ in range(n):                               # scripted trend: available route, NOT taken
        obs = RouteObservation(route_available=True, agent_attempted=False,
                               attempt_succeeded=False)
        cs.record_decision_equilibrium("network", governed, ungoverned, obs, weights)
    report = cs.equilibrium_report()
    return {
        "report": report,
        "mechanism_proven": (report["observations"] == n
                             and report["regime_distribution"].get(INCENTIVE_STABLE) == n
                             and report["incidents"] == 0
                             and report["claim"] == "deferred"),
        "note": "MECHANISM: the regime distribution + the differential D compute over a scripted "
                "trend, and the report HONESTLY returns claim='deferred'. CALIBRATION (the "
                "equilibrium 'holds' verdict) needs hundreds of REAL probe takes — never synthesised.",
    }


# ── AG: per-environment authority provenance is recorded ───────────────────────
def scenario_ag_environment() -> dict:
    ag = AuthorityGovernor(el=_el())
    cc = "deploy"
    signals = {"tl_tier": 3, "eg_uncertainty": 0.0, "hvp_pass_rate": 1.0, "oi_trend": 1.0}
    for i, env in enumerate(("dev", "staging", "prod"), start=1):
        ag.observe(cc, signals, now=float(i), environment=env)
    earned_in = ag.earned_in(cc)
    return {
        "earned_in": earned_in,
        "explain_earned_in": ag.explain(cc)["earned_in"],
        "mechanism_proven": earned_in == ["dev", "staging", "prod"],
        "note": "MECHANISM: which environments earned a class's authority is RECORDED (earned_in) + "
                "recoverable. NOT YET ACTED ON — per-environment keyed authority (prod resets to "
                "floor / dev-vs-prod scoring) is the deferred calibration work.",
    }


def run_all() -> dict:
    return {
        "fc_rejustification": scenario_fc(),
        "oi_proxy_calibration": scenario_oi(),
        "cseq_leg2_equilibrium": scenario_cseq(),
        "ag_per_environment": scenario_ag_environment(),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # be robust to a cp1252 Windows console
    except Exception:
        pass
    results = run_all()
    print("=" * 78)
    print("Group 2 scenario / replay harness — mechanisms demonstrated (calibration deferred)")
    print("=" * 78)
    all_ok = True
    for name, res in results.items():
        ok = res.get("mechanism_proven")
        all_ok = all_ok and bool(ok)
        print(f"\n[{'OK ' if ok else 'XX '}] {name}")
        print(f"      {res['note']}")
    print("\n" + "-" * 78)
    print(f"All Group 2 mechanisms demonstrated: {all_ok}")
    print("NOTE: this proves MECHANISM + wiring only. No build_state flag is flipped; the "
          "evidence-gated CLAIMS (FC firing on a real aged pathway, OI's real proxy calibration, "
          "the CS-EQ equilibrium verdict, per-env AG scoring) still await real cage evidence.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
