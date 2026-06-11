# Aurum

Aurum is an always-on, **caged** personal agent for Dan (24 Karat Recordings — jungle/DnB
label, Hull; Azure DevSecOps engineer). It runs a forked **Hermes** reasoning brain (Nous
Research, MIT-licensed — their brain, not ours) inside **Aurum's own security cage**: a thin
launcher that owns channels, sandbox, and the credential gateway, spawning one ephemeral
container per message. Wrapped around the brain is Aurum's **governance spine** — the organs
(`aurum/aurum/`) that make every self-modifying path gated, sandboxed, fail-closed, and
replayable. Best isolation + best capability + enforced restraint, one codebase, fully ours.

> Full build plan (Hermes brain + cage): `C:\Users\24kar\.claude\plans\groovy-snuggling-lecun.md`
> (phases, verification gates, file-by-file changes). Read it before executing a phase.
> Organs architecture: `C:\Users\24kar\Projects\aurum\aurum_organs_spec.md` (PK/EL/AG/…).
> Sprint roadmap: `C:\Users\24kar\Projects\aurum\aurum_build_sprints.md` (build order, gates).
> LS design deep-dive: `C:\Users\24kar\Projects\aurum\aurum_LS_design.md` (hardest organ — read before any LS phase).
> Arbitration layer: `C:\Users\24kar\Projects\aurum\aurum_arbitration_spec.md` (metric conflict + governance deadlock — Conflict Arbiter + Deadlock Detector, fully spec'd).

> **Pending task — Hermes→aurum rename.** The brain in `C:\Users\24kar\Projects\aurum` is forked
> from Nous Research's hermes-agent, **MIT-licensed** (`aurum/LICENSE`, © 2025 Nous Research). MIT
> permits free renaming/modification; the ONLY obligation is keeping that copyright + permission
> notice in `LICENSE` (and any source headers that carry it). So renaming dirs/identifiers
> hermes→aurum is fully clear — just never delete Nous's notice, and add your own copyright
> alongside rather than replacing it. Treat as its own careful sprint (touches imports brain-wide).

- **Repo / image / doc naming:** `aurum` / `aurum-agent:latest` / this file.
- **Chat persona:** user-selectable **at install** (env `ASSISTANT_NAME`, prompted by
  `/setup`). Neutral default — **do NOT default to "Robbie"** (used elsewhere).
- **v1 ship gate:** (1) 24KR release-pipeline integration, (2) Gmail + 2FA capture.
  Memory/headroom-by-default and social media are wired but deferred to later phases.
- **Authority model — v1 is asymmetric, and that asymmetry is the differentiator.** Most systems
  start by automating trust GROWTH; Aurum starts by automating trust LOSS. Authority CONTRACTION
  is automatic (a proxy failure reflexively demotes — `observe_outcome`); authority EXPANSION
  requires a human-grounded outcome that is cryptographically VERIFIABLE — an ed25519-signed
  operator verdict, the operator holding the private key OUTSIDE the cage
  (`submit_operator_verdict` → `integrations/operator_verdict.py`; sign with
  `scripts/sign_operator_verdict.py`). No operator key deployed ⇒ no promotion is possible ⇒
  contraction-only (the default-safe posture). Every promotion is attributable by value
  (authority↑ *because* verdict V *signed by* operator K) — governance provenance, not just a
  score. Roadmap: **v1** automatic contraction + signed-human expansion; **v2** limited
  evidence-backed expansion; **v3** studied expansion dynamics. The decision/snapshot ledger is
  the institutional memory the score is a compression of.

## Architecture

```
Channels (Telegram / Gmail / …) ── Aurum gateway + cage (host)
      │  message → group resolved → mount-allowlist.json consulted (tamper-proof)
      ▼
  docker run --rm  (ephemeral, host UID)
      │  RO: /workspace/project   RW: /workspace/group   allowlisted host dirs → /workspace/extra/*
      ▼
  [container]  Hermes brain (forked)  — skills loop · tools · MCP · cron · subagents
      │  LLM calls → host.docker.internal:8788 (headroom) → OpenRouter
      ▼
  OneCLI gateway injects secrets over HTTPS — container never holds keys/tokens
```

## Headroom (context optimization) — TWO host proxies

Headroom's MCP tools (`compress`/`retrieve`/`stats`) don't compute locally — they call a
**local proxy**. **If the proxy is down, the tool calls block forever** — that was the
original "headroom hung for 19 minutes" bug. Fix = keep the proxy up. First start downloads
an ONNX model from HuggingFace (one-time, slow); after that startup is fast.

One proxy instance forwards to exactly **one** upstream, so we run two:

| Port  | Backend      | Serves                         | Upstream    |
|-------|--------------|--------------------------------|-------------|
| 8787  | `anthropic`  | Claude Code coding loop (CLI)  | Anthropic   |
| 8788  | `openrouter` | Aurum agent (inside the cage)  | OpenRouter  |

- Both are **long-lived on the host**, started and stopped by Aurum's host launcher. We
  deliberately do **NOT** run a headroom proxy *inside* each
  container — ephemeral `--rm` containers would reload models every message and lose their
  cache. The shared host proxy stays warm and keeps one semantic cache.
- `:8788` is gated behind `AURUM_PROXY=1` (set `setx AURUM_PROXY 1`) so it only loads models
  once the Aurum agent is cut over (Phase 2). Its OpenRouter key is read from `.env`
  (`ANTHROPIC_AUTH_TOKEN`) and passed as `OPENROUTER_API_KEY`.
- Claude Code is routed through `:8787` via `.claude/settings.local.json`
  (`env.ANTHROPIC_BASE_URL=http://127.0.0.1:8787`) — **CLI-scoped only**. Never set
  `ANTHROPIC_BASE_URL` globally: the cage already uses that var to point the container agent
  at OpenRouter, so a global value would break agent routing.
- Native fallback: prefer Hermes's `trajectory_compressor.py` as the reliable default;
  treat the headroom MCP as optional behind a health check + short timeout. Never let a
  flaky proxy be a hard dependency.

## Persistence (must survive `--rm`)

Containers are ephemeral; anything stateful is mounted onto the host per-group dir:

- **Hermes session memory (FTS5 sqlite), learned skills, trajectory-compressor state** →
  `groups/{name}/.hermes/` (mounted per-group). The cage persists `groups/{name}/` on the host.
- **Headroom agent cache/memory** → lives in the long-lived `:8788` host proxy, so it
  persists naturally (another reason for shared-host over per-container).
- **memory MCP (knowledge graph)** + FTS5 store → per-group dir / named volume (Phase 5).

## Current install state (Windows)

- Windows 11 Pro + WSL2 (Git Bash), Docker Desktop. No launchd/systemd — the cage launches
  from the Windows startup folder on login.
- **Channel: Telegram only** (WhatsApp removed). Bot `@TFKayBot`, token in `.env`
  `TELEGRAM_BOT_TOKEN`. Registered chat `tg:8712784438`, folder `telegram_main`.
- **Provider: OpenRouter** (`ANTHROPIC_BASE_URL=https://openrouter.ai/api/v1`,
  key in `.env` `ANTHROPIC_AUTH_TOKEN`, model `ANTHROPIC_MODEL`). Container agent is
  OpenAI-compatible.
- **The Hermes brain runs per message**: `hermes -q` (single-shot headless turn) is invoked
  once per message by `container/aurum/entrypoint.py`, which speaks Aurum's stdin-prompt /
  sentinel `AURUM_OUTPUT` contract (see `docs/hermes-io-contract.md`). Tools, skills loop, MCP,
  cron, and subagents are all Hermes-native. Sessions persist via `--resume` into
  `/workspace/group/.hermes/`.
- **Headroom** installed in a dedicated Python 3.13 venv (`~/headroom-venv`) — the Rust/PyO3
  extension doesn't build on 3.14. Launcher shim: `…/WindowsApps/headroom.cmd`. MCP
  registered globally in `~/.claude.json` as `headroom mcp serve`.

## Key files

| File | Purpose |
|------|---------|
| `cli.py` | Hermes brain entrypoint; `hermes -q` = single-shot headless turn |
| `container/aurum/entrypoint.py` | Per-message shim: seeds SOUL.md, runs `hermes -q`, speaks the `AURUM_OUTPUT` sentinel contract |
| `container/aurum/Dockerfile` | Slim single-shot Aurum brain image (`aurum-agent:latest`) |
| `container/aurum/SOUL.md` | Aurum constitution — identity + always-on directives, seeded per-group |
| `aurum/aurum/` | **The governance spine** — organs (PK/BB/TS + EL/RR/AG/AA/HVP/EG/OI/LS/CS/…) + the AURUM_ERR compliance harness |
| `aurum/aurum/durability/evidence_ledger.py` | Evidence Ledger — hash-chained, append-only; the canonical record every decision is replayable from |
| `~/.config/nanoclaw/mount-allowlist.json` | Cage allowlist — **only** place host-folder access is granted (tamper-proof, outside repo; legacy dir name, predates the rename) |
| `groups/{name}/.hermes/` | Per-group persistent Hermes state (survives `docker run --rm`) |
| `aurum_organs_spec.md` | Organs architecture (PK/EL/AG/AA/HVP/EG/OI/LS + invariants + A3 tests) |
| `aurum_build_sprints.md` | Sprint roadmap derived from the spec's build order + confidence tiers |

## Secrets / OneCLI

API keys, OAuth tokens, and auth credentials are managed by the OneCLI gateway, which
injects secrets into containers at request time — no keys/tokens are passed to containers
directly. Verify via `docker inspect` that no secrets ever appear in container env.
Run `onecli --help`.

## Dynamic MCP registration & secret capture (design notes — not yet built)

> **STALE PLUMBING — re-ground before implementing.** The mechanics below (`McpClient`,
> `container/agent-runner/src/mcp.ts`, `agent.ts`, `src/channels/telegram.ts`) were written
> against the **retired Node runner**, which no longer exists. The *design intent* still holds
> (register-not-install, remote-first, secret-by-reference, conditional tool injection), but
> the implementation must be re-grounded on **Hermes' native MCP layer** + Aurum's gateway —
> not the deleted Node files named below. Treat file references here as historical.

Goal: let Dan connect an arbitrary new MCP (any hosted/remote API — a CRM, a SaaS tool, a
home-automation bridge, whatever) from a Telegram chat without breaking the no-secret-in-cage
posture. (Dataverse/D365 appears below only as a concrete example because it's a familiar
OAuth+OpenAPI shape — it is NOT the target use case; the mechanism is provider-agnostic.)
Decisions reached, to implement in Sprint 0/5/6:

**Why "self-install for next run" doesn't work as stated.**
- The container MCP layer is single-server and hardwired: `McpClient` (`container/agent-runner/src/mcp.ts`)
  takes ONE `serverPath` and spawns one stdio server. There is no MCP config file the agent reads
  at startup — nowhere to "add a server" without a code change.
- `agent.ts` builds `allTools` ONCE before the loop (line ~184) and reuses it every iteration. A tool
  "installed" mid-run never enters `allTools`, so the model can't call it that turn. New tools appear on
  the **NEXT message**, not the current one (unless we rebuild `allTools` after a `register_mcp` call).
- `docker run --rm` destroys any runtime `npm install` on exit → reinstall-every-message anti-pattern
  (the same trap as running headroom inside the cage). So the primitive is **register**, not **install**.

**The design (next-message, config-driven, remote-first):**
1. Generalize `McpClient` from one `serverPath` to a **list** of servers, read at startup from a
   per-group `mcp-servers.json` on the host mount (`groups/{name}/.hermes/`) so it survives `--rm`.
2. Add a gated `register_mcp` builtin tool that appends an entry to that config.
3. **Prefer remote/HTTP MCP** (Dataverse ships a hosted MCP = URL + OAuth) — nothing to install,
   sidesteps `--rm` entirely. Local-stdio MCPs must be baked into the image, then merely *registered*.
4. Idle cost is real: every registered tool's full schema is sent on **every** LLM call (sequential-thinking
   was ~500–700 tokens of dead weight). So registration is **conditional** — only inject a server's tools
   for groups that enabled it. Dead tools are a per-message context tax, not free.

**Secret handling — split by sensitivity (chat apps are a terrible secret channel):**
- URL / App (client) ID / Tenant ID are **identifiers, not secrets** → fine to type in Telegram; they
  flow through as a normal agent message and land in `mcp-servers.json` as plain config.
- The **client secret** must NOT be typed into chat normally. `mcp-servers.json` stores a *reference name*
  (`secretRef`), never the value; OneCLI swaps ref→secret at request time. `docker inspect` stays clean.
- **Primary path:** vault the secret on the host, out-of-band — `onecli vault set <name>`
  (Tailscale to the Windows box for remote). Never touches Telegram, the log, or `conversations.db`.
- **Fallback `/setsecret` (host-side, explicit, single-purpose):** intercepted in `src/channels/telegram.ts`
  the SAME way `chatid`/`ping` are (lines 64–90) — handled in the **Windows host Node process, before any
  container is spawned**, returning before `onMessage()`. It writes straight to OneCLI (local
  process-to-process, no network, no container), `deleteMessage`s the value from the chat, and never
  persists it. The container only ever sees the reference name. Caveat: the value still transits
  Telegram's servers in flight (unavoidable for any chat input) — `deleteMessage` scrubs the stored copy;
  rotate-after is the mitigation for the transit copy. Verify Bot API `deleteMessage` works in private
  chats within its 48h window before relying on it.

This intersects the organs spec: it is the substrate AA (API Archaeologist) promotes synthesized MCP
servers into, and it must honor PK's untrusted-content boundary (a registered server's fetched content is
DATA, never instructions) and the per-request secret-gateway invariant.

## Development

Run commands directly — don't tell the user to run them.

```bash
# Organ test suite (from aurum/)
cd aurum && python -m pytest -q

# Rebuild the Aurum brain image (context = repo root)
docker build -f container/aurum/Dockerfile -t aurum-agent:latest .
```

## Build-cache gotcha

The container buildkit caches the build context aggressively. `--no-cache` alone does NOT
invalidate COPY steps — the builder volume keeps stale files. For a clean rebuild, prune the
builder, then re-run `docker build -f container/aurum/Dockerfile -t aurum-agent:latest .`.
