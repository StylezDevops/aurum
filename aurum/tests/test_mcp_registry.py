"""Phase F1 — mount-resident MCP registry: register-not-install, secret-by-reference,
register≠enable (HUMAN_GATE), conditional injection, atomic durable persistence."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import json
import threading

import pytest

from aurum.mcp import ENABLED, REGISTERED, McpRegistry, McpRegistryError


def _path(tmp_path):
    return str(tmp_path / "state" / "mcp-servers.json")


def _pipeline_spec():
    # The 24KR pipeline as a REMOTE/HTTP MCP — nothing to install, secret by REFERENCE only.
    return {"id": "24kr-pipeline", "transport": "http",
            "url": "http://host.docker.internal:9111",
            "secret_ref": "PIPELINE_API_KEY", "auth_header": "X-API-Key",
            "capability": "submit 24KR release"}


def test_register_is_inert_and_persists_to_mount(tmp_path):
    reg = McpRegistry(_path(tmp_path))
    entry = reg.register(_pipeline_spec())
    assert entry["state"] == REGISTERED          # register ≠ enable — inert on arrival
    assert reg.tools_for_group("telegram_main") == []   # not injected until enabled

    # Persistence-as-projection: a FRESH registry on the same mount path sees it (survives --rm).
    reg2 = McpRegistry(_path(tmp_path))
    assert reg2.get("24kr-pipeline")["url"].endswith(":9111")


def test_no_raw_secrets_allowed(tmp_path):
    reg = McpRegistry(_path(tmp_path))
    # A raw secret value (under any secret-shaped key) is rejected — only secret_ref is allowed.
    for bad in ({"id": "x", "transport": "http", "url": "u", "api_key": "sk-LIVE"},
                {"id": "x", "transport": "http", "url": "u", "token": "abc"},
                {"id": "x", "transport": "http", "url": "u", "password": "p"}):
        with pytest.raises(McpRegistryError):
            reg.register(bad)
    # An unknown field is also rejected (allowlist — catches a smuggled secret under a novel key).
    with pytest.raises(McpRegistryError):
        reg.register({"id": "x", "transport": "http", "url": "u", "sneaky_key": "sk-LIVE"})
    # The mount file never contains a raw secret — only the reference NAME.
    reg.register(_pipeline_spec())
    raw = (tmp_path / "state" / "mcp-servers.json").read_text(encoding="utf-8")
    assert "PIPELINE_API_KEY" in raw and "sk-LIVE" not in raw
    assert "secret_ref" in raw


def test_validation_rejects_bad_transport_and_missing_url(tmp_path):
    reg = McpRegistry(_path(tmp_path))
    with pytest.raises(McpRegistryError):
        reg.register({"id": "x", "transport": "carrier_pigeon", "url": "u"})
    with pytest.raises(McpRegistryError):
        reg.register({"id": "x", "transport": "http"})            # http needs a url
    with pytest.raises(McpRegistryError):
        reg.register({"id": "", "transport": "http", "url": "u"})  # id required


def test_enable_is_human_gated_and_conditional_injection(tmp_path):
    reg = McpRegistry(_path(tmp_path))
    reg.register(_pipeline_spec())

    # ENABLE is HUMAN_GATE — capability-adding (makes the tool live).
    with pytest.raises(PermissionError):
        reg.enable("24kr-pipeline", groups=["telegram_main"])

    reg.enable("24kr-pipeline", groups=["telegram_main"], approved_by="dan")
    assert reg.get("24kr-pipeline")["state"] == ENABLED

    # CONDITIONAL INJECTION: injected only for the enabled group, nowhere else (idle-token guard).
    assert [e["id"] for e in reg.tools_for_group("telegram_main")] == ["24kr-pipeline"]
    assert reg.tools_for_group("some_other_group") == []


def test_lifecycle_quarantine_and_deprecate_stop_injection(tmp_path):
    reg = McpRegistry(_path(tmp_path))
    reg.register(_pipeline_spec())
    reg.enable("24kr-pipeline", groups=["g"], approved_by="dan")
    assert reg.tools_for_group("g")                       # live

    reg.quarantine("24kr-pipeline", reason="reliability drop")
    assert reg.tools_for_group("g") == []                 # suspended → not injected
    reg.deprecate("24kr-pipeline")
    assert reg.get("24kr-pipeline")["state"] == "deprecated"
    with pytest.raises(KeyError):
        reg.enable("ghost", groups=["g"], approved_by="dan")


def test_atomic_concurrent_register_no_corruption(tmp_path):
    reg = McpRegistry(_path(tmp_path))
    n = 25

    def worker(i):
        reg.register({"id": f"srv-{i}", "transport": "http", "url": f"http://h/{i}"})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reg.list()) == n                           # no lost updates under concurrency
    # The file is still valid JSON (atomic temp+replace never left it half-written).
    json.loads((tmp_path / "state" / "mcp-servers.json").read_text(encoding="utf-8"))


def test_corrupt_registry_fails_closed_to_empty(tmp_path):
    p = tmp_path / "state" / "mcp-servers.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not json", encoding="utf-8")
    reg = McpRegistry(str(p))
    assert reg.list() == []                               # fail closed: no servers, no crash
    assert reg.tools_for_group("g") == []
