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
# Persist Hermes session/state under the per-group RW mount so --resume works
# across the ephemeral `docker run --rm` (the cage's persistence rule).
HERMES_HOME = os.path.join(GROUP_DIR, ".hermes")
# Canonical Aurum constitution shipped in the image (see Dockerfile COPY).
SOUL_TEMPLATE = "/opt/aurum/SOUL.md"

_ERROR_PREFIXES = ("Error code:", "API call failed", "Error:")


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


def main() -> int:
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:  # malformed input — report, don't crash
        emit({"status": "error", "result": None, "error": "bad input json: %s" % exc})
        return 0

    prompt = inp.get("prompt", "") or ""
    session_id = inp.get("sessionId")
    # Secrets arrive on stdin (never via docker -e), so they stay out of
    # `docker inspect`. Injected into the in-container env for hermes + tools below.
    secrets = inp.get("secrets") or {}

    os.makedirs(HERMES_HOME, exist_ok=True)
    _seed_soul(HERMES_HOME, inp.get("assistantName"))
    _ensure_plugin_enabled(HERMES_HOME, "aurum-governance")

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
    if session_id:
        cmd += ["--resume", session_id]

    env = dict(os.environ)
    # Inject stdin-brokered secrets into the runtime env (NOT visible in docker
    # inspect, which only shows the `-e` config set at `docker run`).
    for _k, _v in secrets.items():
        if _v:
            env[str(_k)] = str(_v)
    env["HERMES_HOME"] = HERMES_HOME
    env["HERMES_INTERACTIVE"] = "0"
    # Secure-by-default in the cage: scan self-authored skills on every write
    # (Policy Kernel / skills_guard). A persisted skill can detonate later, so
    # unlike stock Hermes we do not leave this opt-in. See skill_manager_tool
    # ._guard_agent_created_enabled.
    env["AURUM_GUARD_SKILLS"] = "1"
    # Validate-before-promote: run a skill's tests in a hardened subprocess on
    # write (Skill-CI / Regression Guard). See tools/skill_ci.py.
    env["AURUM_SKILL_CI"] = "1"
    # Offer the Toolsmith (propose_tool) in the cage. It can only stage a scanned,
    # sandbox-tested proposal for human review — never activate a tool. See
    # tools/toolsmith.py.
    env["AURUM_TOOLSMITH"] = "1"
    # Route every tool call through the governance spine (PK → AG → CA → EL) before it
    # executes, via the aurum-governance pre_tool_call hook. Fail-closed: blocks on policy
    # deny, gate, or governance fault. See plugins/aurum-governance + aurum.kernel.
    env["AURUM_GOVERNANCE"] = "1"

    cwd = GROUP_DIR if os.path.isdir(GROUP_DIR) else "/opt/hermes"
    # No timeout here — the cage owns the wall-clock timeout and kills the container.
    try:
        proc = subprocess.run(
            cmd, input="", capture_output=True, text=True, cwd=cwd, env=env
        )
    except Exception as exc:
        emit({"status": "error", "result": None, "error": "hermes spawn failed: %s" % exc})
        return 0

    # Hermes prints `session_id: <id>` to STDERR (verified), the answer to STDOUT.
    new_session = None
    for stream in (proc.stderr or "", proc.stdout or ""):
        for line in stream.split("\n"):
            if line.startswith("session_id:"):
                new_session = line.split("session_id:", 1)[1].strip()

    # Build the answer from stdout, dropping Hermes' own noise lines (e.g. the
    # `⚠ tirith security scanner …` notice and any stray session_id line).
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

    is_error = answer.startswith(_ERROR_PREFIXES) or (not answer and proc.returncode != 0)
    if is_error:
        emit({
            "status": "error",
            "result": None,
            "error": answer or (proc.stderr or "")[-500:] or "hermes produced no output",
            "newSessionId": new_session,
        })
    else:
        emit({"status": "success", "result": answer, "newSessionId": new_session})
    return 0


if __name__ == "__main__":
    sys.exit(main())
