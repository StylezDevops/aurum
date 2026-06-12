"""Aurum cage broker — the OpenAI-compatible `/v1/chat/completions` SSE endpoint
Hermes' gateway proxies to (see PROXY_CONTRACT.md). Per request it runs ONE agent
turn in an ephemeral `docker run --rm` cage with allowlisted mounts and per-request
secret injection, then streams the result back as OpenAI content-delta chunks.

Why this shape: Hermes' gateway already provides every channel + headless cli; its
`_run_agent` delegates to a proxy URL when one is configured. Pointing that URL at
this broker gives per-request container isolation for ALL front-ends at once, with
zero gateway changes.

Security posture (fail closed everywhere):
- Mounts pass through the MountJail (deny-by-default; AURUM_ERR_021). A denied mount
  aborts the turn — never run with an over-broad mount.
- SECRETS travel via STDIN (inside ContainerInput), never via `-e`/argv, so they do
  not appear in `docker inspect` or `ps`. The container's entrypoint injects them
  into the in-container process env after start. If a required secret can't be
  brokered, the turn is refused — never run unauthenticated/uncaged.
- If docker is missing, the image won't run, or the cage exits without valid output,
  the broker returns an error to the gateway rather than falling back to host execution.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from .mount_jail import MountJail, MountDenied, jail_from_file

# Sentinels emitted by container/aurum/entrypoint.py (kept in sync with it).
OUT_START = "---AURUM_OUTPUT_START---"
OUT_END = "---AURUM_OUTPUT_END---"

CAGE_IMAGE = os.getenv("AURUM_CAGE_IMAGE", "aurum-agent:latest")
PROXY_KEY_ENV = "GATEWAY_PROXY_KEY"
# Egress posture is a DEPLOYMENT decision, not hardcoded here. On cloud runtimes
# (ACI / Fargate / ECS) egress is governed by the platform (Azure Firewall/NSG, AWS
# security groups, private endpoints). For docker-host deployments, set
# AURUM_CAGE_NETWORK to pass `docker --network <value>` (e.g. a custom egress-firewalled
# network, or "none"). Unset = docker default. The containment that actually matters —
# no durable secret resident in the cage — holds regardless of the network (secrets ride
# stdin; see container_input / build_docker_argv), so a compromised container has nothing
# to exfiltrate even with open egress.
CAGE_NETWORK_ENV = "AURUM_CAGE_NETWORK"

# Secret env names the agent needs brokered per-request (provider auth). Non-secret
# config (base_url, model) is passed plainly; only these are routed via stdin.
SECRET_ENV_NAMES = ("ANTHROPIC_AUTH_TOKEN", "OPENROUTER_API_KEY", "PIPELINE_API_KEY")

# A runner takes a ContainerInput dict + docker args and returns a ContainerOutput
# dict. Injectable so the HTTP/translation logic is testable without docker.
Runner = Callable[[Dict[str, Any], List[str], Dict[str, str]], Awaitable[Dict[str, Any]]]


# ── request / response translation (pure, unit-tested) ───────────────────────

def extract_prompt(messages: List[Dict[str, Any]]) -> str:
    """The current user turn is the last user message (gateway always appends it)."""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def container_input(
    prompt: str,
    session_id: Optional[str],
    group_folder: str,
    is_main: bool,
    secrets: Mapping[str, str],
    assistant_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ContainerInput the entrypoint reads from stdin. Secrets ride here
    (stdin), NOT in env/argv, so they never surface in `docker inspect`."""
    return {
        "prompt": prompt,
        "sessionId": session_id,
        "groupFolder": group_folder,
        "isMain": bool(is_main),
        "assistantName": assistant_name,
        "secrets": dict(secrets or {}),
    }


def parse_container_output(stdout: str) -> Dict[str, Any]:
    """Extract the sentinel-wrapped ContainerOutput JSON. Fail closed: any missing/
    malformed output is an error, never an empty 'success'."""
    s = stdout.find(OUT_START)
    e = stdout.find(OUT_END)
    if s == -1 or e == -1 or e < s:
        return {"status": "error", "result": None, "error": "no cage output sentinel found"}
    blob = stdout[s + len(OUT_START):e].strip()
    try:
        obj = json.loads(blob)
    except ValueError:
        return {"status": "error", "result": None, "error": "malformed cage output json"}
    if not isinstance(obj, dict):
        return {"status": "error", "result": None, "error": "cage output not an object"}
    return obj


def _chunk_obj(content: str) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "aurum-agent",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }


def sse_content(content: str) -> bytes:
    """One OpenAI content-delta SSE event (the only thing the gateway consumes)."""
    return f"data: {json.dumps(_chunk_obj(content))}\n\n".encode("utf-8")


def sse_done() -> bytes:
    return b"data: [DONE]\n\n"


# ── secret brokering (per-request; fail closed) ──────────────────────────────

def broker_secrets(names: tuple = SECRET_ENV_NAMES) -> Dict[str, str]:
    """Resolve the per-request secrets. v1 source: host process env (populated from
    .env / OneCLI on the host, NOT in the cage). Returns only the names that resolve;
    the caller decides if a missing required secret means refuse.

    This is the single seam a OneCLI/secret-manager integration slots into later —
    swap the source here without touching the broker's request path.
    """
    out: Dict[str, str] = {}
    for n in names:
        v = os.environ.get(n)
        if v:
            out[n] = v
    return out


# ── the caged runner (the part that needs docker) ────────────────────────────

def build_docker_argv(
    mount_args: List[str],
    plain_env: Dict[str, str],
    network: Optional[str] = None,
    image: Optional[str] = None,
    name: Optional[str] = None,
) -> List[str]:
    """Build the `docker run` argv. Pure + testable (no subprocess).

    INVARIANT: only non-secret config (base_url, model) is ever passed as `-e`.
    Secrets travel via stdin inside ContainerInput, NEVER here — so they never appear
    in argv / `docker inspect` / `ps`. `network` is an optional `--network` passthrough
    (deployment's egress choice; see CAGE_NETWORK_ENV). `name` (optional) labels the
    container so a timed-out turn can be force-reaped (`docker rm -f`).
    """
    argv: List[str] = ["docker", "run", "--rm", "-i"]
    if name:
        argv += ["--name", name]
    # Run as the host user where the platform supports it, so files written to the
    # RW group mount are host-owned (best effort; Docker Desktop on Windows handles
    # ownership itself and has no getuid).
    if hasattr(os, "getuid"):
        argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
    if network:
        argv += ["--network", network]
    for k, v in (plain_env or {}).items():
        if v:
            argv += ["-e", f"{k}={v}"]      # non-secret config only
    argv += list(mount_args)
    argv.append(image or CAGE_IMAGE)
    return argv


CAGE_TURN_TIMEOUT_ENV = "AURUM_CAGE_TURN_TIMEOUT_SEC"
_DEFAULT_TURN_TIMEOUT = 600.0


def _turn_timeout() -> float:
    """Per-turn wall-clock cap. The cage OWNS the timeout (the container entrypoint deliberately
    sets none). A bad/non-positive env value falls back to the default rather than disabling it."""
    try:
        t = float(os.environ.get(CAGE_TURN_TIMEOUT_ENV, _DEFAULT_TURN_TIMEOUT))
    except (TypeError, ValueError):
        return _DEFAULT_TURN_TIMEOUT
    return t if t > 0 else _DEFAULT_TURN_TIMEOUT


async def _reap_container(name: str) -> None:
    """Best-effort force-remove a named container (the timed-out turn's). `docker run --rm` only
    reaps on a CLEAN exit, so a killed client can leave the container running — remove it explicitly."""
    try:
        p = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.wait()
    except Exception:  # noqa: BLE001 — reaping is best-effort; never raise from cleanup
        pass


async def docker_runner(
    ci: Dict[str, Any], mount_args: List[str], plain_env: Dict[str, str]
) -> Dict[str, Any]:
    """Run one turn in `docker run --rm`. Secrets are NOT here — they're inside `ci`
    (stdin). Only non-secret config (base_url, model) is passed as `-e`. A turn that exceeds
    the wall-clock cap is KILLED and its container reaped, so a hung turn (stalled model, dead
    proxy — the documented 19-minute hang) cannot pin the broker forever or leak a container."""
    name = f"aurum-turn-{uuid.uuid4().hex[:12]}"
    argv = build_docker_argv(
        mount_args, plain_env, network=os.environ.get(CAGE_NETWORK_ENV), name=name
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"status": "error", "result": None, "error": "docker not found on host"}
    timeout = _turn_timeout()
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(ci).encode("utf-8")), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await _reap_container(name)
        return {"status": "error", "result": None,
                "error": f"caged turn exceeded {timeout:.0f}s timeout; container killed"}
    except asyncio.CancelledError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await _reap_container(name)
        raise
    stdout = out.decode("utf-8", errors="replace")
    parsed = parse_container_output(stdout)
    if parsed.get("status") == "error" and proc.returncode not in (0, None):
        parsed["error"] = (err.decode("utf-8", "replace")[-500:] or parsed.get("error"))
    return parsed


# ── persistent container runner ──────────────────────────────────────────────
# When AURUM_PERSISTENT_CONTAINER=1 the broker keeps ONE named container alive
# and POSTs turns to its HTTP server (serve.py) instead of spawning --rm per turn.
# This removes the ~10-20s Docker cold-start; hermes subprocess startup (~3-7s)
# still applies per turn. The container is started automatically on first request.

PERSISTENT_PORT = 8901
PERSISTENT_CONTAINER_NAME = "aurum-agent-persistent"


async def _persistent_healthy() -> bool:
    try:
        from aiohttp import ClientSession, ClientTimeout
        async with ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{PERSISTENT_PORT}/health",
                timeout=ClientTimeout(total=2),
            ) as r:
                return r.status == 200
    except Exception:
        return False


async def _ensure_persistent(project_dir: str, groups_root: str) -> None:
    """Start the persistent container if it's not healthy. Idempotent."""
    # Purge any stale/stopped container with the same name.
    p = await asyncio.create_subprocess_exec(
        "docker", "rm", "-f", PERSISTENT_CONTAINER_NAME,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await p.wait()

    plain_env: Dict[str, str] = {
        "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", ""),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", ""),
    }
    argv: List[str] = [
        "docker", "run", "-d",
        "--name", PERSISTENT_CONTAINER_NAME,
        "-p", f"127.0.0.1:{PERSISTENT_PORT}:{PERSISTENT_PORT}",
    ]
    for k, v in plain_env.items():
        if v:
            argv += ["-e", f"{k}={v}"]
    # Mount project dir (RO) and groups root (RW, all groups in one mount).
    argv += [
        "-v", f"{project_dir}:/workspace/project:ro",
        "-v", f"{groups_root}:/workspace/groups",
    ]
    # Dockerfile: ENTRYPOINT ["python"], CMD ["/opt/aurum/entrypoint.py"]
    # Overriding CMD to serve.py runs: python /opt/aurum/serve.py
    argv += [CAGE_IMAGE, "/opt/aurum/serve.py"]

    p2 = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await p2.wait()

    # Wait up to 30s for the HTTP server to become ready.
    for _ in range(30):
        await asyncio.sleep(1)
        if await _persistent_healthy():
            return
    raise RuntimeError(
        f"aurum persistent container did not become healthy on :{PERSISTENT_PORT} after 30s"
    )


def make_persistent_runner(project_dir: str, groups_root: str) -> Runner:
    """Return a Runner that routes turns to the long-lived persistent container."""
    async def _run(
        ci: Dict[str, Any], mount_args: List[str], plain_env: Dict[str, str]
    ) -> Dict[str, Any]:
        # mount_args / plain_env are built by handle_turn for the ephemeral case;
        # we ignore mount_args (mounts are set at container start) and rebuild
        # plain_env from the ambient env (same values, always consistent).
        if not await _persistent_healthy():
            await _ensure_persistent(project_dir, groups_root)
        timeout = _turn_timeout()
        try:
            from aiohttp import ClientSession, ClientTimeout
            async with ClientSession() as s:
                async with s.post(
                    f"http://127.0.0.1:{PERSISTENT_PORT}/turn",
                    json=ci,
                    timeout=ClientTimeout(total=timeout),
                ) as r:
                    return await r.json()
        except asyncio.TimeoutError:
            return {
                "status": "error", "result": None,
                "error": f"persistent turn exceeded {timeout:.0f}s timeout",
            }
        except Exception as exc:
            return {"status": "error", "result": None, "error": f"persistent runner: {exc}"}
    return _run


# ── the broker ───────────────────────────────────────────────────────────────

class CageBroker:
    """Translates a gateway proxy request into a caged turn and back. The HTTP layer
    is thin; the testable logic (auth, translation, fail-closed) lives here."""

    def __init__(
        self,
        jail: Optional[MountJail] = None,
        runner: Runner = docker_runner,
        proxy_key: Optional[str] = None,
        require_secret: bool = True,
    ) -> None:
        self._jail = jail if jail is not None else jail_from_file()
        self._runner = runner
        self._proxy_key = proxy_key if proxy_key is not None else os.environ.get(PROXY_KEY_ENV, "")
        self._require_secret = require_secret

    def authorize(self, auth_header: Optional[str]) -> bool:
        """Bearer check. If no proxy key is configured, auth is open (dev). If one is
        configured, a missing/wrong token fails closed."""
        if not self._proxy_key:
            return True
        if not auth_header or not auth_header.startswith("Bearer "):
            return False
        return auth_header[len("Bearer "):].strip() == self._proxy_key

    def _resolve_group(self, session_id: Optional[str]) -> str:
        """Map X-Hermes-Session-Id to a per-group folder name. v1: a sanitised slug;
        the group's RW dir + allowlist are keyed off this."""
        sid = (session_id or "default").strip()
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in sid) or "default"
        return safe

    async def handle_turn(
        self,
        messages: List[Dict[str, Any]],
        session_id: Optional[str],
        project_dir: str,
        groups_root: str,
        extras: Optional[Mapping[str, str]] = None,
        is_main: bool = False,
    ) -> Dict[str, Any]:
        """Run one caged turn; returns a ContainerOutput dict. FAIL CLOSED on a
        denied mount or an unbrokerable required secret."""
        prompt = extract_prompt(messages)
        group_folder = self._resolve_group(session_id)
        group_dir = os.path.join(groups_root, group_folder)
        os.makedirs(group_dir, exist_ok=True)

        # Mounts through the jail — MountDenied aborts the turn (never over-broad).
        try:
            mounts = self._jail.build_mounts(project_dir, group_dir, extras or {})
        except MountDenied as e:
            return {"status": "error", "result": None, "error": f"mount denied: {e}"}

        # Per-request secrets (via stdin). Refuse if a required one is absent.
        secrets = broker_secrets()
        if self._require_secret and not any(secrets.values()):
            return {"status": "error", "result": None,
                    "error": "no provider secret could be brokered; refusing to run uncaged/unauthenticated"}

        ci = container_input(
            prompt=prompt, session_id=session_id, group_folder=group_folder,
            is_main=is_main, secrets=secrets,
        )
        # Non-secret config passed plainly (safe in docker inspect).
        plain_env = {
            "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", ""),
            "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", ""),
        }
        return await self._runner(ci, MountJail.docker_args(mounts), plain_env)


# ── HTTP app (aiohttp) ───────────────────────────────────────────────────────

def make_app(broker: Optional[CageBroker] = None):
    """Build the aiohttp app exposing POST /v1/chat/completions. Imported lazily so
    the module (and its pure helpers/tests) don't require aiohttp."""
    from aiohttp import web

    project_dir = os.environ.get("AURUM_PROJECT_DIR", os.getcwd())
    groups_root = os.environ.get(
        "AURUM_GROUPS_ROOT", os.path.join(os.path.expanduser("~"), ".aurum", "groups")
    )
    if broker is None:
        use_persistent = os.environ.get("AURUM_PERSISTENT_CONTAINER", "").lower() in (
            "1", "true", "yes"
        )
        runner: Runner = (
            make_persistent_runner(project_dir, groups_root) if use_persistent else docker_runner
        )
        broker = CageBroker(runner=runner)

    async def chat_completions(request: "web.Request") -> "web.StreamResponse":
        if not broker.authorize(request.headers.get("Authorization")):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        session_id = request.headers.get("X-Hermes-Session-Id")
        result = await broker.handle_turn(
            messages=body.get("messages") or [],
            session_id=session_id,
            project_dir=project_dir,
            groups_root=groups_root,
        )

        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if result.get("status") == "success":
            text = result.get("result") or ""
        else:
            text = f"⚠️ cage error: {result.get('error') or 'unknown'}"
        await resp.write(sse_content(text))
        await resp.write(sse_done())
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app


def main() -> None:
    from aiohttp import web

    host = os.environ.get("AURUM_CAGE_HOST", "127.0.0.1")
    port = int(os.environ.get("AURUM_CAGE_PORT", "8900"))
    web.run_app(make_app(), host=host, port=port)


if __name__ == "__main__":
    main()
