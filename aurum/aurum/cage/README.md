# Aurum cage

Per-request ephemeral-container execution behind Hermes' gateway proxy seam. The
gateway (chat + headless, ~25 channels) is **unchanged**; it delegates each turn to
this broker when a proxy URL is configured. See `PROXY_CONTRACT.md` for the wire shape.

```
Hermes gateway / cli  ──(POST /v1/chat/completions, SSE)──▶  cage broker (host)
                                                                  │  per turn
                                                                  ▼
                                                  docker run --rm aurum-agent
                                                  · mount_jail: allowlisted mounts only
                                                  · secrets via stdin (not docker inspect)
                                                  · entrypoint.py: one-turn hermes -q
```

## Components
- `mount_jail.py` — host-mount containment (deny-by-default, symlink/traversal safe).
  Covered by `AURUM_ERR_021`.
- `broker.py` — the OpenAI-compatible SSE server; runs each turn in the cage, fail-closed.
- `container/aurum/entrypoint.py` — in-container one-turn shim (reads stdin secrets).

## Run

Start the broker on the host:
```
AURUM_CAGE_IMAGE=aurum-agent:latest \
AURUM_PROJECT_DIR=/path/to/project \
AURUM_GROUPS_ROOT=~/.aurum/groups \
python -m aurum.cage.broker          # serves http://127.0.0.1:8900
```

Grant host-dir access via the allowlist (host-owned, outside any agent-writable mount):
```
~/.config/aurum/mount-allowlist.json   ->   ["/srv/shared", "/data/inbox"]
```
Missing/empty file = deny-by-default (no extra host access).

## Wiring `_get_proxy_url()` — the in-process ↔ caged switch

The gateway's `_get_proxy_url()` checks `GATEWAY_PROXY_URL` (env), then `gateway.proxy_url`
(config.yaml). One value flips the whole system; **no gateway code changes**:

| Mode | How | Execution |
|------|-----|-----------|
| **Dev (in-process)** | leave `GATEWAY_PROXY_URL` unset / `gateway.proxy_url` empty | gateway runs `AIAgent` in-process (fast, no isolation) |
| **Prod (caged)** | `GATEWAY_PROXY_URL=http://127.0.0.1:8900` (or `gateway.proxy_url` in config.yaml) | every turn runs in a `--rm` cage via this broker |

Optional `GATEWAY_PROXY_KEY` — if set, the gateway sends `Authorization: Bearer <key>`
and the broker rejects anything else (fail closed).

## Secrets

Provider auth (`ANTHROPIC_AUTH_TOKEN` / `OPENROUTER_API_KEY` / `PIPELINE_API_KEY`) is
brokered **per request** from the host env and passed to the container on **stdin**, never
as `-e`/argv — so `docker inspect` shows no secret. `broker.broker_secrets()` is the single
seam to swap the host env for OneCLI / a secret manager later. If no provider secret can be
brokered, the turn is refused (never run unauthenticated).
