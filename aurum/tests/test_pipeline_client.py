"""Phase F — governed PipelineClient: safe reads pass, the irreversible /submit is gated to the
full band (shadow-contained) until authority is earned, and only then is the API actually called."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os

import pytest

from aurum.build_state import is_built
from aurum.integrations import PipelineClient, resolve_secret
from aurum.kernel import GovernanceKernel
from aurum.mcp import McpRegistry

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)


class _FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body, headers):
        self.calls.append({"method": method, "url": url, "body": body,
                           "has_key": "X-API-Key" in headers})
        return {"status": "ok", "echo": body}


def _client(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    ft = _FakeTransport()
    reg = McpRegistry(str(tmp_path / "mcp-servers.json"))
    pc = PipelineClient(k, base_url="http://127.0.0.1:9111", api_key="test-key",
                        transport=ft, registry=reg)
    return k, ft, reg, pc


def test_registers_pipeline_as_remote_mcp(tmp_path):
    _k, _ft, reg, _pc = _client(tmp_path)
    entry = reg.get("24kr-pipeline")
    assert entry["transport"] == "http" and entry["secret_ref"] == "PIPELINE_API_KEY"
    assert "9111" in entry["url"]


def test_safe_reads_pass_and_carry_key_appropriately(tmp_path):
    _k, ft, _reg, pc = _client(tmp_path)
    r = pc.health()
    assert r["ok"] and ft.calls[-1]["url"].endswith("/health") and ft.calls[-1]["has_key"] is False
    r = pc.validate("24KJ900")
    assert r["ok"] and ft.calls[-1]["url"].endswith("/validate") and ft.calls[-1]["has_key"] is True
    assert ft.calls[-1]["body"] == {"folder_name": "24KJ900"}


def test_submit_is_gated_at_baseline_and_not_called(tmp_path):
    _k, ft, _reg, pc = _client(tmp_path)
    n = len(ft.calls)
    r = pc.submit("24KJ900")
    assert r["allowed"] is False and r["gated"] is True and r["rule_id"] == "ag:ceiling"
    assert len(ft.calls) == n, "a GATED irreversible submit must NOT hit the API (shadow contained)"


def test_submit_fires_only_after_full_authority_is_earned(tmp_path):
    k, ft, _reg, pc = _client(tmp_path)
    # Earn the full band the legitimate way (human-grounded outcomes promote 'pipeline').
    for i in range(3):
        k.record_outcome_verdict(f"t{i}", "pipeline", satisfied=True)
    assert k.ag.band("pipeline") == "full"
    r = pc.submit("24KJ900", headless=True)
    assert r["ok"] and r["allowed"] is True
    assert ft.calls[-1]["url"].endswith("/submit") and ft.calls[-1]["has_key"] is True
    assert ft.calls[-1]["body"]["folder_name"] == "24KJ900" and ft.calls[-1]["body"]["headless"] is True


def test_missing_key_fails_closed_not_silently(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    pc = PipelineClient(k, base_url="http://x:9111", api_key=None, transport=_FakeTransport())
    r = pc.validate("24KJ900")        # safe-read allowed, but no key → fail closed (not a silent call)
    assert r["ok"] is False and "API key" in r["error"]


def test_resolve_secret_env_then_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AURUM_X_REF", "from-env")
    assert resolve_secret("AURUM_X_REF") == "from-env"
    monkeypatch.delenv("AURUM_X_REF", raising=False)
    f = tmp_path / "pipeline.env"
    f.write_text("# c\nAURUM_X_REF=from-file\n", encoding="utf-8")
    assert resolve_secret("AURUM_X_REF", secrets_file=str(f)) == "from-file"
    assert resolve_secret("AURUM_MISSING", secrets_file=str(f)) is None
