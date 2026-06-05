"""AG — Authority Governor. Hysteresis bands + recovery kinetics + PK-enforced ceiling."""
from __future__ import annotations

import os
import tempfile

from aurum.durability.el import EvidenceLedger
from aurum.novel.ag import AuthorityGovernor


def _ag(**kw):
    return AuthorityGovernor(dwell_seconds=0.0, **kw)


# -- bands -----------------------------------------------------------------
def test_bands_map_from_authority():
    ag = _ag()
    ag.set_authority("c", 0.97); assert ag.band("c") == "full"
    ag.set_authority("c", 0.85); assert ag.band("c") == "code"
    ag.set_authority("c", 0.55); assert ag.band("c") == "readonly"
    ag.set_authority("c", 0.20); assert ag.band("c") == "advisory"


# -- accept (e) / AURUM_ERR_010: no flapping -------------------------------
def test_no_flapping_with_dual_thresholds():
    ag = _ag()
    ag.set_authority("c", 0.81)
    assert ag.band("c") == "code"
    for v in (0.79, 0.81, 0.79, 0.81):
        ag.set_authority("c", v)
        assert ag.band("c") == "code"  # 0.79 >= demote(0.70) -> holds


# -- accept (a): EG spike demotes immediately (fast fall) ------------------
def test_eg_spike_demotes_without_human():
    ag = AuthorityGovernor(dwell_seconds=0.0,
                           kinetics={"rise_rate": 1.0, "fall_rate": 1.0,
                                     "floor": 0.0, "max_gain_per_window": 1.0})
    ag.observe("c", {"tl_tier": 3, "eg_uncertainty": 0.0, "hvp_pass_rate": 1.0,
                     "oi_trend": 1.0})
    assert ag.band("c") == "full"
    ag.observe("c", {"tl_tier": 3, "eg_uncertainty": 0.95, "hvp_pass_rate": 0.4,
                     "oi_trend": 0.3})  # EG spikes, OI dips
    assert ag.band("c") in ("readonly", "advisory")  # demoted, no human


# -- accept (d): high TL alone doesn't grant -------------------------------
def test_high_tl_alone_does_not_grant():
    ag = AuthorityGovernor(dwell_seconds=0.0,
                           kinetics={"rise_rate": 1.0, "fall_rate": 1.0,
                                     "floor": 0.0, "max_gain_per_window": 1.0})
    ag.observe("c", {"tl_tier": 3, "eg_uncertainty": 0.9, "hvp_pass_rate": 0.2,
                     "oi_trend": 0.2})
    assert ag.band("c") != "full"  # poor live signals override a high tier


# -- accept (b): permits enforces the band ---------------------------------
def test_permits_respects_band():
    ag = _ag()
    ag.set_authority("c", 0.65)  # readonly (>= promote 0.60)
    assert ag.permits({"capability_class": "c", "action_class": "code_edit"}) is False
    assert ag.permits({"capability_class": "c", "action_class": "propose"}) is True


# -- accept (f): rate-limited rise + floor ---------------------------------
def test_rise_is_rate_limited():
    ag = AuthorityGovernor(dwell_seconds=0.0,
                           kinetics={"rise_rate": 0.05, "fall_rate": 1.0,
                                     "floor": 0.1, "max_gain_per_window": 0.2})
    perfect = {"tl_tier": 3, "eg_uncertainty": 0.0, "hvp_pass_rate": 1.0, "oi_trend": 1.0}
    a1 = ag.observe("c", perfect)
    assert a1 <= 0.1 + 0.05 + 1e-9  # one good observation can't spike authority


def test_floor_prevents_collapse():
    ag = AuthorityGovernor(dwell_seconds=0.0,
                           kinetics={"rise_rate": 0.05, "fall_rate": 1.0,
                                     "floor": 0.1, "max_gain_per_window": 0.2})
    for _ in range(5):
        ag.observe("c", {"tl_tier": 0, "eg_uncertainty": 1.0, "hvp_pass_rate": 0.0,
                         "oi_trend": 0.0})
    assert ag.authority("c") >= 0.1  # never collapses to zero -> can recover


def test_cb_freeze_demotes():
    ag = AuthorityGovernor(dwell_seconds=0.0,
                           kinetics={"rise_rate": 1.0, "fall_rate": 1.0,
                                     "floor": 0.0, "max_gain_per_window": 1.0})
    ag.observe("c", {"tl_tier": 3, "eg_uncertainty": 0.0, "hvp_pass_rate": 1.0,
                     "oi_trend": 1.0, "cb_frozen": True})
    assert ag.authority("c") <= 0.3


# -- accept (c): explain + EL audit ----------------------------------------
def test_explain_and_el_audit():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    ag = AuthorityGovernor(el=el, dwell_seconds=0.0)
    ag.observe("c", {"tl_tier": 2, "eg_uncertainty": 0.3, "hvp_pass_rate": 0.9,
                     "oi_trend": 0.8})
    ex = ag.explain("c")
    assert set(ex["contributions"]) == {"tl", "eg", "hvp", "oi"}
    rows = el.query({"source_organ": "AG"})
    assert rows and rows[0]["action_type"] == "TRUST_CHANGE"


# -- promotion dwell -------------------------------------------------------
def test_promotion_respects_dwell():
    ag = AuthorityGovernor(dwell_seconds=100.0)
    ag.set_authority("c", 0.65, now=0.0)   # readonly (first promote allowed)
    ag.set_authority("c", 0.97, now=1.0)   # wants full but dwell since last promote (1s<100)
    assert ag.band("c") != "full"          # held by dwell
    ag.set_authority("c", 0.97, now=200.0)  # dwell satisfied
    assert ag.band("c") == "full"
