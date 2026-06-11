# Containment invariant — migration design note

> **CONTAINMENT INVARIANT.** No execution container may have filesystem visibility into Aurum
> runtime code, governance state, policy definitions, authority records, identity keys, or ledger
> storage. **Violation = containment failure.**

This is the structural upgrade to Aurum's security property: from *the brain runs governance
honestly* to *governance runs where the brain cannot reach it*. It is the CaMeL move applied to the
**substrate** — the governed cannot read or tamper the thing that governs it.

## Current state (v1) — the invariant is violated, by design-as-bootstrap

Verified in code:

- **Governance state is on a container-visible mount.** `paths.state_root()` → `HERMES_HOME`
  (`paths.py`), and the cage sets `HERMES_HOME = /workspace/group/.hermes`
  (`container/aurum/entrypoint.py`) — the **RW per-group mount**. So `el.db`, `ca.db`, authority
  records (TRUST_CHANGE), the decision/snapshot ledger, the constitution + operator **public** keys
  live at `/workspace/group/.hermes/governance/`, readable AND writable by the brain.
- **Governance code ships in the image** (`container/aurum/Dockerfile` `COPY . .`; the
  `aurum-governance` plugin imports the `aurum.aurum.*` spine).
- **The kernel runs *inside* the container** — the `aurum-governance` plugin's `pre_tool_call`
  hook instantiates `GovernanceKernel` in-process.
- **Already satisfied:** identity *private* keys are out-of-band (the cage holds only public keys).
  The migration extends that asymmetry to everything else.

This was the deliberate "first governed turn" bootstrap — stand the mechanism up in-cage, prove it,
then move it out. The invariant is the target, not a claim about today.

## Why it matters

Today a fully prompt-injected brain has a filesystem path to its own ledger, authority records, and
policy. The hash chain + append-only triggers make tampering *evident* and forged appends are
distrusted-as-proxy — but "evident and distrusted" is weaker than "unreachable." Under the
invariant, two of the five must-never classes become **structurally impossible** rather than
detected-and-floored: `ledger_tamper_or_provenance_forge` and `secret_capability_misdirection` (the
bound keys) cannot be reached if they are not in the brain's filesystem. It is also the structural
answer to ultrareview finding H4 ("governance is an opt-in overlay") — containment makes governance
not merely engaged but **unreachable** by the governed.

## What this PR delivers (the enforcement point + tripwire)

`MountJail` (`aurum/aurum/cage/mount_jail.py`) gains the invariant as requirement **R7**:

- `MountJail(allowlist, governance_roots=...)` + `jail_from_file(..., governance_roots=...)`.
- An **absolute exclusion** that OVERRIDES the allowlist: `validate_extra` and `build_mounts` raise
  `ContainmentError` (a `MountDenied` subclass — still fails closed) if any mount — structural OR
  extra — overlaps a governance root **in either direction** (mounting it, a parent that contains
  it, or a child within it).
- **Dormant until `governance_roots` is supplied** (empty = back-compat, so today's cage is
  unaffected). The broker enables it *after* the relocation below — at which point a cage that would
  expose governance can no longer start.
- Red→green tests (`tests/test_cage_mount_jail.py::test_R7_*`): a governance mount is denied even
  when allowlisted; a parent-of and child-of are denied; `build_mounts` fails closed when the group
  mount exposes governance (the v1 violation, made loud); a clean post-migration layout is contained;
  the dormant default is non-breaking.

This codifies the boundary as a tripwire: any future config or persistence drift that would expose
governance trips a test, before the full migration.

## The migration (v2 architecture sprint — NOT in this PR)

To make the invariant *hold* (not just be enforceable), governance must leave the brain's isolation
domain. Steps, each its own PR:

1. **Relocate governance state off the container mount.** Introduce a dedicated governance root
   (e.g. `AURUM_GOVERNANCE_ROOT`, defaulting to `state_root()` for back-compat) and point it at a
   **host-only** path outside every mount — alongside `~/.config/aurum/mount-allowlist.json`, which
   is already host-only and tamper-proof. The per-group `/workspace/group/.hermes/` keeps *brain*
   state (session memory, skills) — not governance state.
2. **Move the kernel host-side.** Run `GovernanceKernel` in the host gateway (or a sidecar the brain
   container has no mount into — either satisfies "no filesystem visibility"). This is what the
   architecture diagram already intends ("Aurum gateway + cage (host)").
3. **The container gets a thin govern-RPC client, not the kernel.** The `pre_tool_call` hook stops
   importing the spine and instead calls the host governance service (action → `{allow, reason}`),
   the same pattern already in use for the headroom proxy (`host.docker.internal:8788`) and the
   OneCLI secret gateway. The brain receives a decision and nothing else.
4. **Drop the spine from the image** (`Dockerfile` no longer ships `aurum/aurum/`; only the RPC
   stub). Now "runtime code" is also out of the container's filesystem.
5. **Enable R7 in the broker:** `jail_from_file(governance_roots=[<governance root>])`, so
   `build_mounts` fails closed if any mount would expose it.

### Bonus the migration pays for
The host kernel is **long-lived**, which cleanly resolves H2: `new_turn()` (taint reset) is called
at the RPC turn boundary; the maintenance loop and `_action_log` bounding become natural host-side
concerns instead of awkward per-container ones.

### Tradeoffs
- **Hot-path RPC latency** — govern runs on every tool call; an in-process call becomes IPC. The
  headroom-proxy precedent shows it is acceptable, but it is real.
- **Complexity** — a host govern service + a stable RPC protocol + the persistence split. A genuine
  architecture change, sequenced after the in-cage mechanism is proven (the same v1→v2 honesty as
  the operator-verdict and calibration work).

Until the migration lands, the honest statement is: **the invariant is enforceable and tested at the
mount boundary, and dormant in the live cage, where governance still runs in-container — the known
gap this note exists to close.**
