```
    _   _   _ ____  _   _ __  __      _    ____ _____ _   _ _____
   / \ | | | |  _ \| | | |  \/  |    / \  / ___| ____| \ | |_   _|
  / _ \| | | | |_) | | | | |\/| |   / _ \| |  _|  _| |  \| | | |
 / ___ \ |_| |  _ <| |_| | |  | |  / ___ \ |_| | |___| |\  | | |
/_/   \_\___/|_| \_\\___/|_|  |_| /_/   \_\____|_____|_| \_| |_|
```

# Aurum

**A governance-first personal agent for long-lived autonomous work.** Aurum is a
[Hermes](https://github.com/NousResearch/hermes-agent) agent brain running inside an
ephemeral, network-restricted Docker cage. It can learn skills, diagnose its own failures,
and author its own tools — and every one of those self-modifying paths is gated, sandboxed,
fail-closed, and tested. Capability and governance grow on the same rails.

> **Controlled autonomy, not maximal autonomy.** The market optimises capability; the
> documented production failures are elsewhere — governance, drift, recovery, observability.
> Aurum treats governance as the operating system, not a layer sprinkled on top.

Current build status — commit by commit — is tracked in **[`progress.md`](./progress.md)**.

---

## What it is

```
Channels (Telegram / Gmail / …) ── thin secure launcher (host)
      │  message → group resolved → mount-allowlist consulted (tamper-proof)
      ▼
  docker run --rm   (ephemeral, host UID, least-privilege mounts)
      │  RO project · RW per-group state · allowlisted host dirs only
      ▼
  [cage]  Hermes brain  — skills · tools · MCP · cron · subagents
      │  + Aurum's governance organs (see the spine below)
      ▼
  secrets injected per-request over the gateway — the container never holds keys
```

- The **hard containment boundary** is the cage: a `--rm` container holding no long-lived
  secrets, with controlled egress and an allowlist as the *only* place host-folder access is
  granted. Everything Aurum does to itself happens inside it.
- Per-group state (memory, learned skills, postmortems, compression state) persists on the
  host across the ephemeral container so nothing is lost when it exits.

---

## The thesis

An agent that invents its own tools, checks its homework across independent models, reroutes
before its own errors compound, judges *outcomes* not just *completion*, governs its own
authority in real time, and rewrites its own operating identity **under human approval** —
getting more capable and more constrained at once.

Every promote / persist / policy-change / spec-revision path terminates at a **human gate**.
Generated and learned artifacts run caged until reviewed; secrets are redacted from every
persisted record; ingested external content is data, never instructions. Absence shrinks the
agent, never grows it.

---

## Status: built vs designed

The architecture is organised into tiers. **Tier 0 (the spine) is built and tested**; the
rest is the active roadmap.

### ✅ Built — Tier 0 spine

| Organ | What it does | Guardrail |
|-------|--------------|-----------|
| **Constitution** (`SOUL.md`) | Identity + always-on directives, seeded per-group | Lean, references guardrails rather than inlining them |
| **Policy Kernel** | Skill-content security scanning | Supply-chain `§3a` defenses: `.skillignore` can't hide test/exec code; AST flags import-time payloads; secure-by-default in the cage |
| **Skill-CI / Regression Guard** | Validate-before-promote | Fail-closed: never runs a skill the scanner flagged; tests run in a hardened, secret-scrubbed, network-dropped sandbox |
| **Black Box** | Failure → structured postmortem → skill | Redacted on-disk corpus; feeds the learning loop; never hot-path |
| **Toolsmith** | The agent authors real tools | Propose → scan → sandbox-test → **staged for human review; never auto-activated** |
| **Memory + compression** | On by default, per-group | Native engine (no flaky external dependency); persists across `--rm` |

### 🧭 Designed — the roadmap

The governance-first organs that make the headline claim, grouped by tier:

- **Tier 0.5 — durability & scaling:** Evidence Ledger (hash-chained, replayable decision
  provenance), Reproducibility Runner, Memory Garbage Collector, Knowledge Validity Engine
  (stored knowledge expires), Goal Registry, Preference Model, Tool Capability Manager.
- **Tier 1 — novel core:** API Archaeologist (gap → discover API → synthesise tool, extension-
  first), Living Specification (gated self-rewrite of the constitution), Heterogeneous Verifier
  Panel (cross-model checking), Epistemic Governor (reroute before errors compound), Causal
  Simulator (counterfactuals over its own state), Authority Governor (live trust dial),
  Outcome Interpreter (completion ≠ satisfaction).
- **Tier 2–4:** cross-domain extensions, known support patterns (Trust Ladder, Circuit
  Breaker, Shadow Mode, Cost Governor, Sensorium), observability (drift / poisoning /
  concentration monitors), and deferred multi-agent orchestration.

Each carries a build-confidence label (HIGH / PARTIAL / HARDEST) — an honest expectation,
not a promise that all organs are equally achievable.

---

## Cross-cutting invariants

These hold across every organ:

- Every side-effecting call passes a policy check first; action *chains* are checked for
  aggregate privilege, not just per step.
- Ingested content is untrusted data, never instructions — even when it claims operator authority.
- One redaction policy for all persisted state. Archive ≠ delete; everything recoverable.
- Capability promotion requires **governance parity**: no matching gate + regression check +
  audit record → no promotion. Testable, not aspirational.
- Authority is computed live, not a fixed tier; it rises slowly and falls fast, and never to zero.
- Metrics that drive self-modification are periodically re-grounded against signal from
  *outside* the agent's own loop (Goodhart guard).

---

## Built on Hermes

Aurum is a fork of **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** by
[Nous Research](https://nousresearch.com) (MIT). Hermes provides the agent runtime — the
skills loop, Curator, tools, MCP, cron, subagents, multi-provider model routing, and the
terminal/gateway surfaces. Aurum adds the cage, the governance spine, and the organ
architecture on top. Upstream remains tracked as the `upstream` remote for merges.

## Repository map

| Path | Purpose |
|------|---------|
| [`progress.md`](./progress.md) | Current build status, commit-by-commit |
| `container/aurum/` | Cage entrypoint, Dockerfile, and the seeded `SOUL.md` |
| `tools/skills_guard.py`, `tools/skill_ci.py`, `tools/toolsmith.py` | Policy Kernel · Skill-CI · Toolsmith |
| `agent/black_box.py` | Black Box postmortem store |
| `tests/test_*` | Tests for every built organ |

---

_Governance over time is the contribution; everything else is evidence for it._
