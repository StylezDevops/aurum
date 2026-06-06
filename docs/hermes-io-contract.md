# Hermes ↔ Aurum cage I/O contract (Aurum Phase 0 deliverable)

Documents the seam Aurum's cage must speak to. **Corrects the build plan:** there is no
`run_agent.py`. The real headless entrypoint is `cli.py` (≈15k lines, exposed as the `hermes`
command via `hermes_cli.main:main`). The cage shim that drives it is `container/aurum/entrypoint.py`.

## Headless single-shot invocation (the seam)

`main()` in `cli.py` accepts these relevant args (CLI flags shown):

| Flag | Meaning |
|------|---------|
| `-q` / `--query "TEXT"` | **Single query, then exit** (headless mode). Without it, starts the interactive prompt_toolkit TUI. |
| `--provider` | `auto`, **`openrouter`**, `nous`, `openai-codex`, `zai`, `kimi-coding`, `minimax`, `minimax-cn` |
| `--base-url URL` | API base URL (set for OpenRouter) |
| `--api-key KEY` | API key (OpenRouter key) |
| `--model NAME` | default `anthropic/claude-opus-4-20250514` |
| `--resume ID` | **Resume a prior session** by id, format `YYYYMMDD_HHMMSS_xxxxxx` → session persistence hook |
| `--pass-session-id` | emit/accept session id for round-tripping |
| `--max-turns N` | tool-calling iterations, default 60 |
| `--skills a,b` / `--toolsets web,terminal` | preload skills / enable toolsets |
| `--quiet` / `--compact` | reduce display chatter |
| `--image PATH` | attach a local image to the query |
| `--gateway` | start the messaging+cron gateway instead (NOT what the cage wants per-message) |

**Aurum's per-message call (host → container) will be:**
```
hermes -q "<prompt>" \
  --provider openrouter --base-url <OR_URL> --api-key <OR_KEY> \
  --model <model> --resume <sessionId> --quiet --max-turns <N>
```
This maps cleanly onto the cage's `ContainerInput` ({prompt, sessionId, model, …}).

## Output  (VERIFIED 2026-06-03)

In `--quiet` single-query mode, stdout is **just the answer text**, then a blank line, then
`session_id: <YYYYMMDD_HHMMSS_xxxxxx>`. Verified live against OpenRouter
(`moonshotai/kimi-k2.6:free`):

```
AURUM_OK

session_id: 20260603_182647_d33f6f
```

So the Phase-1 sentinel shim is simple: capture stdout, split off the trailing
`session_id:` line (feed it back as `--resume` next turn), and wrap the remaining answer in
`---AURUM_OUTPUT_START---` / `---AURUM_OUTPUT_END---`. Errors surface on the answer line
too (e.g. `Error code: 402 …`, `API call failed after 3 retries: HTTP 429 …`) followed by the
same `session_id:` line and exit 0 — so the shim must detect error-shaped answers, not rely on
exit code.

Hermes otherwise prints Rich box-drawing to stdout (e.g. `--list-tools`); `--quiet` suppresses
that for query mode. On Windows it force-sets UTF-8 stdio.

## Container entrypoint (heavy — s6-overlay)

- `ENTRYPOINT [ "/init", "/opt/hermes/docker/main-wrapper.sh" ]`, `CMD [ ]`. `/init` (s6) is
  PID 1; `cont-init.d` runs `stage2-hook.sh` (UID remap, chown, config seed, skills sync)
  before the CMD. `WORKDIR /opt/hermes`.
- `docker/entrypoint.sh` is a **deprecated shim** — do not target it; it only runs bootstrap,
  not the CMD.
- For Aurum, the container must run a **single query then exit** (not the gateway). Options:
  pass `-q …` args through `main-wrapper.sh`, or add an Aurum-specific wrapper that execs
  `hermes -q …` after s6 cont-init. Must verify `main-wrapper.sh` forwards args (Phase 1).
- Image is large (torch/transformers-class deps + s6). Plan for build time and bake the
  embedding/model assets to avoid per-run downloads.

## Config & persistence

- Config: `cli-config.yaml` (see `cli-config.yaml.example`, ~62KB) + `.env`
  (`.env.example` ~23KB). Provider/base_url/key can come from flags (preferred for the cage)
  or config/env.
- Sessions resume via `--resume <id>`; persist Hermes session/state dir onto the per-group
  host dir (`groups/{name}/.hermes/`) so it survives `docker run --rm`.

## Provider note (OpenRouter)  — ACCOUNT BLOCKER

`--provider openrouter` is first-class and verified working. **But the OpenRouter balance is
depleted:** the configured paid model `google/gemini-2.5-flash` returns `402 Insufficient
credits`, and most `:free` models 429 (rate-limited / need a minimum balance). One free model
(`moonshotai/kimi-k2.6:free`) completed. **Aurum cannot run for real until the user tops up
OpenRouter credits** — this gates every live agent turn, not just testing.

The cage already holds the OpenRouter key (`.env ANTHROPIC_AUTH_TOKEN`) and base URL. In Aurum the container's LLM calls route to the
shared host headroom proxy (`host.docker.internal:8788`, `--backend openrouter`) → OpenRouter,
so pass `--base-url http://host.docker.internal:8788` instead of OpenRouter directly once the
proxy is enabled (Phase 2/5).

## Open items for Phase 1

1. Confirm `main-wrapper.sh` arg forwarding for `-q` single-shot in-container.
2. Build the stdout→sentinel shim; pin the final-answer delimiter under `--quiet`.
3. Map `.hermes` state dir to `/workspace/group/.hermes`.
4. Decide base-url target (direct OpenRouter for first smoke test; headroom :8788 after).
