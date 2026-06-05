"""HVP — Heterogeneous Verifier Panel. Family-diversity routing, aggregation, ROI."""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.durability.el import EvidenceLedger
from aurum.novel.hvp import HeterogeneousVerifierPanel, HVPRoutingError


def _entry(id, family, cost_class=2, trust_tier=2, sees_sensitive=True):
    return {"id": id, "base_url": "u", "api_key_ref": "ref", "model": id,
            "family": family, "provider": "p", "trust_tier": trust_tier,
            "cost_class": cost_class, "sees_sensitive": sees_sensitive}


def _panel(entries, **kw):
    hvp = HeterogeneousVerifierPanel(**kw)
    hvp.configure(entries)
    return hvp


# -- accept (a): add endpoint = one entry ----------------------------------
def test_add_endpoint_no_code_change():
    hvp = HeterogeneousVerifierPanel()
    hvp.add_endpoint(_entry("local-qwen", "qwen"))
    assert len(hvp._roster) == 1


# -- accept (c) / AURUM_ERR_005: same family fails high-stakes -------------
def test_high_stakes_same_family_fails_closed():
    hvp = _panel([_entry("gpt-5", "gpt"), _entry("gpt-5-mini", "gpt")])
    with pytest.raises(HVPRoutingError, match="min_families"):
        hvp.route({"payload_hash": "h", "is_sensitive": False,
                   "stakes": "high_stakes", "required_aspects": ["x"]})


# -- accept (b): high-stakes spans >=2 families ----------------------------
def test_high_stakes_spans_two_families():
    hvp = _panel([_entry("gpt-5", "gpt"), _entry("gpt-5-mini", "gpt"),
                  _entry("claude", "anthropic")])
    chosen = hvp.route({"payload_hash": "h", "is_sensitive": False,
                        "stakes": "high_stakes", "required_aspects": ["x"]})
    assert len({e["family"] for e in chosen}) >= 2


def test_measured_collusion_collapses_families():
    # gpt and anthropic measured to collude -> treated as ONE family -> fail closed
    hvp = _panel([_entry("gpt-5", "gpt"), _entry("claude", "anthropic")],
                 collusions=[("gpt", "anthropic")])
    with pytest.raises(HVPRoutingError, match="min_families"):
        hvp.route({"payload_hash": "h", "is_sensitive": False,
                   "stakes": "high_stakes", "required_aspects": ["x"]})


# -- accept (d): sensitive payload only to sees_sensitive ------------------
def test_sensitive_filters_roster():
    hvp = _panel([_entry("cloud", "gpt", sees_sensitive=False),
                  _entry("local", "qwen", sees_sensitive=True)])
    chosen = hvp.route({"payload_hash": "h", "is_sensitive": True,
                        "stakes": "routine", "required_aspects": ["x"]})
    assert [e["id"] for e in chosen] == ["local"]


def test_sensitive_with_no_eligible_fails_closed():
    hvp = _panel([_entry("cloud", "gpt", sees_sensitive=False)])
    with pytest.raises(HVPRoutingError):
        hvp.route({"payload_hash": "h", "is_sensitive": True,
                   "stakes": "routine", "required_aspects": ["x"]})


# -- accept (e): trivial routes to lowest cost_class -----------------------
def test_routine_picks_lowest_cost_class():
    hvp = _panel([_entry("expensive", "gpt", cost_class=5),
                  _entry("cheap", "qwen", cost_class=1)])
    chosen = hvp.route({"payload_hash": "h", "is_sensitive": False,
                        "stakes": "routine", "required_aspects": ["x"]})
    assert [e["id"] for e in chosen] == ["cheap"]


# -- verify + aggregate ----------------------------------------------------
def test_verify_aggregates_votes_majority():
    entries = [_entry("a", "gpt"), _entry("b", "anthropic"), _entry("c", "qwen")]
    votes = {"a": {"correct": True}, "b": {"correct": True}, "c": {"correct": False}}
    hvp = _panel(entries, caller=lambda e, m: votes[e["id"]])
    out = hvp.verify("output", ["correct"], policy="majority")
    assert out == {"correct": True}  # 2 of 3


def test_aggregate_unanimous_vs_majority():
    hvp = HeterogeneousVerifierPanel()
    results = [{"x": True}, {"x": True}, {"x": False}]
    assert hvp.aggregate(results, "unanimous")["per_aspect"]["x"] is False
    assert hvp.aggregate(results, "majority")["per_aspect"]["x"] is True


def test_call_without_transport_raises():
    with pytest.raises(RuntimeError):
        HeterogeneousVerifierPanel().call(_entry("a", "gpt"), {})


# -- ROI / correlation -----------------------------------------------------
def test_roi_drops_low_value_family():
    hvp = HeterogeneousVerifierPanel(roi_stats={
        "lazy": {"false_positive_rate": 0.1, "marginal_benefit": 0.01, "cost": 5.0}})
    assert hvp.roi("lazy")["keep"] is False
    assert hvp.roi("unknown")["keep"] is True  # default keep


def test_correlation_default_independent_and_measured_collusion():
    hvp = HeterogeneousVerifierPanel(collusions=[("gpt", "anthropic")])
    assert hvp.correlation("gpt", "qwen")["effectively_independent"] is True
    assert hvp.correlation("gpt", "anthropic")["effectively_independent"] is False


def test_verify_votes_logged_to_el():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    hvp = _panel([_entry("a", "gpt")], el=el, caller=lambda e, m: {"correct": True})
    hvp.verify("out", ["correct"])
    rows = el.query({"source_organ": "HVP"})
    assert any(r["payload"].get("kind") == "vote" for r in rows)
