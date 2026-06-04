"""Toolsmith tests (self-ability-map.md §3: governed tool authoring lifecycle).

Safety invariant: a proposed tool is scanned and tested but NEVER auto-activated.
The dangerous-tool case is blocked at the *scan* phase (its code is never run);
the execution-path cases use benign tools only.
"""

from pathlib import Path

import pytest

from tools.toolsmith import (
    STATUS_BLOCKED,
    STATUS_NO_TESTS,
    STATUS_TESTED_FAIL,
    STATUS_TESTED_PASS,
    get_proposal,
    list_proposals,
    propose_tool,
)

_GOOD_TOOL = "def run(x):\n    \"\"\"Double a number.\"\"\"\n    return x * 2\n"


class TestNameValidation:
    def test_invalid_name_blocked(self, tmp_path):
        r = propose_tool("Bad Name!", _GOOD_TOOL, hermes_home=tmp_path)
        assert r.status == STATUS_BLOCKED
        assert "invalid tool name" in r.detail


class TestFailClosedOnDangerousCode:
    def test_module_level_danger_blocked_without_execution(self, tmp_path):
        # eval at import scope → Policy Kernel flags critical → dangerous → blocked.
        # A test is supplied but must NOT run (we never execute flagged code).
        dangerous = "eval(\"__import__('os').system('id')\")\n\ndef run():\n    return 1\n"
        sentinel = tmp_path / "DETONATED"
        test = (
            "import pathlib\n"
            f"pathlib.Path(r'{sentinel}').write_text('x')\n"
            "def test_noop():\n    assert True\n"
        )
        r = propose_tool("danger", dangerous, test_code=test, hermes_home=tmp_path)
        assert r.status == STATUS_BLOCKED
        assert r.verdict == "dangerous"
        assert not sentinel.exists(), "flagged tool's test was executed — must not happen"


class TestExecutionPaths:
    def test_clean_tool_no_tests(self, tmp_path):
        r = propose_tool("calc", _GOOD_TOOL, hermes_home=tmp_path)
        assert r.status == STATUS_NO_TESTS

    def test_clean_tool_passing_tests(self, tmp_path):
        test = (
            "from calc import run\n"
            "def test_doubles():\n    assert run(3) == 6\n"
        )
        r = propose_tool("calc", _GOOD_TOOL, test_code=test, hermes_home=tmp_path)
        assert r.status == STATUS_TESTED_PASS, r.detail
        # Invariant: staged only, never activated.
        assert (tmp_path / "tools_staging" / "calc" / "meta.json").exists()
        assert "NOT active" in r.detail

    def test_clean_tool_failing_tests(self, tmp_path):
        test = (
            "from calc import run\n"
            "def test_wrong():\n    assert run(3) == 7\n"
        )
        r = propose_tool("calc", _GOOD_TOOL, test_code=test, hermes_home=tmp_path)
        assert r.status == STATUS_TESTED_FAIL

    def test_subprocess_in_function_is_not_overblocked(self, tmp_path):
        # A tool legitimately using subprocess INSIDE a function must not be
        # flagged — only import-time side effects are dangerous.
        code = (
            "import subprocess\n"
            "def run(cmd):\n"
            "    return subprocess.run(cmd, capture_output=True)\n"
        )
        test = "from netcheck import run\ndef test_importable():\n    assert callable(run)\n"
        r = propose_tool("netcheck", code, test_code=test, hermes_home=tmp_path)
        assert r.status == STATUS_TESTED_PASS, (r.status, r.detail)


class TestStore:
    def test_list_and_get(self, tmp_path):
        propose_tool("calc", _GOOD_TOOL, hermes_home=tmp_path)
        proposals = list_proposals(hermes_home=tmp_path)
        assert len(proposals) == 1
        assert proposals[0]["name"] == "calc"
        got = get_proposal("calc", hermes_home=tmp_path)
        assert got is not None and got["status"] == STATUS_NO_TESTS

    def test_get_missing_returns_none(self, tmp_path):
        assert get_proposal("nope", hermes_home=tmp_path) is None


class TestRegistration:
    def test_tool_is_registered(self):
        import tools.toolsmith  # noqa: F401 — ensure registration ran
        from tools.registry import registry

        assert "propose_tool" in registry.get_all_tool_names()

    def test_check_fn_gates_on_cage_env(self, monkeypatch):
        from tools.toolsmith import _toolsmith_check

        monkeypatch.delenv("AURUM_TOOLSMITH", raising=False)
        assert _toolsmith_check() is False
        monkeypatch.setenv("AURUM_TOOLSMITH", "1")
        assert _toolsmith_check() is True
