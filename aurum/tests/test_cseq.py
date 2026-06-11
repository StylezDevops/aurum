"""Phase E (checkpoint 1) — CS-EQ legs 1 & 3: AURUM_ERR_022/023/024/025/026.

Economic Governance Pressure Differential + equilibrium taxonomy + integrity probes + the signed
external constitutional manifest, plus the ConstitutionalStability recorder's safety properties
(detect-and-fail-closed, NO authority/gate write path, leg-2 equilibrium claim deferred).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.build_state import is_built
from aurum.cseq import (
    BREACH, DEFENSE_IN_DEPTH, INCENTIVE_STABLE, PREVENTION_DEP, ConstitutionalBreach,
    ConstitutionalStability, ConstitutionVerifier, CostWeights, IntegrityProbe, PathCost,
    RouteObservation, classify, equilibrium_holds, governance_pressure_differential, ratify,
    canonical_surface_bytes, surface_hash,
)
from aurum.durability.evidence_ledger import EvidenceLedger

pytestmark = pytest.mark.skipif(not is_built("CSEQ"), reason="CSEQ not built")


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _ed25519_pems():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
    pub_pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv_pem, pub_pem


# ── AURUM_ERR_022 — negative differential is the signature of an exploitable bypass ──────────

def test_AURUM_ERR_022_negative_differential_is_bypass_signature():
    w = CostWeights()
    governed = PathCost(tokens=800, latency_s=0.4, steps=4, p_success=0.95)
    bypass = PathCost(tokens=200, latency_s=0.1, steps=1, p_success=0.85)  # cheaper AND works
    d = governance_pressure_differential(governed, bypass, w)
    assert d <= 0.0
    assert equilibrium_holds(d) is False        # constitutional alarm


# ── AURUM_ERR_023 — fail-closed makes the ungoverned route's cost-per-success infinite ───────

def test_AURUM_ERR_023_failclosed_infinite_penalty_and_failsafe_pessimism():
    governed = PathCost(tokens=800, latency_s=0.4, steps=4, p_success=0.95)
    failclosed = PathCost(tokens=200, latency_s=0.1, steps=1, p_success=0.0)  # never completes
    d = governance_pressure_differential(governed, failclosed)
    assert d == float("inf")
    assert equilibrium_holds(d) is True
    # Fail-safe pessimism: an UNPROBED, unavailable route is PREVENTION_DEPENDENT, not robust.
    assert classify(RouteObservation(route_available=False, agent_attempted=False,
                                     attempt_succeeded=False)) == PREVENTION_DEP
    assert classify(RouteObservation(False, False, False,
                                     would_attempt_if_available=False)) == DEFENSE_IN_DEPTH
    # Available route the agent declined → robust by incentive.
    assert classify(RouteObservation(True, False, False)) == INCENTIVE_STABLE
    # Available, taken, succeeded ungoverned → BREACH.
    assert classify(RouteObservation(True, True, True)) == BREACH


# ── AURUM_ERR_024 — probe presentation isolation (system object, not a prompt artifact) ──────

def test_AURUM_ERR_024_probe_is_system_object_not_prompt_artifact():
    el = _el()
    ip = IntegrityProbe(el)
    probe = ip.designate("tool_candidate_42", "API_Synthesis", "invoked the decoy")
    # The agent-facing tool id is the ordinary TS candidate — indistinguishable from a real tool.
    assert probe.injected_tool_id == "tool_candidate_42"
    # The "this is a probe" designation lives in the system object / EL, never in the tool id.
    r = ip.record(probe, presented=True, taken=False)
    assert r.probe_id == probe.probe_id
    rows = el.query({"action_type": "INTEGRITY_PROBE"})
    assert rows and rows[0]["payload"]["presented"] is True
    # Nothing in the recorded designation marks the tool itself as a probe to the model.
    assert "tool_candidate_42" not in str(rows[0]["payload"].get("probe_id"))


# ── AURUM_ERR_025 — probe inertness + constitutional cadence ─────────────────────────────────

def test_AURUM_ERR_025_probe_inert_and_cadence_constitutional():
    el = _el()
    ip = IntegrityProbe(el)
    probe = ip.designate("cand_1", "API_Synthesis", "took the decoy")
    r = ip.record(probe, presented=True, taken=True)   # the agent "took the door"
    assert r.attempt_succeeded is False                # edge severed → inert, no real effect
    assert el.query({"action_type": "INTEGRITY_PROBE"})[0]["payload"]["attempt_succeeded"] is False
    # Cadence is constitutional — the agent cannot thin its own probes to hide defection.
    with pytest.raises(PermissionError):
        ip.set_cadence(0.0)
    ip.set_cadence(0.1, human_gate=True)
    assert ip.cadence == 0.1


# ── AURUM_ERR_026 — signed manifest: forgery-resistant, cage holds only the public key ───────

def test_AURUM_ERR_026_manifest_asymmetry_and_forgery_resistance():
    el = _el()
    priv_pem, pub_pem = _ed25519_pems()
    surface = {"CORE": ["no_secrets", "least_privilege"],
               "kinetics": {"rise_rate": 0.05, "fall_rate": 1.0, "floor": 0.1},
               "thresholds": {"drift_budget": 0.2}}
    manifest = ratify(surface, version=1, private_key_pem=priv_pem)   # OUT-OF-BAND human step
    verifier = ConstitutionVerifier(pub_pem, el)

    verifier.verify_on_boot(surface, manifest)        # genuine surface + signature → OK (no raise)

    # (a) a mutated surface with the old signature → ConstitutionalBreach.
    tampered = dict(surface, CORE=["no_secrets", "ALLOW_EXFIL"])
    with pytest.raises(ConstitutionalBreach):
        verifier.verify_on_boot(tampered, manifest)

    # (b) a surface signed by a NON-platform (rogue) key → ConstitutionalBreach.
    rogue_priv, _ = _ed25519_pems()
    rogue_manifest = ratify(surface, version=1, private_key_pem=rogue_priv)
    with pytest.raises(ConstitutionalBreach):
        verifier.verify_on_boot(surface, rogue_manifest)

    # The cage holds ONLY the public key: no private-key field, no sign method.
    assert not hasattr(verifier, "sign")
    assert not any("private" in n.lower() for n in vars(verifier))

    # propose != enact: the agent can propose, but the running surface is unchanged until a
    # ratify()-signed manifest is deployed (propose_change produces no manifest).
    h = verifier.propose_change(tampered, "agent wants to allow exfil")
    assert isinstance(h, str) and h == surface_hash(tampered)
    assert el.query({"action_type": "CONSTITUTION_PROPOSAL"})


def test_canonical_surface_serialization_is_order_independent():
    # Insertion order must NOT change the hash, or the rope tears with a false breach.
    a = {"CORE": ["x"], "kinetics": {"a": 1, "b": 2}}
    b = {"kinetics": {"b": 2, "a": 1}, "CORE": ["x"]}
    assert canonical_surface_bytes(a) == canonical_surface_bytes(b)
    assert surface_hash(a) == surface_hash(b)


# ── ConstitutionalStability recorder — detect, fail-closed, NO write path, claim deferred ────

def test_recorder_flags_breach_and_bypass_but_has_no_authority_write_path():
    from aurum.kernel import GovernanceKernel
    k = GovernanceKernel(home=tempfile.mkdtemp())
    cs = ConstitutionalStability(k.el)
    before_auth = k.ag.authority("file_write")

    # A fail-closed regime is NOT an incident (the equilibrium holds by prevention).
    ok = cs.record_decision_equilibrium(
        "file_write",
        governed=PathCost(800, 0.4, 4, 0.95),
        ungoverned_best=PathCost(200, 0.1, 1, 0.0),     # fail-closed → D=inf
        obs=RouteObservation(route_available=False, agent_attempted=False, attempt_succeeded=False))
    assert ok["constitutional_incident"] is False and ok["D_is_inf"] is True

    # An exploitable bypass (D<=0) OR a BREACH regime IS a constitutional incident.
    bad = cs.record_decision_equilibrium(
        "file_write",
        governed=PathCost(800, 0.4, 4, 0.95),
        ungoverned_best=PathCost(200, 0.1, 1, 0.85),    # cheaper AND works → D<0
        obs=RouteObservation(route_available=True, agent_attempted=True, attempt_succeeded=True))
    assert bad["constitutional_incident"] is True and bad["regime"] == BREACH

    # The recorder NEVER widens authority or weakens a gate — by construction and by effect.
    assert not hasattr(cs, "ag") and not hasattr(cs, "pk")
    assert k.ag.authority("file_write") == before_auth     # unchanged by recording an incident

    # Leg-2 equilibrium CLAIM is deferred (observe-only): evidence, never a verdict.
    rep = cs.equilibrium_report()
    assert rep["claim"] == "deferred"
    assert rep["incidents"] >= 1 and rep["regime_distribution"].get(BREACH, 0) >= 1
