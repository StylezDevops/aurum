#!/usr/bin/env python3
"""Aurum entrypoint — the seam between the cage broker and the Hermes brain.

The Aurum cage broker (`aurum.cage.broker`) spawns this container per turn with:
  * stdin: one JSON ContainerInput  {prompt, sessionId?, groupFolder, isMain,
           assistantName?, secrets?}
           — SECRETS arrive on STDIN, never via `-e`/argv, so they never appear in
             `docker inspect`. We inject them into the in-container process env here.
  * env:   ANTHROPIC_BASE_URL, ANTHROPIC_MODEL (non-secret config only)
  * mounts: /workspace/group (RW, persistent), /workspace/project (RO),
            /workspace/extra/* (allowlisted, jailed by `aurum.cage.mount_jail`)

and the broker stream-parses stdout for ContainerOutput JSON wrapped in sentinels:
  ---AURUM_OUTPUT_START---
  {"status": "...", "result": "...", "newSessionId": "...", "error": "..."}
  ---AURUM_OUTPUT_END---

This translates that contract to a single-shot `hermes -q` call and back. Verified
Hermes contract (docs/hermes-io-contract.md): in --quiet mode stdout is the answer
text followed by a trailing `session_id: <id>` line; API errors print on the answer
line and the process still exits 0, so we detect error-shaped text.

`run_turn(inp, group_dir)` is also imported by serve.py (persistent container mode).
"""
import json
import os
import re
import subprocess
import sys

# Terminal color/cursor escapes Hermes uses on status lines; LLM answers never
# contain these, so stripping them is safe and de-noises the result.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

START = "---AURUM_OUTPUT_START---"
END = "---AURUM_OUTPUT_END---"
HERMES_CLI = "/opt/hermes/cli.py"
GROUP_DIR = "/workspace/group"
# Canonical Aurum constitution shipped in the image (see Dockerfile COPY).
SOUL_TEMPLATE = "/opt/aurum/SOUL.md"

_ERROR_PREFIXES = ("Error code:", "API call failed", "Error:")

def _load_session_map(hermes_home: str) -> dict:
    """Load the gateway→hermes session ID map from HERMES_HOME."""
    try:
        with open(os.path.join(hermes_home, "session_map.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_session_map(hermes_home: str, m: dict) -> None:
    try:
        with open(os.path.join(hermes_home, "session_map.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
    except OSError:
        pass  # non-fatal


def emit(output: dict) -> None:
    """Write one sentinel-wrapped ContainerOutput to stdout."""
    sys.stdout.write("\n%s\n%s\n%s\n" % (START, json.dumps(output), END))
    sys.stdout.flush()


def _seed_soul(hermes_home: str, assistant_name) -> None:
    """Seed the Aurum constitution into HERMES_HOME/SOUL.md on first run only.

    Seed-if-absent, never clobber: per-group edits persist, fresh groups get the
    identity + always-on guardrails. Hermes loads HERMES_HOME/SOUL.md as its
    primary identity slot (agent/prompt_builder.load_soul_md). The persona name is
    install-selectable, so substitute it here. Failures are non-fatal — a single
    answer doesn't require the identity file, and Hermes has its own fallback.
    """
    dest = os.path.join(hermes_home, "SOUL.md")
    if os.path.exists(dest):
        return
    try:
        with open(SOUL_TEMPLATE, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return  # template missing — Hermes falls back to its own default identity
    name = (assistant_name or os.environ.get("ASSISTANT_NAME") or "Andy").strip()
    text = text.replace("{{ASSISTANT_NAME}}", name)
    try:
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass  # non-fatal: identity is a nicety, not required to answer one message


def _ensure_plugin_enabled(hermes_home: str, plugin_name: str) -> None:
    """Idempotently add `plugin_name` to `plugins.enabled` in HERMES_HOME/config.yaml.

    Hermes plugins are opt-in via the `plugins.enabled` allow-list
    (hermes_cli.plugins._get_enabled_plugins, read from get_config_path() =
    HERMES_HOME/config.yaml). The cage must enable aurum-governance so the governance
    hook actually loads. Seed-merge, never clobber other settings; non-fatal on error.
    """
    try:
        import yaml  # PyYAML is a core Hermes dependency
    except Exception:
        return
    cfg_path = os.path.join(hermes_home, "config.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except OSError:
        cfg = {}
    except Exception:
        return  # malformed config — don't risk clobbering it
    if not isinstance(cfg, dict):
        return
    plugins = cfg.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
    if plugin_name in enabled:
        return  # already enabled — idempotent no-op
    enabled.append(plugin_name)
    plugins["enabled"] = enabled
    cfg["plugins"] = plugins
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
    except OSError:
        pass  # non-fatal: governance hook simply won't load this run


def _prepare_mcp_servers(hermes_home: str, group: str) -> None:
    """CONTAINER LOAD-FROM-MOUNT (Phase F): stage the ENABLED MCP servers for this group so the
    MCP layer can register them this turn. register-not-install — nothing is downloaded; a remote
    (http) server needs only its url + secret_ref (OneCLI resolves the value at request time), so
    it survives `--rm` by construction. Reads the durable mount registry
    (HERMES_HOME/mcp-servers.json) and writes the filtered injection plan to
    HERMES_HOME/mcp-active.json. Idempotent + NON-FATAL: no registry / no enabled servers → no-op;
    any error is swallowed (a single message must not fail because the registry is unreadable)."""
    if not group:
        return
    try:
        try:
            from aurum.mcp import load_enabled_servers
        except ImportError:
            from aurum.aurum.mcp import load_enabled_servers  # type: ignore[no-redef]
    except Exception:
        return
    try:
        plan = load_enabled_servers(hermes_home, group)
    except Exception:
        return
    if not plan:
        return
    try:
        with open(os.path.join(hermes_home, "mcp-active.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, sort_keys=True)
    except OSError:
        pass  # non-fatal: the MCP layer simply registers nothing extra this turn


def run_turn(inp: dict, group_dir: str = GROUP_DIR) -> dict:
    """Run one Hermes turn and return a ContainerOutput dict.

    Extracted so serve.py (persistent container mode) can call it without the
    stdin/stdout wrapper. group_dir defaults to the single-group legacy mount;
    serve.py passes the per-group path under /workspace/groups/{folder}.
    """
    hermes_home = os.path.join(group_dir, ".hermes")

    prompt = inp.get("prompt", "") or ""
    session_id = inp.get("sessionId")
    secrets = inp.get("secrets") or {}

    os.makedirs(hermes_home, exist_ok=True)
    _seed_soul(hermes_home, inp.get("assistantName"))
    _ensure_plugin_enabled(hermes_home, "aurum-governance")
    _prepare_mcp_servers(hermes_home, str(inp.get("groupFolder") or ""))

    cmd = [sys.executable, HERMES_CLI, "-q", prompt, "--provider", "openrouter", "--quiet"]
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    api_key = (secrets.get("ANTHROPIC_AUTH_TOKEN")
               or secrets.get("OPENROUTER_API_KEY")
               or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    model = os.environ.get("ANTHROPIC_MODEL")
    if base_url:
        cmd += ["--base-url", base_url]
    if api_key:
        cmd += ["--api-key", api_key]
    if model:
        cmd += ["--model", model]
    session_map = _load_session_map(hermes_home)
    hermes_session = session_map.get(session_id) if session_id else None
    if hermes_session:
        cmd += ["--resume", hermes_session]

    env = dict(os.environ)
    for _k, _v in secrets.items():
        if _v:
            env[str(_k)] = str(_v)
    if api_key and not env.get("OPENROUTER_API_KEY"):
        env["OPENROUTER_API_KEY"] = api_key
    env["HERMES_HOME"] = hermes_home
    env["HERMES_INTERACTIVE"] = "0"
    env["AURUM_GUARD_SKILLS"] = "1"
    env["AURUM_SKILL_CI"] = "1"
    env["AURUM_TOOLSMITH"] = "1"
    env["AURUM_GOVERNANCE"] = "1"

    cwd = group_dir if os.path.isdir(group_dir) else "/opt/hermes"
    try:
        proc = subprocess.run(
            cmd, input="", capture_output=True, text=True, cwd=cwd, env=env
        )
    except Exception as exc:
        return {"status": "error", "result": None, "error": "hermes spawn failed: %s" % exc}

    new_session = None
    for stream in (proc.stderr or "", proc.stdout or ""):
        for line in stream.split("\n"):
            if line.startswith("session_id:"):
                new_session = line.split("session_id:", 1)[1].strip()

    answer_lines = []
    for raw in (proc.stdout or "").rstrip("\n").split("\n"):
        line = _ANSI.sub("", raw)
        stripped = line.strip()
        if stripped.startswith("session_id:"):
            new_session = stripped.split("session_id:", 1)[1].strip()
            continue
        if stripped.startswith("⚠") or "tirith security scanner" in stripped:
            continue
        answer_lines.append(line)
    answer = "\n".join(answer_lines).strip()

    if session_id and new_session:
        session_map[session_id] = new_session
        _save_session_map(hermes_home, session_map)

    is_error = answer.startswith(_ERROR_PREFIXES) or (not answer and proc.returncode != 0)
    if is_error:
        return {
            "status": "error",
            "result": None,
            "error": answer or (proc.stderr or "")[-500:] or "hermes produced no output",
            "newSessionId": new_session,
        }
    return {"status": "success", "result": answer, "newSessionId": new_session}


def main() -> int:
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        emit({"status": "error", "result": None, "error": "bad input json: %s" % exc})
        return 0
    emit(run_turn(inp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
