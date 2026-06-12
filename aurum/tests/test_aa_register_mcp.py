"""Phase F — AA.register_mcp + container load-from-mount.

The governed, EL-logged bridge from AA's synthesis to the durable MOUNT registry
(register-not-install), plus the startup projection a `--rm` container runs to learn which
enabled servers to register this turn. A registered server is INERT until ENABLED (HUMAN_GATE);
raw secrets are rejected; the spec is DATA, never instructions.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.mcp import ENABLED, REGISTERED, McpRegistry, McpRegistryError, load_enabled_servers
from aurum.novel.api_archaeologist import APIArchaeologist


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _reg(tmp_path):
    return McpRegistry(str(tmp_path / "state" / "mcp-servers.json"))


def _spec():
    return {"id": "24kr-pipeline", "transport": "http",
            "url": "http://host.docker.internal:9111",
            "secret_ref": "PIPELINE_API_KEY", "auth_header": "X-API-Key",
            "capability": "submit 24KR release",
            "tools": {"pipeline_submit": {"capability_class": "pipeline",
                                          "action_class": "commit_outward",
                                          "risk_tier": "consequential", "irreversible": True}}}


def _events(el, action_type):
    return el.query({"source_organ": "AA", "action_type": action_type})


# ── AA.register_mcp — governed, EL-logged, INERT on arrival ────────────────────
def test_register_mcp_records_inert_and_logs_el(tmp_path):
    el = _el()
    aa = APIArchaeologist(el=el, registry=_reg(tmp_path))
    entry = aa.register_mcp(_spec())
    assert entry["state"] == REGISTERED            # register ≠ enable — inert
    assert _reg(tmp_path).get("24kr-pipeline")["url"].endswith(":9111")   # persisted to mount
    logged = _events(el, "MCP_REGISTER")
    assert len(logged) == 1
    assert logged[0]["payload"]["secret_ref"] == "PIPELINE_API_KEY"       # ref name, not a value
    assert logged[0]["payload"]["state"] == REGISTERED


def test_register_mcp_rejects_raw_secret_and_logs_rejection(tmp_path):
    el = _el()
    aa = APIArchaeologist(el=el, registry=_reg(tmp_path))
    with pytest.raises(McpRegistryError):
        aa.register_mcp({"id": "leaky", "transport": "http", "url": "u", "api_key": "sk-LIVE"})
    # the rejection is itself audited, and nothing was persisted
    assert len(_events(el, "MCP_REGISTER_REJECTED")) == 1
    assert _reg(tmp_path).get("leaky") is None
    # and the raw secret never reached the EL payload
    raw = repr(_events(el, "MCP_REGISTER_REJECTED"))
    assert "sk-LIVE" not in raw


def test_register_mcp_requires_a_registry():
    with pytest.raises(RuntimeError):
        APIArchaeologist(el=_el()).register_mcp(_spec())


# ── AA.enable_mcp — HUMAN_GATE, EL-logged, conditional injection ───────────────
def test_enable_mcp_is_human_gated_and_logs(tmp_path):
    el = _el()
    aa = APIArchaeologist(el=el, registry=_reg(tmp_path))
    aa.register_mcp(_spec())
    # missing approver → HUMAN_GATE (the registry enforces it)
    with pytest.raises(PermissionError):
        aa.enable_mcp("24kr-pipeline", groups=["telegram_main"], approved_by=None)  # type: ignore[arg-type]
    entry = aa.enable_mcp("24kr-pipeline", groups=["telegram_main"], approved_by="dan")
    assert entry["state"] == ENABLED
    logged = _events(el, "MCP_ENABLE")
    assert len(logged) == 1 and logged[0]["payload"]["approved_by"] == "dan"


# ── container load-from-mount — the startup projection ─────────────────────────
def test_load_enabled_servers_only_returns_enabled_for_group(tmp_path):
    state_root = str(tmp_path / "state")
    aa = APIArchaeologist(el=_el(), registry=McpRegistry(os.path.join(state_root, "mcp-servers.json")))
    aa.register_mcp(_spec())
    # registered-but-not-enabled → injected nowhere (idle-token-tax guard)
    assert load_enabled_servers(state_root, "telegram_main") == []
    aa.enable_mcp("24kr-pipeline", groups=["telegram_main"], approved_by="dan")
    plan = load_enabled_servers(state_root, "telegram_main")
    assert [e["id"] for e in plan] == ["24kr-pipeline"]
    # the per-tool classification rides along so the live seam governs the dynamic tool from the mount
    assert plan[0]["tools"]["pipeline_submit"]["irreversible"] is True
    assert load_enabled_servers(state_root, "some_other_group") == []   # conditional injection


def test_load_enabled_servers_missing_registry_fails_closed(tmp_path):
    # no mount registry at all → inject nothing, never crash a turn
    assert load_enabled_servers(str(tmp_path / "nope"), "g") == []
