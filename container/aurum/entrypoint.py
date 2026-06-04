#!/usr/bin/env python3
"""Aurum entrypoint shim — the seam between nanoclaw's cage and the Hermes brain.

nanoclaw spawns this container per message with:
  * stdin: one JSON ContainerInput  {prompt, sessionId?, groupFolder, chatJid,
           isMain, isScheduledTask?, assistantName?, script?}
  * env:   ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, ANTHROPIC_AUTH_TOKEN (OpenRouter)
  * mounts: /workspace/group (RW, persistent), /workspace/project (RO, main),
            /workspace/extra/* (allowlisted), /workspace/ipc (RW)

and stream-parses stdout for ContainerOutput JSON wrapped in sentinel markers:
  ---NANOCLAW_OUTPUT_START---
  {"status": "...", "result": "...", "newSessionId": "...", "error": "..."}
  ---NANOCLAW_OUTPUT_END---

This shim translates that contract to a single-shot `hermes -q` call and back.
Verified Hermes contract (docs/hermes-io-contract.md): in --quiet mode stdout is
the answer text followed by a trailing `session_id: <id>` line; API errors print
on the answer line and the process still exits 0, so we detect error-shaped text.
"""
import json
import os
import re
import subprocess
import sys

# Terminal color/cursor escapes Hermes uses on status lines; LLM answers never
# contain these, so stripping them is safe and de-noises the result.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

START = "---NANOCLAW_OUTPUT_START---"
END = "---NANOCLAW_OUTPUT_END---"
HERMES_CLI = "/opt/hermes/cli.py"
GROUP_DIR = "/workspace/group"
# Persist Hermes session/state under the per-group RW mount so --resume works
# across the ephemeral `docker run --rm` (matches nanoclaw's persistence rule).
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


def main() -> int:
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:  # malformed input — report, don't crash
        emit({"status": "error", "result": None, "error": "bad input json: %s" % exc})
        return 0

    prompt = inp.get("prompt", "") or ""
    session_id = inp.get("sessionId")

    os.makedirs(HERMES_HOME, exist_ok=True)
    _seed_soul(HERMES_HOME, inp.get("assistantName"))

    cmd = [sys.executable, HERMES_CLI, "-q", prompt, "--provider", "openrouter", "--quiet"]
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
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

    cwd = GROUP_DIR if os.path.isdir(GROUP_DIR) else "/opt/hermes"
    # No timeout here — nanoclaw owns the wall-clock timeout and kills the container.
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
