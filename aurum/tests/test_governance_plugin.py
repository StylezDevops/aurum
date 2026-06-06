"""Tests for the aurum-governance plugin hook (the live seam entry).

Loads the bundled plugin module directly and drives `_on_pre_tool_call` the way the
Hermes `pre_tool_call` hook would, asserting allow (None) / block (dict) and that the
decisions land in the per-group EL.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aurum.build_state import is_built

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

# Load plugins/aurum-governance/__init__.py by path (it's not an installed package).
_PLUGIN_PATH = (
    Path(__file__).resolve().parents[2] / "plugins" / "aurum-governance" / "__init__.py"
)


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("AURUM_GOVERNANCE", "1")
    monkeypatch.delenv("AURUM_GOVERNANCE_DISABLE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("aurum_governance_plugin", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    mod._kernel = None  # fresh kernel per test (process singleton otherwise)
    yield mod
    mod._kernel = None


def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AURUM_GOVERNANCE", raising=False)
    spec = importlib.util.spec_from_file_location("aurum_gov_off", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Off ⇒ never blocks, never even builds a kernel
    assert mod._on_pre_tool_call("write_file", {"path": "/x"}) is None


def test_benign_read_allowed(plugin):
    assert plugin._on_pre_tool_call("read_file", {"path": "/x"}) is None


def test_consequential_write_allowed_at_baseline(plugin):
    assert plugin._on_pre_tool_call("write_file", {"path": "/x", "content": "y"}) is None


def test_outward_action_blocked_by_ceiling(plugin):
    block = plugin._on_pre_tool_call("send_email", {})
    assert isinstance(block, dict)
    assert block["action"] == "block"
    assert "ag-ceiling" in block["message"]


def test_kill_switch_bypasses(plugin, monkeypatch):
    monkeypatch.setenv("AURUM_GOVERNANCE_DISABLE", "1")
    # even an outward action passes when the kill-switch is set
    assert plugin._on_pre_tool_call("send_email", {}) is None


def test_decisions_logged_to_el(plugin):
    plugin._on_pre_tool_call("write_file", {"path": "/x", "content": "y"})
    events = plugin._get_kernel().el.query({"action_type": "GOVERNANCE_DECISION"})
    assert any(e["payload"].get("outcome") == "proceed" for e in events)
