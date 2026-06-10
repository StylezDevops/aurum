"""Group 2 scenario / replay harness — CI proof that each mechanism fires.

Loads scripts/group2_replay.py and asserts every Group-2 mechanism is demonstrated end-to-end
on a deterministic scenario, AND that the harness stays honest: it proves MECHANISM, never
calibration (CS-EQ still returns claim='deferred'; AG environment is recorded, not scored).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aurum.build_state import is_built

pytestmark = pytest.mark.skipif(
    not is_built("EL", "AG", "OI"),
    reason="governance organs not all built",
)

_HARNESS_PATH = Path(__file__).resolve().parents[2] / "scripts" / "group2_replay.py"


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("group2_replay", _HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_fc_rejustification_fires_at_31_days(harness):
    res = harness.scenario_fc()
    assert res["before"]["sufficient"] is False and res["before"]["scheduled"] == []
    assert res["after"]["sufficient"] is True
    assert [s["capability_class"] for s in res["after"]["scheduled"]] == ["network"]
    assert res["mechanism_proven"] is True


def test_oi_proxy_calibration_down_weights_divergent_proxy(harness):
    res = harness.scenario_oi()
    cal = res["calibration"]
    assert cal["samples"] == 4
    assert cal["proxy_vs_human_agreement"] == pytest.approx(0.75)
    assert cal["proxy_weight"] == pytest.approx(0.75)        # divergent proxy down-weighted
    assert res["mechanism_proven"] is True


def test_cseq_statistic_computes_but_claim_stays_deferred(harness):
    res = harness.scenario_cseq(n=10)
    rep = res["report"]
    assert rep["observations"] == 10
    assert rep["incidents"] == 0
    assert rep["min_D"] is not None and rep["min_D"] > 0     # the differential is a real number
    assert rep["claim"] == "deferred"                        # the HONEST non-assertion (leg-2)
    assert res["mechanism_proven"] is True


def test_ag_per_environment_provenance_recorded(harness):
    res = harness.scenario_ag_environment()
    assert res["earned_in"] == ["dev", "staging", "prod"]
    assert res["explain_earned_in"] == ["dev", "staging", "prod"]
    assert res["mechanism_proven"] is True


def test_harness_main_runs_green(harness):
    assert harness.main() == 0                               # all four mechanisms demonstrated


def test_harness_does_not_overclaim_calibration(harness):
    # honesty guard: the harness must NOT claim the CS-EQ equilibrium holds (calibration deferred).
    assert harness.scenario_cseq()["report"]["claim"] == "deferred"
