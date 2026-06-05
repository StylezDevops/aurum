"""Cage broker — translation + fail-closed logic. No docker/HTTP: the runner is
injected so the request path is exercised directly. The live container round-trip is
the separate, human-gated verification.
"""
from __future__ import annotations

import asyncio
import json

from aurum.cage.broker import (
    CageBroker,
    SECRET_ENV_NAMES,
    extract_prompt,
    container_input,
    parse_container_output,
    sse_content,
    sse_done,
    OUT_START,
    OUT_END,
)
from aurum.cage.mount_jail import MountJail


class _FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __call__(self, ci, mount_args, plain_env):
        self.calls.append((ci, mount_args, plain_env))
        return self.result


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_extract_prompt_takes_last_user_turn():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "current"},
    ]
    assert extract_prompt(msgs) == "current"
    assert extract_prompt([]) == ""


def test_container_input_carries_secrets_in_payload_not_env():
    ci = container_input("hi", "s1", "grp", False, {"ANTHROPIC_AUTH_TOKEN": "sk"})
    assert ci["prompt"] == "hi" and ci["sessionId"] == "s1"
    assert ci["secrets"]["ANTHROPIC_AUTH_TOKEN"] == "sk"
    # The secret is carried in the (stdin) payload — that's the whole point.
    assert "ANTHROPIC_AUTH_TOKEN" in json.dumps(ci)


def test_parse_container_output_ok_and_failclosed():
    good = f"noise\n{OUT_START}\n{json.dumps({'status':'success','result':'hi'})}\n{OUT_END}\ntrailing"
    assert parse_container_output(good) == {"status": "success", "result": "hi"}
    # Missing sentinel -> error, never a silent success.
    assert parse_container_output("just text")["status"] == "error"
    # Malformed json between sentinels -> error.
    bad = f"{OUT_START}\n{{not json\n{OUT_END}"
    assert parse_container_output(bad)["status"] == "error"


def test_sse_format():
    ev = sse_content("hello").decode()
    assert ev.startswith("data: ") and ev.endswith("\n\n")
    obj = json.loads(ev[len("data: "):].strip())
    assert obj["choices"][0]["delta"]["content"] == "hello"
    assert sse_done() == b"data: [DONE]\n\n"


# ── auth ─────────────────────────────────────────────────────────────────────

def test_authorize_open_without_key_failclosed_with_key():
    assert CageBroker(jail=MountJail([]), proxy_key="").authorize(None) is True
    b = CageBroker(jail=MountJail([]), proxy_key="topsecret")
    assert b.authorize("Bearer topsecret") is True
    assert b.authorize("Bearer wrong") is False
    assert b.authorize(None) is False
    assert b.authorize("topsecret") is False  # missing 'Bearer '


# ── handle_turn: happy + fail-closed ─────────────────────────────────────────

def test_handle_turn_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-test")
    fake = _FakeRunner({"status": "success", "result": "hi", "newSessionId": "s1"})
    broker = CageBroker(jail=MountJail([]), runner=fake)
    res = asyncio.run(broker.handle_turn(
        [{"role": "user", "content": "hello"}], "sess1",
        project_dir=str(tmp_path / "proj"), groups_root=str(tmp_path / "groups"),
    ))
    assert res["status"] == "success" and res["result"] == "hi"
    assert len(fake.calls) == 1
    ci, mount_args, plain_env = fake.calls[0]
    assert ci["prompt"] == "hello"
    assert ci["secrets"]["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    # secret must NOT leak into the plain (-e) env that shows in docker inspect.
    assert "ANTHROPIC_AUTH_TOKEN" not in plain_env
    # structural mounts present.
    assert any(a.endswith(":/workspace/group:rw") for a in mount_args)


def test_handle_turn_fails_closed_on_denied_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk")
    fake = _FakeRunner({"status": "success"})
    broker = CageBroker(jail=MountJail([str(tmp_path / "allow")]), runner=fake)
    res = asyncio.run(broker.handle_turn(
        [{"role": "user", "content": "x"}], "s",
        project_dir=str(tmp_path), groups_root=str(tmp_path / "g"),
        extras={"bad": str(tmp_path / "outside")},
    ))
    assert res["status"] == "error" and "mount denied" in res["error"]
    assert fake.calls == []  # runner NEVER invoked on a denied mount


def test_handle_turn_fails_closed_without_secret(monkeypatch, tmp_path):
    for n in SECRET_ENV_NAMES:
        monkeypatch.delenv(n, raising=False)
    fake = _FakeRunner({"status": "success"})
    broker = CageBroker(jail=MountJail([]), runner=fake, require_secret=True)
    res = asyncio.run(broker.handle_turn(
        [{"role": "user", "content": "x"}], "s",
        project_dir=str(tmp_path), groups_root=str(tmp_path / "g"),
    ))
    assert res["status"] == "error" and "secret" in res["error"].lower()
    assert fake.calls == []  # never run unauthenticated
