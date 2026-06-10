"""GovernedHttpClient (public framework primitive): safe reads pass; an irreversible/outward call
is gated to the full band (shadow-contained) until authority is earned, then actually sent.
Domain-free — uses a generic tool set, no instance/label specifics."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.durability.clock import DomainClock
from aurum.integrations import GovernedHttpClient, resolve_secret
from aurum.kernel import GovernanceKernel
from aurum.mcp import McpRegistry

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

# A generic external API: one safe read, one irreversible outward commit.
TOOLS = {
    "demo_read":   {"capability_class": "read", "action_class": "advise", "risk_tier": "safe_read"},
    "demo_commit": {"capability_class": "network", "action_class": "commit_outward",
                    "risk_tier": "consequential", "irreversible": True},
}


class _FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body, headers):
        self.calls.append({"method": method, "url": url, "body": body,
                           "auth": headers.get("X-API-Key")})
        return {"ok": True}


def _client(tmp_path, **kw):
    # Injectable DomainClock so a test can SPACE grounded outcomes past the band promotion dwell.
    k = GovernanceKernel(home=str(tmp_path), domain_clock=DomainClock(1_000_000.0))
    ft = _FakeTransport()
    reg = McpRegistry(str(tmp_path / "mcp-servers.json"))
    c = GovernedHttpClient(k, server_id="demo-api", base_url="http://127.0.0.1:8080",
                           tool_classes=TOOLS, api_key="k", transport=ft, registry=reg, **kw)
    return k, ft, reg, c


def test_registers_as_remote_mcp_with_tool_classes(tmp_path):
    _k, _ft, reg, _c = _client(tmp_path)
    entry = reg.get("demo-api")
    assert entry["transport"] == "http" and "demo_commit" in entry["tools"]
    assert entry["tools"]["demo_commit"]["irreversible"] is True


def test_safe_read_passes_and_sends(tmp_path):
    _k, ft, _reg, c = _client(tmp_path)
    r = c.call("demo_read", "GET", "/thing", auth=False)
    assert r["ok"] and ft.calls[-1]["url"].endswith("/thing") and ft.calls[-1]["auth"] is None


def test_irreversible_is_gated_at_baseline_and_not_sent(tmp_path):
    _k, ft, _reg, c = _client(tmp_path)
    n = len(ft.calls)
    r = c.call("demo_commit", "POST", "/do", body={"x": 1})
    assert r["allowed"] is False and r["gated"] is True and r["rule_id"] == "ag:ceiling"
    assert len(ft.calls) == n, "a GATED irreversible call must NOT be sent (shadow contained)"


def test_irreversible_sends_only_after_full_authority(tmp_path):
    k, ft, _reg, c = _client(tmp_path)
    for i in range(40):                       # grounded outcomes promote toward the full band
        if k.ag.band("network") == "full":
            break
        k._domain_clock.advance(61.0)         # > dwell_seconds, so each band crossing clears
        k.record_outcome_verdict(f"t{i}", "network", satisfied=True)
    assert k.ag.band("network") == "full"
    r = c.call("demo_commit", "POST", "/do", body={"x": 1})
    assert r["ok"] and r["allowed"] is True
    assert ft.calls[-1]["url"].endswith("/do") and ft.calls[-1]["auth"] == "k"


def test_missing_key_fails_closed(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    c = GovernedHttpClient(k, server_id="demo", base_url="http://x", tool_classes=TOOLS,
                           api_key=None, transport=_FakeTransport())
    r = c.call("demo_read", "GET", "/x")          # safe read, but auth required + no key → fail closed
    assert r["ok"] is False and "API key" in r["error"]


def test_resolve_secret_env_then_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AURUM_X_REF", "from-env")
    assert resolve_secret("AURUM_X_REF") == "from-env"
    monkeypatch.delenv("AURUM_X_REF", raising=False)
    f = tmp_path / "s.env"
    f.write_text("# c\nAURUM_X_REF=from-file\n", encoding="utf-8")
    assert resolve_secret("AURUM_X_REF", secrets_file=str(f)) == "from-file"
    assert resolve_secret("AURUM_MISSING", secrets_file=str(f)) is None
