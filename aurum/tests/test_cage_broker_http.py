"""Broker HTTP/SSE end-to-end — starts the real aiohttp app and drives it the way
Hermes' gateway does (POST /v1/chat/completions). The agent run is faked so this
needs no docker; it proves the wire contract (request parse -> SSE deltas -> [DONE])
and the auth fail-closed path.
"""
from __future__ import annotations

import asyncio

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from aurum.cage.broker import CageBroker, make_app  # noqa: E402
from aurum.cage.mount_jail import MountJail  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_sse_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-test")
    monkeypatch.setenv("AURUM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("AURUM_GROUPS_ROOT", str(tmp_path / "groups"))

    async def fake_runner(ci, mount_args, plain_env):
        assert ci["prompt"] == "hi there"
        return {"status": "success", "result": "hello from the cage", "newSessionId": "s1"}

    async def go():
        # Build the app inside the loop that serves it (aiohttp binds to the loop).
        app = make_app(CageBroker(jail=MountJail([]), runner=fake_runner, proxy_key=""))
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "hermes-agent",
                      "messages": [{"role": "user", "content": "hi there"}],
                      "stream": True},
                headers={"X-Hermes-Session-Id": "sess-http"},
            )
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            return await resp.text()

    body = _run(go())
    # The gateway extracts choices[0].delta.content; assert the cage text + DONE are present.
    assert "hello from the cage" in body
    assert "data: [DONE]" in body


def test_sse_auth_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-test")
    monkeypatch.setenv("AURUM_GROUPS_ROOT", str(tmp_path / "groups"))

    async def fake_runner(ci, mount_args, plain_env):
        return {"status": "success", "result": "should not happen"}

    async def go():
        # One app, one loop; exercise all three auth cases against the same server.
        app = make_app(CageBroker(jail=MountJail([]), runner=fake_runner, proxy_key="secret"))
        out = {}
        async with TestClient(TestServer(app)) as client:
            for label, token in (("none", None), ("wrong", "wrong"), ("ok", "secret")):
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "x"}]},
                    headers=headers,
                )
                out[label] = resp.status
        return out

    res = _run(go())
    assert res["none"] == 401    # no token -> rejected
    assert res["wrong"] == 401   # wrong token -> rejected
    assert res["ok"] == 200      # correct token -> allowed
