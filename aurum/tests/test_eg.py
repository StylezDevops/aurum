"""EG — Epistemic Governor. Composite U, EWMA smoothing, branch, validated calibrate."""
from __future__ import annotations

import os
import tempfile

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.novel.epistemic_governor import EpistemicGovernor, _COMPONENTS


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _step(**over):
    s = {c: 0.0 for c in _COMPONENTS}
    s.update(over)
    return s


# -- composite U (accept a) ------------------------------------------------
def test_U_bounded_and_weighted_from_components():
    eg = EpistemicGovernor()
    out = eg.score_step(_step(verifier_disagreement=1.0, retrieval_conflict=1.0,
                              tool_failure_rate=1.0, policy_ambiguity=1.0,
                              historical_failure_similarity=1.0), {})
    assert abs(out["U"] - 1.0) < 1e-9          # all components maxed -> U=1
    assert set(out["components"]) == set(_COMPONENTS)
    # fresh instance (EWMA is stateful by design): all-zero -> U=0
    assert EpistemicGovernor().score_step(_step(), {})["U"] == 0.0


def test_weights_sum_to_one():
    assert abs(sum(EpistemicGovernor().weights.values()) - 1.0) < 1e-9


# -- EWMA smoothing of sparse components -----------------------------------
def test_sparse_spike_is_damped_instantaneous_is_not():
    eg = EpistemicGovernor(ewma_alpha=0.4)
    # establish a calm baseline for the sparse component
    eg.smooth_sparse("tool_failure_rate", 0.0)
    damped = eg.smooth_sparse("tool_failure_rate", 1.0)  # isolated spike
    assert damped < 1.0                         # smoothed, not the raw spike
    # instantaneous signal passes straight through
    assert eg.smooth_sparse("verifier_disagreement", 1.0) == 1.0


def test_sustained_pattern_moves_sparse_component_up():
    eg = EpistemicGovernor(ewma_alpha=0.4)
    eg.smooth_sparse("tool_failure_rate", 0.0)
    vals = [eg.smooth_sparse("tool_failure_rate", 1.0) for _ in range(5)]
    assert vals[-1] > vals[0]                   # converges upward under a sustained pattern


# -- branching (accept b) --------------------------------------------------
def test_should_branch_on_threshold():
    eg = EpistemicGovernor(threshold=0.5)
    assert eg.should_branch({"U": 0.7}) is True
    assert eg.should_branch({"U": 0.3}) is False


def test_freeze_and_branch_forks_from_last_good_step():
    el = _el()
    eg = EpistemicGovernor(el=el, threshold=0.5)
    traj = {"id": "t1", "steps": [
        _step(verifier_disagreement=0.0),       # good (idx 0)
        _step(verifier_disagreement=0.9),       # poisoned (idx 1)
        _step(verifier_disagreement=0.9)]}      # idx 2 (tip)
    branches = eg.freeze_and_branch(traj)
    alt = next(b for b in branches if b["kind"] == "alternate")
    assert alt["from_step"] == 0                # fork from the last good step
    # branch decision logged to EL with the component breakdown (accept d)
    rows = el.query({"source_organ": "EG", "action_type": "BRANCH"})
    assert rows and "components" in rows[0]["payload"]


def test_scoring_is_deterministic_and_idempotent_for_sparse_signals():
    # The bug: score_step mutated the shared EWMA, so re-scoring (freeze_and_branch / should_branch /
    # audit) double-folded the sparse signals → U drifted and the fork point was non-deterministic.
    # The instantaneous-signal test above never exercised the EWMA path. This does.
    eg = EpistemicGovernor(threshold=0.5, ewma_alpha=0.4)
    traj = {"id": "t", "steps": [
        _step(tool_failure_rate=0.0),   # calm (idx 0 — lowest U)
        _step(tool_failure_rate=0.2),
        _step(tool_failure_rate=0.6),
        _step(tool_failure_rate=1.0)]}  # tip (distinct values so context-location is unambiguous)

    u_tip_1 = eg.score_step(traj["steps"][-1], traj)["U"]
    eg.should_branch(traj)                       # intervening scoring calls (would corrupt EWMA)
    eg.freeze_and_branch(traj)
    u_tip_2 = eg.score_step(traj["steps"][-1], traj)["U"]
    assert u_tip_1 == u_tip_2                     # idempotent: scoring did not drift the state

    fb1 = eg.freeze_and_branch(traj)
    fb2 = eg.freeze_and_branch(traj)
    assert fb1 == fb2                             # deterministic fork point across repeated calls
    alt = next(b for b in fb1 if b["kind"] == "alternate")
    assert alt["from_step"] == 0                  # the calm (lowest-U) step, correctly chosen


def test_select_avoids_poisoned_branch():
    eg = EpistemicGovernor()
    chosen = eg.select([{"kind": "continue", "U": 0.8},
                        {"kind": "alternate", "U": 0.1}])
    assert chosen["kind"] == "alternate"


# -- calibration with holdout validation (accept c, e) ---------------------
def _samples(signal, n=8):
    # `signal` component is elevated before failures; a noise component is not.
    out = []
    for i in range(n):
        failed = i % 2 == 0
        comp = {c: 0.0 for c in _COMPONENTS}
        if failed:
            comp[signal] = 0.9
            comp["retrieval_conflict"] = 0.9  # coincidental in fit half only handled below
        out.append({"components": comp, "failed": failed})
    return out


def test_calibrate_upweights_predictive_component():
    eg = EpistemicGovernor()
    samples = []
    # verifier_disagreement consistently precedes failure across all samples
    for i in range(12):
        failed = i % 2 == 0
        comp = {c: 0.0 for c in _COMPONENTS}
        comp["verifier_disagreement"] = 0.9 if failed else 0.0
        samples.append({"components": comp, "failed": failed})
    w = eg.calibrate(history=samples)
    assert w["verifier_disagreement"] == max(w.values())
    assert abs(sum(w.values()) - 1.0) < 1e-9    # renormalised


def test_calibrate_rejects_overfit_keeps_prior():
    eg = EpistemicGovernor()
    prior = dict(eg.weights)
    # fit half: 'policy_ambiguity' precedes failure; holdout half: it does NOT (noise).
    samples = []
    for i in range(6):  # fit partition (first half)
        failed = i % 2 == 0
        comp = {c: 0.0 for c in _COMPONENTS}
        comp["policy_ambiguity"] = 0.9 if failed else 0.0
        samples.append({"components": comp, "failed": failed})
    for i in range(6):  # holdout partition: signal flipped (no generalisation)
        failed = i % 2 == 0
        comp = {c: 0.0 for c in _COMPONENTS}
        comp["policy_ambiguity"] = 0.0 if failed else 0.9
        samples.append({"components": comp, "failed": failed})
    w = eg.calibrate(history=samples)
    assert w == prior                            # rejected -> prior kept


def test_calibrate_insufficient_history_keeps_prior():
    eg = EpistemicGovernor()
    assert eg.calibrate(history=[]) == dict(eg.weights)
