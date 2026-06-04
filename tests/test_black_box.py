"""Black Box tests (self-ability-map.md gap #1: failure → postmortem → skill)."""

from pathlib import Path

from agent.black_box import (
    build_failure_review_addendum,
    is_learnable_api_error,
    recent_postmortems,
    record_postmortem,
    redact,
)


# ── redaction ───────────────────────────────────────────────────────────────


class TestRedact:
    def test_redacts_known_key_shapes(self):
        s = redact("token sk-abcdefgh12345678 and ghp_" + "A" * 22)
        assert "sk-abcdefgh12345678" not in s
        assert "ghp_" not in s or "[REDACTED]" in s

    def test_redacts_bearer_and_kv_secrets(self):
        assert "[REDACTED]" in redact("Authorization: Bearer abcdef0123456789")
        assert "supersecretvalue" not in redact("api_key=supersecretvalue")
        assert "hunter2xx" not in redact("password: hunter2xx")

    def test_redacts_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456"
        assert jwt not in redact(f"cookie {jwt} end")

    def test_truncates_long_text(self):
        out = redact("x" * 9000)
        assert len(out) < 9000
        assert out.endswith("…[truncated]")

    def test_none_is_empty(self):
        assert redact(None) == ""


# ── store: record + retrieve ────────────────────────────────────────────────


class TestStore:
    def test_record_and_retrieve(self, tmp_path):
        p = record_postmortem(
            "tool_error", "thing broke", "it went wrong",
            lesson_hint="do it differently", hermes_home=tmp_path,
        )
        assert p is not None and p.exists()
        assert (p / "postmortem.json").exists()
        assert (p / "POSTMORTEM.md").exists()

        recs = recent_postmortems(hermes_home=tmp_path)
        assert len(recs) == 1
        assert recs[0]["trigger"] == "tool_error"
        assert recs[0]["title"] == "thing broke"

    def test_secret_is_redacted_on_disk(self, tmp_path):
        record_postmortem(
            "api_error", "auth failed",
            "request used api_key=THE_REAL_SECRET_VALUE and failed",
            hermes_home=tmp_path,
        )
        on_disk = (tmp_path / "blackbox")
        blob = "\n".join(
            f.read_text(encoding="utf-8")
            for f in on_disk.rglob("*") if f.is_file()
        )
        assert "THE_REAL_SECRET_VALUE" not in blob
        assert "[REDACTED]" in blob

    def test_recent_is_newest_first_and_bounded(self, tmp_path):
        for i in range(4):
            record_postmortem("t", f"title {i}", "s", hermes_home=tmp_path)
        recs = recent_postmortems(limit=2, hermes_home=tmp_path)
        assert len(recs) == 2  # bounded

    def test_no_store_returns_empty(self, tmp_path):
        assert recent_postmortems(hermes_home=tmp_path / "nope") == []


# ── learnable-vs-transient classifier ───────────────────────────────────────


class _Reason:
    def __init__(self, name):
        self.name = name


class _Err:
    def __init__(self, retryable, reason):
        self.retryable = retryable
        self.reason = _Reason(reason)


class TestIsLearnable:
    def test_retryable_is_not_learnable(self):
        assert is_learnable_api_error(_Err(True, "auth")) is False

    def test_transient_nonretryable_is_not_learnable(self):
        assert is_learnable_api_error(_Err(False, "timeout")) is False
        assert is_learnable_api_error(_Err(False, "server_error")) is False

    def test_nonretryable_auth_is_learnable(self):
        assert is_learnable_api_error(_Err(False, "auth")) is True

    def test_none_is_not_learnable(self):
        assert is_learnable_api_error(None) is False


# ── review addendum ─────────────────────────────────────────────────────────


class TestReviewAddendum:
    def test_empty_when_no_records(self, tmp_path):
        assert build_failure_review_addendum(hermes_home=tmp_path) == ""

    def test_includes_recorded_failures(self, tmp_path):
        record_postmortem("skill_ci", "blocked foo", "tests failed",
                          lesson_hint="fix the assertion", hermes_home=tmp_path)
        addendum = build_failure_review_addendum(hermes_home=tmp_path)
        assert "Black Box" in addendum
        assert "blocked foo" in addendum
        assert "skill_ci" in addendum


# ── integration: a Skill-CI block records a postmortem ──────────────────────


class TestSkillCiRecordsPostmortem:
    def test_blocked_skill_writes_postmortem(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AURUM_SKILL_CI", "1")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from tools.skill_ci import skill_ci_gate

        skill = tmp_path / "bad"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: bad\ndescription: x\n---\nbody\n", encoding="utf-8"
        )
        (skill / "tests").mkdir()
        (skill / "tests" / "test_s.py").write_text(
            "def test_fail():\n    assert False\n", encoding="utf-8"
        )

        err = skill_ci_gate(skill)
        assert err is not None  # blocked

        recs = recent_postmortems(hermes_home=tmp_path)
        assert any(r["trigger"] == "skill_ci" for r in recs), recs
