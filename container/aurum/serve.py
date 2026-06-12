#!/usr/bin/env python3
"""Persistent turn server — keeps aurum-agent running between turns.

POST /turn  (ContainerInput JSON) -> runs hermes -q -> ContainerOutput JSON.
GET  /health                       -> 200 {"status": "ok"}

The broker (aurum.cage.broker) connects here instead of spawning docker run --rm
per message when AURUM_PERSISTENT_CONTAINER=1. This removes the ~10-20s Docker
cold-start cost; hermes subprocess startup (~3-7s) still applies per turn.

Mount layout (set at container start by the broker):
  /workspace/project  (RO) — project files
  /workspace/groups   (RW) — all groups; this server finds the group subdir from
                             ContainerInput.groupFolder. Falls back to
                             /workspace/group (legacy single-group ephemeral mount)
                             so the image works in both modes.
"""
from __future__ import annotations

import asyncio
import os
import sys

# entrypoint.py is copied to /opt/aurum/entrypoint.py in the image.
sys.path.insert(0, "/opt/hermes")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from entrypoint import GROUP_DIR, run_turn  # noqa: E402

_GROUPS_ROOT = "/workspace/groups"
_PORT = 8901


def _group_dir_for(inp: dict) -> str:
    folder = (inp.get("groupFolder") or "default").strip() or "default"
    if os.path.isdir(_GROUPS_ROOT):
        path = os.path.join(_GROUPS_ROOT, folder)
        os.makedirs(path, exist_ok=True)
        return path
    return GROUP_DIR  # legacy single-group mount


async def _handle_turn(request: "web.Request") -> "web.Response":
    from aiohttp import web

    try:
        inp = await request.json()
    except Exception as exc:
        return web.json_response(
            {"status": "error", "result": None, "error": "bad json: %s" % exc},
            status=400,
        )
    # run_turn calls subprocess.run which blocks; run it in the default thread pool
    # so the event loop stays responsive (health checks continue to work mid-turn).
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_turn, inp, _group_dir_for(inp))
    return web.json_response(result)


async def _handle_health(_request: "web.Request") -> "web.Response":
    from aiohttp import web
    return web.json_response({"status": "ok"})


def main() -> None:
    from aiohttp import web

    app = web.Application()
    app.router.add_post("/turn", _handle_turn)
    app.router.add_get("/health", _handle_health)
    web.run_app(app, host="0.0.0.0", port=_PORT)


if __name__ == "__main__":
    main()
