# Aurum — Build Progress

> **Aurum** = a forked **Hermes** agent brain running inside **nanoclaw's** ephemeral
> Docker security cage. The agent can learn skills, diagnose its own failures, and author
> tools — and every self-modifying path is gated, sandboxed, fail-closed, and tested.
> Capability and governance grow on the same rails.

_Last updated: 2026-06-04_

---

## Status at a glance

Everything **code-completable** is done to production grade: **6 commits** on `aurum` `main`
(`c143e15`→`f3b2e43`, unpushed), **61 new tests green**, each organ verified inside the
actual built `aurum-agent:latest` image. Only **live-test / OAuth** work remains.

---

## The governed self-ability spine (complete)

| # | Organ | Capability it adds | The guardrail that makes it safe | Commit | Tests |
|---|---|---|---|---|---|
| 1 | **SOUL.md constitution** | Stable identity + 6 always-on directives, seeded per-group | Lean, name-agnostic (`{{ASSISTANT_NAME}}`), references guardrails rather than inlining them (no prompt bloat) | `c143e15` | live |
| 2 | **Policy Kernel (§3a supply-chain)** | — | `.skillignore` can't hide test/exec code; stdlib-`ast` flags import-time payloads as CRITICAL; secure-by-default in the cage | `515c7dc` | 19 |
| 3 | **Skill-CI / Regression Guard** | Validates skills before they go live | Fail-closed: never runs a skill the scanner flagged; tests run in a hardened, secret-scrubbed, network-dropped sandbox | `e8e30c6` | 11 |
| 4 | **Black Box** | Turns its own failures into durable fixes | Redacted postmortems; feeds the skill-review fork; transient infra noise filtered out | `9ea1340` | 16 |
| 5 | **Toolsmith** | The agent can **author new tools** | Propose → scan → sandbox-test → **staged for human review; never auto-activates** | `ceb124a` | 10 |

### Phase 5 — memory + compression by default
Reuse-first outcome (`f3b2e43`, 5 guard tests): Hermes' **native** memory and context
compressor are already on by default and rooted at `HERMES_HOME` (mounted per-group), so
per-group persistence across the `--rm` container is already handled. Deliberately rejected:
a redundant knowledge-graph memory MCP, and making the optional headroom proxy a *hard*
dependency (the "headroom hung 19 min" failure mode is absent by construction).

---

## Files added / changed this phase

| File | Change |
|------|--------|
| `container/aurum/SOUL.md` | **new** — Aurum constitution |
| `container/aurum/entrypoint.py` | seeds SOUL.md per-group; sets `AURUM_GUARD_SKILLS` / `AURUM_SKILL_CI` / `AURUM_TOOLSMITH=1` |
| `container/aurum/Dockerfile` | COPY SOUL.md; add `pytest` + `util-linux` for Skill-CI |
| `.dockerignore` | `!container/aurum/SOUL.md` negation |
| `tools/skills_guard.py` | §3a: `_is_force_scanned`, `ast` import-time detector, wider scannable suffixes |
| `tools/skill_manager_tool.py` | secure-by-default gate; chains Skill-CI; imports Toolsmith to register it |
| `tools/skill_ci.py` | **new** — validate-before-promote + shared `run_sandboxed_tests` |
| `agent/black_box.py` | **new** — postmortem store + redaction + review addendum |
| `agent/background_review.py` | skill-review fork consumes recent postmortems |
| `tools/toolsmith.py` | **new** — governed tool authoring + cage-gated `propose_tool` tool |
| `tests/test_skills_guard_supplychain.py`, `test_skill_ci.py`, `test_black_box.py`, `test_toolsmith.py`, `test_aurum_phase5_defaults.py` | **new** — 61 tests |

---

## Done earlier (Phases 0–4)

- **Phase 0** — Hermes runs headless `cli.py -q` via OpenRouter.
- **Phase 1** — `aurum-agent:latest` slim image; stdin `ContainerInput` → `hermes -q` →
  sentinel `ContainerOutput`; persists to `/workspace/group/.hermes`.
- **Phase 2** — nanoclaw `CONTAINER_IMAGE` reads `.env`.
- **Phase 3 / 4** — 24KR release-pipeline + Gmail/2FA skills written; `PIPELINE_API_KEY`
  passthrough wired (verification gated on live creds).

---

## Remaining — live-test / OAuth only

These cannot be completed without operator credentials, by their nature:

- [ ] **Phase 6 — social media + label skills** (post-v1): needs platform **OAuth** + live
      posting. Not built — shipping unverified social skills would violate the prod-grade bar.
- [ ] **24KR pipeline live-test**: add pipeline to `mount-allowlist.json` (RW) +
      `PIPELINE_API_KEY` in nanoclaw `.env` + run pipeline API on `:9111`.
- [ ] **Gmail OAuth** (Phase 4 live capture).
- [ ] **Cut over**: set nanoclaw `CONTAINER_IMAGE=aurum-agent:latest` + restart nanoclaw.
- [ ] **Live multi-turn memory recall** check in the running cage.
- [ ] _(Deferred infra)_ route agent LLM via the headroom `:8788` proxy (`AURUM_PROXY=1`) as
      a compression backstop.

---

## Notes

- Git on the `aurum` repo uses a **local** identity `StylezDevops / me0wc0w73@gmail.com`.
  Nothing is pushed.
- Dev `.venv` lacks the dev test deps by default. Install pinned and run with the
  Windows-compatible timeout method:
  ```
  .venv/Scripts/python.exe -m pip install pytest==9.0.2 pytest-asyncio==1.3.0 pytest-timeout==2.4.0
  .venv/Scripts/python.exe -m pytest <file> -o addopts="--timeout=30 --timeout-method=thread"
  ```
  (`pytest-timeout`'s default `signal` method needs `SIGALRM`, which Windows lacks.)
- ~11 pre-existing test failures on Windows (hash symmetry, binary assets, llm guards) are
  **not** regressions — confirmed by re-running them against the committed baseline.
- Clean image rebuild: prune the buildkit builder first, then
  `docker build -f container/aurum/Dockerfile -t aurum-agent:latest .` (context = repo root).
