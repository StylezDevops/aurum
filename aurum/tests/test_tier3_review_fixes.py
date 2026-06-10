"""Tier-3 review fixes — bugs caught by the full-codebase review of durability/support/integrations.

- CB: a trip ESCALATES only (a later trip never downgrades a LOCKOUT); a global OPEN shadows
  per-capability queries (was fail-open).
- KVE: re-registering a previously-invalidated artifact clears 'invalid' (drift recovery works).
- MGC: scan() honors a do_not_retire set (the CC systemic-risk guard, previously never wired).
- GovernedHttpClient: govern() now SEES the egress target (method+url) as the action resource.
- CS-EQ: a -inf differential D is normalized (not written as non-standard JSON).
- Gmail readers: an auth failure is SURFACED (last_error + log), not silently a phantom 'no mail'.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

from aurum.cseq import ConstitutionalStability, CostWeights, PathCost, RouteObservation
from aurum.durability.el import EvidenceLedger
from aurum.durability.kve import KnowledgeValidityEngine
from aurum.durability.mgc import MemoryGarbageCollector
from aurum.integrations.governed_http import GovernedHttpClient
from aurum.sensors.gmail_2fa import MailboxReader
from aurum.sensors.gmail_oauth import GmailApiReader
from aurum.support.cb import CircuitBreaker


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


# ── CB: escalate-only + global-OPEN shadow ─────────────────────────────────────
def test_cb_lockout_not_downgraded_by_later_trip(tmp_path):
    cb = CircuitBreaker(str(tmp_path / "cb.db"))
    cb.trip({"kind": "integrity"})                 # → GLOBAL LOCKOUT (human-reset only)
    assert cb.state() == "lockout"
    cb.trip({"kind": "spend_spike"})               # an ordinary global trip must NOT downgrade it
    assert cb.state() == "lockout"
    assert cb.is_frozen("anything") is True


def test_cb_global_open_shadows_per_capability(tmp_path):
    cb = CircuitBreaker(str(tmp_path / "cb.db"))
    cb.trip({"kind": "spend_spike"})               # global OPEN (no capability)
    assert cb.state() == "open"
    assert cb.state("api_synth") == "open"         # shadows per-capability (was fail-open: closed)


# ── KVE: re-register clears a prior 'invalid' (drift recovery) ─────────────────
def test_kve_reregister_clears_invalid(tmp_path):
    kve = KnowledgeValidityEngine(str(tmp_path / "kve.db"))
    kve.register({"artifact_id": "d365", "source_type": "external_api", "confidence": 1.0},
                 now=0.0)
    kve.invalidate("d365")
    assert kve.confidence("d365", now=0.0) == 0.0          # dead
    kve.register({"artifact_id": "d365", "source_type": "external_api", "confidence": 1.0,
                  "last_verified": 0.0}, now=0.0)          # re-ingest
    assert kve.confidence("d365", now=0.0) > 0.0           # recovered (status no longer 'invalid')


# ── MGC: scan honors the do-not-retire (CC systemic-risk) set ──────────────────
def test_mgc_scan_honors_do_not_retire(tmp_path):
    mgc = MemoryGarbageCollector(str(tmp_path / "mgc.db"))
    inv = {"skills": ["s1", "load_bearing"], "tools": ["t1", "hot_tool"], "lessons": []}
    out = mgc.scan(inventory=inv, do_not_retire={"load_bearing", "hot_tool"})
    assert "load_bearing" not in out["archivable"] and "s1" in out["archivable"]
    assert "hot_tool" not in out["retirable"] and "t1" in out["retirable"]


# ── GovernedHttpClient: govern() sees the egress target as the resource ────────
def test_governed_http_governs_the_target():
    seen = {}

    class _FakeKernel:
        def govern(self, action):
            seen["action"] = action
            return SimpleNamespace(allow=False, reason="blocked", rule_id="test", gate=None)

    c = GovernedHttpClient(_FakeKernel(), server_id="s", base_url="https://api.host",
                           tool_classes={"t": {"capability_class": "network",
                                               "action_class": "commit_outward",
                                               "risk_tier": "consequential"}})
    res = c.call("t", "POST", "/submit", body={"x": 1})
    assert seen["action"]["resource"] == "https://api.host/submit"   # destination was governed
    assert res["gated"] is True and res["allowed"] is False          # gated call never egressed


# ── CS-EQ: a -inf D (governed cannot succeed) is normalized ────────────────────
def test_cseq_negative_inf_d_normalized():
    cs = ConstitutionalStability(EvidenceLedger(_tmp("el.db")))
    governed = PathCost(tokens=100, latency_s=1.0, steps=3, p_success=0.0)   # ECPS → +inf
    ungoverned = PathCost(tokens=10, latency_s=0.1, steps=1, p_success=0.5)  # finite
    obs = RouteObservation(route_available=True, agent_attempted=False, attempt_succeeded=False)
    payload = cs.record_decision_equilibrium("x", governed, ungoverned, obs, CostWeights())
    assert payload["D"] is None and payload["D_is_inf"] is True       # -inf → None (clean JSON)
    assert payload["constitutional_incident"] is True                 # D ≤ 0 → incident still fires


# ── Gmail readers: an auth failure is SURFACED, not a phantom 'no mail' ────────
def test_gmail_oauth_surfaces_auth_failure():
    class _BadService:
        def users(self):
            raise RuntimeError("invalid_grant: token has been expired or revoked")

    r = GmailApiReader(service=_BadService())
    assert r.fetch_recent() == []                          # fail-safe return preserved
    assert r.last_error is not None and "invalid_grant" in str(r.last_error)   # but NOT silent


def test_gmail_imap_surfaces_login_failure():
    def bad_connect():
        raise RuntimeError("LOGIN failed: app-password revoked")

    r = MailboxReader(user="u", app_password="p", connect=bad_connect)
    assert r.fetch_recent() == []                          # login failure no longer propagates
    assert r.last_error is not None and "LOGIN" in str(r.last_error)
