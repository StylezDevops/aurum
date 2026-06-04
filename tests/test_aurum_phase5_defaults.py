"""Aurum Phase 5 — memory + native compression on by default, per-group, no flaky dep.

Reuse-first decision: Aurum uses Hermes'
NATIVE memory and context compressor. Both are on by default and rooted at
``HERMES_HOME``, which the cage mounts per-group, so persistence across the
ephemeral ``--rm`` container is already handled by the existing mount. We
deliberately do NOT:
  * wire a redundant knowledge-graph "memory" MCP — native memory already covers it; or
  * make the optional host-side headroom proxy a *hard* dependency — the agent uses
    the native compressor, so a down proxy can never hang it (the original
    "headroom hung for 19 minutes" failure mode is absent by construction).

These tests lock those product invariants: if an upstream default drift ever
disabled memory or swapped the default context engine for an external one, the
Aurum guarantee would break loudly here rather than silently in the cage.

What remains for Phase 5 is genuinely a *live* test (multi-turn memory save/recall
in the running cage over real LLM turns), which is out of scope for unit CI.
"""

from hermes_cli.config import DEFAULT_CONFIG, cfg_get


class TestMemoryOnByDefault:
    def test_memory_enabled(self):
        assert cfg_get(DEFAULT_CONFIG, "memory", "memory_enabled", default=False) is True

    def test_user_profile_enabled(self):
        assert cfg_get(DEFAULT_CONFIG, "memory", "user_profile_enabled", default=False) is True

    def test_no_external_memory_provider_required(self):
        # Built-in memory only by default — no external provider as a dependency.
        assert cfg_get(DEFAULT_CONFIG, "memory", "provider", default="") == ""


class TestNativeCompressionOnByDefault:
    def test_default_context_engine_is_native_compressor(self):
        # "compressor" = built-in lossy summarization. Not an external/headroom
        # engine, so context management has no off-box hard dependency.
        assert cfg_get(DEFAULT_CONFIG, "context", "engine", default=None) == "compressor"


class TestPerGroupPersistence:
    def test_hermes_home_env_drives_storage_root(self, tmp_path, monkeypatch):
        # The cage sets HERMES_HOME to the per-group dir; memory_manager and the
        # rest root their state at get_hermes_home(), so per-group persistence
        # follows from honoring this env var.
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from hermes_constants import get_hermes_home

        assert str(get_hermes_home()) == str(tmp_path)
