"""Phase B acceptance — AG authority as a ledger PROJECTION (cross-turn persistence).

Pins the four Phase-B conditions as one coherent gate (sub-parts are also covered by
test_governance_kernel.py / test_ag.py; this asserts them together against the goal):

  1. cold-start rehydrates to a CLEAN NO-OP (empty ledger invents no authority);
  2. AG.explain (and the kernel why-chain) traces EVERY authority level back to its evidence;
  3. a governance gate BLOCKS a forbidden action BY BAND, and PK gates genuinely enforce;
  4. authority is a PROJECTION of the append-only TRUST_CHANGE stream — never a stored mutable
     scalar — so it survives a --rm restart by replay, and a tampered ledger is not trusted.
"""
from __future__ import annotations

from unittest import mock

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


def _k(tmp_path, **kwargs):
    return GovernanceKernel(home=str(tmp_path), **kwargs)


# ── 1. cold-start rehydrates to a clean no-op ─────────────────────────────────────────────

def test_cold_start_rehydrates_to_clean_noop(tmp_path):
    k = _k(tmp_path)
    # A class with NO baseline and NO history: rehydration invents nothing.
    unseen = "kubernetes_internals"
    assert k.ag.authority(unseen) == k.ag.kinetics()["floor"]
    assert k.ag.explain(unseen)["evidence"] == []          # no fabricated trail
    assert k.why_authority(unseen) is None
    assert k.why_authority_chain(unseen) == []
    # The empty/cold ledger still verifies, and baseline classes sit at exactly baseline
    # (not inflated by a phantom projection).
    assert k.el.verify_chain() is True
    from aurum.action_map import DEFAULT_AG_BASELINE
    for cc, val in DEFAULT_AG_BASELINE.items():
        assert k.ag.authority(cc) == pytest.approx(val)


# ── 2. AG.explain traces every authority level back to its evidence ───────────────────────

def test_explain_traces_every_level_to_evidence(tmp_path):
    k = _k(tmp_path)
    cc = "file_write"
    # Move authority twice through audited paths: a proxy-bad demote, then a human promote.
    k.observe_outcome(to_action("write_file", {"path": "/x"}),
                      {"completed": False, "error": "boom"})        # demote (proxy cause)
    k.record_outcome_verdict("task-1", cc, satisfied=True)          # promote (human cause)

    trail = k.ag.explain(cc)["evidence"]
    assert len(trail) >= 3, "expected baseline-seed + demote + promote in the evidence trail"
    # Ordered oldest→newest and contiguous: each level's `from` is the prior level's `to`.
    for prev, cur in zip(trail, trail[1:]):
        assert cur["from"] == prev["to"]
    # The newest evidence entry IS the current authority — explain traces the level to evidence.
    assert trail[-1]["to"] == pytest.approx(k.ag.authority(cc))
    # The two outcome-driven moves carry a cause with a decision_id + classification.
    caused = [e for e in trail if e.get("cause")]
    assert len(caused) >= 2
    assert {e["cause"]["classified"] for e in caused} >= {"bad", "good"}
    assert all(e["cause"].get("decision_id") for e in caused)

    # The kernel why-chain JOINS each caused level to the outcome event that produced it.
    chain = k.why_authority_chain(cc)
    joined = [c for c in chain if c["cause"] and c["outcome_event"] is not None]
    assert len(joined) >= 2
    for c in joined:
        assert c["outcome_event"]["payload"]["decision_id"] == c["cause"]["decision_id"]


# ── 3. a gate blocks a forbidden action by band; PK gates genuinely enforce ───────────────

def test_gate_blocks_forbidden_action_by_band(tmp_path):
    k = _k(tmp_path)
    cc = "file_write"
    # write_file needs the `code` band; drop authority into `readonly` and it must be refused.
    k.ag.set_authority(cc, 0.55)
    assert k.ag.band(cc) == "readonly"
    d = k.govern(to_action("write_file", {"path": "/x", "content": "y"}))
    assert d.allow is False
    assert d.rule_id == "ag:ceiling", f"band gate did not fire: {d}"
    assert "band=readonly" in d.reason


def test_pk_gate_genuinely_enforces(tmp_path):
    k = _k(tmp_path)
    # needs_gate: tool promotion is HUMAN_GATE via the rule table.
    g = k.govern(to_action("skill_manage", {"action": "promote"}))
    assert g.allow is False and g.gate is not None
    # injection boundary: a justification tracing to untrusted content is denied outright.
    inj = k.govern(to_action("write_file", {"path": "/x"},
                             justification_sources=["aa-fetched-spec"]))
    assert inj.allow is False and inj.rule_id == "pk:injection-boundary"


# ── 4. authority is a projection of the ledger, not a stored mutable scalar ───────────────

def test_authority_is_ledger_projection_not_mutable_scalar(tmp_path):
    home = str(tmp_path)
    cc = "file_write"
    k1 = GovernanceKernel(home=home)
    k1.record_outcome_verdict("t", cc, satisfied=True)   # human-grounded promote, logged
    earned = k1.ag.authority(cc)
    assert earned > 0.0

    # A fresh kernel on the SAME durable home rebuilds authority by REPLAYING TRUST_CHANGE —
    # there is no stored mutable authority scalar to load; the value is a projection.
    k2 = GovernanceKernel(home=home)
    assert k2.ag.authority(cc) == pytest.approx(earned)
    # And the projected value equals the latest TRUST_CHANGE in the append-only ledger
    # (i.e. it came from replay, not from a separate writable store).
    tcs = [e for e in k2.el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE"})
           if (e["payload"] or {}).get("capability_class") == cc]
    assert tcs[0]["payload"]["authority"] == pytest.approx(earned)   # query is newest-first
    assert k2.el.verify_chain() is True


def test_tampered_ledger_falls_back_to_baseline_not_projection(tmp_path):
    home = str(tmp_path)
    cc = "file_write"
    k1 = GovernanceKernel(home=home)
    k1.record_outcome_verdict("t", cc, satisfied=True)
    assert k1.ag.authority(cc) > 0.0

    # A durable ledger that fails verify_chain MUST NOT drive authority — rehydration falls
    # back to the low-trust baseline (fail-safe = contraction), never trusts a tampered chain.
    from aurum.action_map import DEFAULT_AG_BASELINE
    with mock.patch.object(EvidenceLedger, "verify_chain", return_value=False):
        k2 = GovernanceKernel(home=home)
    assert k2.ag.authority(cc) == pytest.approx(DEFAULT_AG_BASELINE[cc])
