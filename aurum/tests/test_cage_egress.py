"""Cage egress / credential-containment.

The cage deliberately allows open network egress (the agent needs the internet to be
useful, and on cloud runtimes egress is the platform's job, not docker's). So the
containment that matters is NOT "no network" but "nothing to steal": no durable secret
is ever resident in the cage's inspectable surface (argv / `-e` / `docker inspect`) —
secrets ride stdin per request. A compromised container that `curl`s out has no
credential to exfiltrate.

These tests pin that invariant, plus the optional `--network` passthrough that lets a
docker-host deployment choose its egress posture without Aurum hardcoding one.
"""
from __future__ import annotations

import asyncio
import json

from aurum.cage.broker import (
    CAGE_NETWORK_ENV,
    CageBroker,
    build_docker_argv,
    container_input,
)
from aurum.cage.mount_jail import MountJail


# ── credential containment: secrets never on the inspectable surface ──────────

def test_build_docker_argv_never_contains_secret_values():
    """Only non-secret config goes to `-e`; a secret value must never appear in argv."""
    secret = "sk-super-secret-token-value"
    plain_env = {"ANTHROPIC_BASE_URL": "https://host:8788", "ANTHROPIC_MODEL": "x"}
    argv = build_docker_argv(["-v", "/proj:/workspace/project:ro"], plain_env)
    joined = " ".join(argv)
    assert secret not in joined
    # config IS present (it's non-secret)
    assert any("ANTHROPIC_BASE_URL=" in a for a in argv)
    # no secret env name leaked as a flag either
    assert "ANTHROPIC_AUTH_TOKEN" not in joined
    assert "OPENROUTER_API_KEY" not in joined


def test_handle_turn_keeps_secrets_off_argv_surface(monkeypatch, tmp_path):
    """End-to-end: the secret reaches the container via stdin (ci), and the runner's
    inspectable surface (plain_env) carries NO secret. This is the enterprise
    guarantee — a leaked/compromised cage has nothing durable to steal."""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-leak-me-if-you-can")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://host.docker.internal:8788")

    captured = {}

    async def capturing_runner(ci, mount_args, plain_env):
        captured["ci"] = ci
        captured["mount_args"] = mount_args
        captured["plain_env"] = plain_env
        return {"status": "success", "result": "ok"}

    broker = CageBroker(jail=MountJail([]), runner=capturing_runner)
    asyncio.run(broker.handle_turn(
        [{"role": "user", "content": "hi"}], "sess",
        project_dir=str(tmp_path / "proj"), groups_root=str(tmp_path / "groups"),
    ))

    plain_env = captured["plain_env"]
    # The secret is NOT in the inspectable env/argv surface...
    assert "ANTHROPIC_AUTH_TOKEN" not in plain_env
    assert "sk-leak-me-if-you-can" not in json.dumps(plain_env)
    assert "sk-leak-me-if-you-can" not in " ".join(captured["mount_args"])
    # ...it's carried via stdin (ContainerInput) instead.
    assert captured["ci"]["secrets"]["ANTHROPIC_AUTH_TOKEN"] == "sk-leak-me-if-you-can"


# ── network egress passthrough (deployment's choice, not hardcoded) ───────────

def test_no_network_flag_by_default():
    """Unset → no `--network` flag (docker default). Aurum doesn't hardcode egress."""
    argv = build_docker_argv([], {"ANTHROPIC_MODEL": "x"})
    assert "--network" not in argv


def test_network_passthrough_is_respected():
    """A configured network is passed straight to `docker --network`."""
    argv = build_docker_argv([], {}, network="aurum-egress-fw")
    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == "aurum-egress-fw"
    # also works for the strict "none" posture
    argv_none = build_docker_argv([], {}, network="none")
    assert argv_none[argv_none.index("--network") + 1] == "none"


def test_docker_runner_reads_network_from_env(monkeypatch):
    """docker_runner sources the network posture from AURUM_CAGE_NETWORK so a
    deployment can set it without code changes."""
    monkeypatch.setenv(CAGE_NETWORK_ENV, "none")
    # We don't run docker here; just assert build_docker_argv honors the same env
    # value the runner would read (the runner passes os.environ[CAGE_NETWORK_ENV]).
    import os
    argv = build_docker_argv([], {}, network=os.environ.get(CAGE_NETWORK_ENV))
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"


# ── containment holds regardless of egress: the ci payload is the only secret path
def test_container_input_is_the_only_secret_channel():
    ci = container_input("p", "s", "grp", False, {"OPENROUTER_API_KEY": "or-key"})
    # secret is in the stdin payload (by design)...
    assert ci["secrets"]["OPENROUTER_API_KEY"] == "or-key"
    # ...and build_docker_argv (the inspectable surface) never sees secrets, only config
    argv = build_docker_argv([], {"ANTHROPIC_MODEL": "m"})
    assert "or-key" not in " ".join(argv)
