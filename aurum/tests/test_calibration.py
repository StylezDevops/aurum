"""M4 — threshold calibration framing. The human sets the VALUE (which error is worse, per
threshold); the system finds the optimum GIVEN that direction and PROPOSES it. The system must
NEVER auto-apply a threshold/weight change — a self-tuning loss function optimizes toward "whatever
fires least", the opposite of the declared direction. Evidence finds the optimum; it never chooses
the loss function.
"""
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.novel.epistemic_governor import EpistemicGovernor

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


def _predictive_history(n=12):
    out = []
    for i in range(n):
        failed = i % 2 == 0
        comp = {c: 0.0 for c in EpistemicGovernor()._fit_weights([])  # zero-init all components
                } if False else {"verifier_disagreement": 0.9 if failed else 0.0,
                                 "retrieval_conflict": 0.0, "tool_failure_rate": 0.0,
                                 "policy_ambiguity": 0.0, "historical_failure_similarity": 0.0}
        out.append({"components": comp, "failed": failed})
    return out


# ── (f) calibrate PROPOSES; it does not auto-apply (the M4 hard constraint) ────
def test_calibrate_does_not_auto_apply(tmp_path):
    eg = EpistemicGovernor()
    prior = dict(eg.weights)
    samples = _predictive_history()                 # a candidate that DOES generalize

    proposal = eg.calibrate(history=samples)        # default: PROPOSE only
    assert proposal["verifier_disagreement"] == max(proposal.values())   # a real proposal
    assert eg.weights == prior                      # ...but NOT applied (hard constraint)

    applied = eg.calibrate(history=samples, human_ratified=True)   # explicit human ratification
    assert eg.weights == applied                    # only now is it applied
    assert eg.weights != prior


def test_calibration_report_is_proposal_only(tmp_path):
    from aurum.kernel import GovernanceKernel
    from aurum.observability import calibration as cal

    k = GovernanceKernel(home=str(tmp_path))
    report = cal.calibration_report(k)
    assert report["proposals"]
    for _name, entry in report["proposals"].items():
        # every threshold carries the operator-declared DIRECTION and is a PROPOSAL — never applied.
        assert "direction" in entry and "current" in entry
        assert entry["status"] in ("proposal", "insufficient_evidence")
    # with a fresh/thin ledger the system must NOT fabricate a recalibration — honest seed holds.
    assert any(e["status"] == "insufficient_evidence" for e in report["proposals"].values())
    # there is NO auto-apply path in the module (evidence finds the optimum; never chooses it).
    assert not hasattr(cal, "apply_calibration") and not hasattr(cal, "auto_tune")


def test_thresholds_declare_a_direction_not_a_measurement():
    from aurum.observability.calibration import THRESHOLD_DIRECTIONS
    # the asymmetry the operator declared: promotion conservative, demotion fast.
    assert THRESHOLD_DIRECTIONS["ag_promotion"]["direction"] == "conservative"
    assert THRESHOLD_DIRECTIONS["ag_demotion"]["direction"] == "fast"
    assert THRESHOLD_DIRECTIONS["screener_block"]["direction"] == "low"   # over-block freely
