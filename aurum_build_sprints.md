# AURUM — BUILD SPRINT ROADMAP

Derived from `aurum_organs_spec.md` (organs, interfaces, gates, A3 assertions) and the
nanoclaw→Hermes cutover plan (`~/.claude/plans/groovy-snuggling-lecun.md`). This document
turns the spec's dependency-correct 23-step Build Order into executable sprints, respecting
its three confidence tiers (HIGH / PARTIAL / HARDEST) and wiring in the dynamic-MCP /
`/setsecret` substrate work (see CLAUDE.md → "Dynamic MCP registration & secret capture").

## Reading this plan

- Each sprint: **goal · builds · depends · exit gate (with A3 test ids) · confidence**.
- "A3-NNN" = the `AURUM_ERR_NNN` assertions in the spec appendix. Each lands in the harness
  as its organ ships; the full set is the **safety-parity gate before the execution loop goes live**.
- Confidence tiers (spec §BUILD-CONFIDENCE): **HIGH** = build as written; **PARTIAL** =
  useful but likely narrower; **HARDEST** = expect v1 small / matures on EL evidence.
- Invariant, every sprint: every side effect calls `PK.check()` first; every consequential
  decision appends to EL ("if it's not in the ledger, it didn't happen"); every
  capability-adding path terminates at HUMAN_GATE.

---

## ⚑ KERNEL HARDENING MANDATE (PK + EL) — read before anything else

PK and EL are the base the whole architecture rests on. Per the spec they are *the kernel*
(PK = may I act?, EL = on what recorded evidence?). **If these are slow or error-prone, every
higher organ silently degrades** — AG can't compute authority, LS can't attribute, RR can't
replay, OI can't trend, KVE can't re-score. They get **dedicated sprints (1 and 2)** and are
held to non-functional requirements as hard exit gates, not best-effort:

- **PK is on the hot path of every side effect.** `PK.check` latency must sit well under the
  decision-loop budget at realistic ruleset size. Benchmark it; index rule lookup; no linear
  scans over rules per action. Correctness: chain-aggregate, untrusted-boundary, and
  data-flow taint refusal must be airtight, not approximate.
- **EL is the one component whose downtime halts consequential action.** Append must be
  durable *before* the side effect commits (write-then-act); if append can't complete, the
  action **cannot** be made (fail-safe). Hot read paths (recent-by-capability_class, lineage-
  by-object_id) must be indexed and fast enough to read inline per action. Hash-chain
  integrity must be verifiable and tamper-evident.

No higher sprint starts until Sprints 1–2 pass their benchmark + correctness gates. This is
deliberate: the spec's likely failure mode is implementation complexity, and a shaky kernel
multiplies it everywhere.

---

## Sprint 0 — Substrate & cutover (prerequisite, not an organ)

**Goal:** the platform the organs run on — Hermes brain in the cage, real isolation,
per-request secrets, the dynamic-MCP plumbing, and a minimal HUMAN_GATE.

**Builds**
- Hermes brain cut into the cage (groovy-snuggling-lecun.md Phase 2): organs are Python
  modules in the forked brain, running in-container, persisting to `groups/{name}/.hermes/`.
- **Cage hardening:** rootless Docker now; document the gVisor/Firecracker path for Tier 1+
  (containment scales with autonomy — spec §DEPLOYMENT). Least-privilege mounts via the
  existing `mount-security.ts` crown jewel (keep byte-for-byte).
- **Per-request secret gateway (OneCLI):** no durable secret resident in the cage; prove via
  `docker inspect`. This is the runtime rule HVP's `api_key_ref` and AA both assume.
- **`/setsecret` host-side handler** in `src/channels/telegram.ts` (mirror the `chatid`/`ping`
  interception at lines 64–90): capture → `onecli vault set` → `deleteMessage` → never reach
  `onMessage`/container. Secret stored by reference only.
- **Dynamic-MCP substrate:** generalize `McpClient` (`container/agent-runner/src/mcp.ts`)
  from one `serverPath` to a server **list** read from per-group `mcp-servers.json`; add the
  gated `register_mcp` builtin (reference-only secrets). Remote/HTTP MCP preferred.
- **Signed persisted state** scaffolding: sign-on-write / verify-on-reload for per-group state
  (BB corpus, memory, constitution) so reloaded state is trusted only by signature — closes
  the self-poisoning-via-own-state vector before EL/BB depend on it.
- **HUMAN_GATE v0:** Class-C (individual review) + EL-logged + **fail-closed TTL expiry**.
  (Class A/B batching, delegation chain, degraded mode enriched in Sprint 10.)

**Depends:** nanoclaw as-is; OneCLI; the cutover plan.
**Exit gate:** `docker inspect` shows zero resident secrets; `/setsecret` round-trips to vault
and scrubs the chat message; a remote MCP registered by reference goes live on the next
message; signed state fails closed on tamper; a Class-C gate expires closed on no response.
**Confidence:** HIGH (engineering, not research).

---

## Sprint 1 — PK · Policy Kernel (THE BASE — hardened) 🔒

**Goal:** the gate every side effect passes, fast and correct. No half measures.

**Builds**
- Versioned, machine-readable ruleset; `PK.check(action)` hot-path (reuse-first, no-secrets,
  least-privilege) — **indexed, benchmarked, well under loop budget**.
- `PK.check_chain(actions, ctx)` — aggregate-privilege guard. v1 uses a **self-contained
  data-flow taint model** (sensitive SOURCE → exfil/mutation SINK); upgraded to CS-projected
  taint in Sprint 5 (so PK is not blocked on CS).
- **Untrusted-content boundary:** `PK.tag_trust(payload, source)`, trust propagation, and
  `PK.trace_justification(action)` — deny if any source is untrusted. Only the operator's
  direct channel carries `trust: operator`; embedded "ignore previous instructions" never binds.
- **Refusal persistence** via **data-flow taint signature** (source→sink, NOT graph topology
  — padding-resistant), **precondition-scoped** (auto-expires when authority/tool/policy
  genuinely change; rewording under same preconditions does not reverse it).
- **`PK.redact(payload)` single chokepoint** + `PK.redaction_version` — the one redaction
  policy every persister (EL, BB, LS, MGC) calls on write. No per-organ redaction.
- `PK.test(ruleset)`, `PK.version`, `PK.denied_intents()`.

**Depends:** Sprint 0. (CS-taint upgrade deferred to Sprint 5.)
**Exit gate:** **A3-007** (semantic privilege escalation denied though each step passes),
**A3-008** (injection boundary — untrusted instruction can't act/raise authority/satisfy a
gate), **A3-009 partial** (refusal persistence; full padding-resistance completed in Sprint 5).
**Benchmark:** `PK.check` p99 latency under loop budget at target ruleset size; redaction is
single-source. **Confidence:** HIGH — but treat as load-bearing; over-test it.

---

## Sprint 2 — EL · Evidence Ledger (THE BASE — critical infrastructure) 🔒

**Goal:** the immutable, hash-chained, fail-safe causal spine nearly everything writes to.

**Builds**
- Append-only **hash-chained** store (sqlite on host mount) using the `ELEvent` schema and
  `calculate_block_hash` from the spec appendix (A2). `prev_hash`→`hash` linkage enforced.
- **Hard SLOs:** durable append **before** the recorded action commits (write-then-act);
  **fail-safe** — if `EL.append` can't complete, the action is blocked, never executed
  unlogged. **Indexed hot reads:** recent events by `capability_class`, lineage by `object_id`.
- `EL.append / query / lineage / verify_chain / health`.
- **Evidence quality:** every event carries `evidence_confidence` + `evidence_source`;
  document the downstream weighting contract (OI/TL/LS weight by it, never full-weight raw).
- **Conflict-driver logging from day one (governance-deadlock prerequisite).** When metric
  conflicts arbitrate (safety wins), EL must record *which organs conflicted, what the risk
  signals read, and whether new evidence had arrived*. Per the spec's GOVERNANCE DEADLOCK
  risk, wise-caution and deadlock are externally identical and distinguishable only
  *historically* — if EL doesn't capture conflict drivers from the first task, the distinction
  is **unrecoverable later**. This is day-one-or-never; it's cheap now and impossible to backfill.
- Redaction on write via `PK.redact`.
- **Lossless structural compression** interface (snapshot + raw deltas) reserved for MGC —
  never semantic summary (LS rolling-window math depends on exactness).

**Depends:** PK (redaction, fail-safe semantics).
**Exit gate:** **A3-001** (retroactive edit → `verify_chain` raises → `CB.trip` lockout),
**A3-002** (lossless snapshot round-trips identical delta count), **A3-011** (append-fails →
consequential action blocked). **Benchmark:** append latency and inline read latency both
under the per-action loop budget; chain verify over a large ledger is tractable.
**Deferred (measurement-triggered — do NOT pre-build):** EL hot cache (only when AG/EG reads
demonstrably eat the loop budget) and read replica (only under multi-agent contention).
**Confidence:** HIGH — but it's infrastructure; the SLOs are the deliverable, not just the API.

---

## Sprint 3 — Observability baseline (pulled forward) + RR + CG

**Goal:** start recording the drift/poisoning baseline from EL's first day, plus replay and
the cost primitive. Spec pulls IDM/MPD forward precisely because *you can't tune a feedback
loop you haven't been measuring* — AG.kinetics and EG weights need this baseline later.

**Builds**
- **IDM** (identity-drift) + **MPD** (memory-poisoning) — passive, read-only views over EL.
  Recording only; never block. (CC added later once there's a tool population.)
- **Governance-event-rate instrumentation (day one).** An EL query: gates raised, conflicts,
  deadlock candidates, proposals, invalidations *per day*. The delegated gate classes solve
  "human ABSENT"; this measures "human OVERWHELMED" — a distinct throughput axis. The threshold
  at which even batched review overloads the owner must be **discovered empirically**, not
  guessed, so the rate has to be logged from the first task to find it before it bites.
- **RR** (Reproducibility Runner) — `replay/diff/context`; decision replay guaranteed,
  environment replay best-effort and always labelled `environment_fidelity`. Replay can be
  stubbed now and completed as TS/PK/LS version history matures.
- **CG** (Cost Governor) — `route/budget/spend`; the budget EG branching and HVP verification
  later draw from.

**Depends:** EL.
**Exit gate:** IDM/MPD writing baseline telemetry derived from EL; `RR.replay` reconstructs a
past event's decision context and labels fidelity; `CG.route` picks model by step cost.
**Confidence:** HIGH (IDM/MPD/CG); RR HIGH for decision-replay, honest about environment drift.

---

## Sprint 4 — CS-minimal + GR (goal graph + leases)

**Goal:** the dependency-graph + lease machinery, and the goal registry that sits on it.

**Builds**
- **CS-minimal:** graph machinery to host nodes + **leases** (`CSNode` schema from A2;
  `is_leased`, `lease/heartbeat/release`). **TTL + heartbeat** crash-safety (live holder
  renews; crashed holder's lease lapses so MGC can reclaim) — required even for single-agent
  v1, and pre-positions Tier-4 AO.
- **GR:** goal registry with **Goal Health from the start** (health gates `active()`); `add/
  get/active/expire/depends/health/touch`. AA + TL later consult `GR.active()` before acting.

**Depends:** CS-minimal (PK, Curator, TS registry); GR (CS, EL).
**Exit gate:** **A3-003 partial** (leased artifact reports `is_leased=true`; full MGC-skip
asserted in Sprint 12 once MGC exists — assert here against a scan stub); lease lapses on
missed heartbeat; `GR.active()` filters by health and an expired/decayed goal drops out.
**Confidence:** HIGH.

---

## Sprint 5 — CS-full core + TCM + PK taint upgrade

**Goal:** counterfactual blast-radius, the tool taxonomy AA needs, and PK's taint completion.

**Builds**
- **CS-full (deterministic core only):** `whatif` blast-radius / conflicts / orphaned over
  skills+tools+policies+goals. **`CS.project` stays DEFERRED/experimental** (returns blast-
  radius + conflicts only; no probabilistic outcome prediction until EL history can validate it).
- **PK taint upgrade:** `PK.check_chain` now projects taint paths through CS; **complete
  A3-009** padding-resistance (source→sink invariant under inserted benign steps / step-splits).
- **TCM:** `check_overlap / recommend_domain / taxonomy / select / retirement_candidates`.
  AA calls it pre-synthesis (`should_bundle`) and pre-promote (`check_overlap`).
- Finalize `register_mcp` → TS lifecycle wiring (the Sprint-0 multi-server substrate becomes
  AA's promotion target).

**Depends:** CS (PK, Curator, TS, GR); TCM (TS, AA-interface, MGC-later, EL).
**Exit gate:** `CS.whatif(deprecate X)` returns exact referencing set + conflicts with no
execution; **A3-009 full** (padding-resistant refusal); `TCM.check_overlap` returns *reuse*
for a gap an existing tool already covers. **Confidence:** HIGH (graph core); `project` deferred.

---

## Sprint 6 — AA · API Archaeologist (capability — extension-first)

**Goal:** the closed gap→tool loop, defaulting to *extending* existing tools. This is where
Dataverse/D365 becomes real, promoting into the Sprint-0/5 MCP substrate.

**Builds** — the bounded cascade (spec A1):
`detect_gap → should_bundle (TCM extend-vs-create) → discover (discovery_budget /
max_depth / max_candidate_specs) → synthesize (FastMCP / openapi-mcp-generator subprocess;
**minimal coherent surface**, **minimal path depth**, coherence maps 1:1 to a TCM category)
→ cage_test (**on-demand expansion** on missing-relation errors, **expansion-burn guard** →
failed-constraint signatures to BB, abort-to-human on repeated identical failures) →
TCM.check_overlap → TS.promote [HUMAN_GATE]`.

**Depends:** TS, PK, TCM, EL, GR.
**Exit gate:** **A3-006** (`CB.freeze_growth` aborts `synthesize`/`promote`, existing tools
keep running); spec AA accept (a)–(d): caged server for "query D365 contacts" (read+update
coherent surface, not every endpoint) with zero human steps before the gate; clean budget-
abort on no findable spec; in-domain third gap → `should_bundle` extends (no new server);
duplicate caught by TCM pre-promote. **Confidence:** PARTIAL — likely lands more as an
extension engine than a creation engine (~80% evolution, per spec). That is success, not shortfall.

---

## Sprint 7 — HVP · Heterogeneous Verifier Panel (capability — verification)

**Goal:** check the agent's homework across *independent families* on any OpenAI-compatible
endpoint, with cost discipline.

**Builds**
- Endpoint-abstraction roster (`{id, base_url, api_key_ref, model, family, provider,
  trust_tier, cost_class, sees_sensitive}`); `api_key_ref` via key-store/OneCLI, never inline.
- `route/call/verify(aspects→binary)/aggregate(unanimous|majority)`.
- **Independence by `family`, not `model`**; **measured per-domain correlation** from EL
  (agreement-on-errors collapses colluding families to one); **no-independent-pair degradation**
  (label `correlated-best-effort`, escalate high-stakes to human).
- **Verifier ROI** instrumented day one (a family that rarely changes a verdict gets down-
  weighted → keeps HVP from becoming a flat tax).
- `sees_sensitive` filtering driven by PK-tagged sensitivity.

**Depends:** PK, EL; key-store; ≥1 reachable endpoint.
**Exit gate:** **A3-005** (`[gpt-5, gpt-5-mini]` = one family → `min_families` violation);
spec HVP accept (a)–(f): one-entry endpoint add (no code change), ≥2 families on high-stakes,
sensitive payload only to `sees_sensitive:true`, trivial check → lowest cost_class, all
votes+routing+cost logged to EL. **Confidence:** PARTIAL — may shrink to high-stakes-only; let
ROI data decide.

---

## Sprint 8 — EG + PM + OI (epistemic control + preferences + outcome truth)

**Goal:** reroute before errors compound; hold durable preferences separately; judge outcomes
vs mere completion.

**Builds**
- **EG:** composite `U` from the **five named observable components** (never a self-rated
  confidence), **normalized** (each [0,1], Σw=1), **EWMA smoothing** of the sparse historical
  components (low-volume blips don't trigger constant branching); `freeze_and_branch` (distinct
  from CB); `calibrate()` against EL failure history with **held-out validation** (reject
  overfit weights) [HUMAN_GATE on weight change].
- **PM:** durable preference store (build before OI/LS); `get/add/applies/check`; stated
  outranks inferred; LS is forbidden from encoding preferences as constitution rules.
- **OI:** outcome-quality verdict; **tiered satisfaction oracle** (proxy = provisional, low-
  confidence, may NOT alone drive self-modification; **human sampling** = ground truth that
  audits/re-grounds the proxy — the Goodhart defense); **operational-effectiveness ratio**
  (the over-caution counterweight — safe-but-useless is a tracked failure).

**Depends:** EG (CG, HVP, BB, EL); PM (EL); OI (GR, HVP, PM, BB, EL).
**Exit gate:** EG `U` from named components only + branches on injected early error +
calibration rejects holdout-failing weights; PM redirects an LS-style preference; OI marks
"completed but not satisfied" → does **not** raise trust, writes BB learning case, and
`OI.effectiveness` drops/flags under rising overhead. **Confidence:** EG PARTIAL (branch
trigger tuning is empirical); PM/OI HIGH (OI's hard problem is the oracle, handled by tiering).

---

## Sprint 9 — AG · Authority Governor (THE KEYSTONE)

**Goal:** authority as a live computed scalar — the spec's single load-bearing idea, the fix
for Gartner's binary-governance failure.

**Builds**
- Compute authority per `capability_class` from live signals (TL tier, EG uncertainty, HVP
  pass rate, CB/freeze, OI trend) — confidence in *authority*, not in an answer.
- Bands with **hysteresis** (separate promote/demote thresholds + promotion dwell time;
  demotion instant for safety) and **recovery kinetics** (asymmetric rise-slow/fall-fast,
  a **floor** so it never death-spirals to zero, a per-window **rate limit** on gains).
- PK consults `AG.permits` on every side-effecting action; `AG.explain` shows signal
  contributions. AG computes a ceiling PK enforces — it never grants.
- **Tune kinetics against the IDM/MPD baseline** recorded since Sprint 3.
- **AG↔LS recursive-loop damper (hard invariant).** LS may **NOT** write AG's coefficients —
  AG governance is a separate path; LS influences authority only *indirectly* through observed
  outcomes. With AG kinetics + gated LS Phase-1, this makes the recursive loop (LS edits rule →
  AG reads outcomes → behavior shifts → LS proposes again) safe for v1. The months-long dynamics
  are a named "LS round 2" research item, not a v1 deliverable (see `aurum_LS_design.md` LS-FM-24).

**Depends:** TL, EG, HVP, CB, OI, PK, EL.
**Exit gate:** **A3-010** (0.81/0.79/0.81/0.79 holds a stable band — no flapping); spec AG
accept: instant demote on EG spike, PK refuses doc-edit at `readonly` band, `explain` shows
up/down contributions, high TL alone ≠ authority, floor + rate-limit hold. **Confidence:**
HIGH conceptually; kinetics tuning is the empirical part (hence the baseline dependency).

---

## Sprint 9.5 — Arbitration layer: Conflict Arbiter (CA) + Deadlock Detector (DD)

> **Read `aurum_arbitration_spec.md` (full spec, all hard decisions made).** The synchronous
> CA logic ships *with AG* in Sprint 9 (AG is the principal contraction signal; CA is meaningless
> until AG + HVP + OI can co-occur). This slot adds the **async DD** once a conflict history
> exists. The `ConflictRecord` schema + emission already shipped in Sprint 2 (day-one-or-never).

**Builds**
- **CA (deterministic, no model call)** — most-conservative-wins over soft signals, below the
  hard layer (PK-deny / CB-freeze / HUMAN_GATE never arbitrated). Writes a `ConflictRecord`
  whenever ≥2 signals held directives — *silent contraction forbidden*.
- **DD (async, reads EL conflict log)** — deadlock score `D = w1·homogeneity + w2·evidence_
  divergence + w3·effectiveness_slope`; flags `homogeneity × evidence_divergence` (stuck +
  justification-abated = deadlock; stuck + still-risky = wise caution). **Constitutional
  exclusion** (permanent Core/PK/PM denials excluded — the key false-positive guard).
- **Response protocol:** escalate (per-cluster deduplicated) + RR replay + root-cause. DD NEVER
  self-resolves, never auto-weakens arbitration; its own parameters are constitutional (Class-C
  human retune only).

**Depends:** EL (ConflictRecord, Sprint 2), AG/HVP/OI (Sprint 7–9), RR (Sprint 3), HUMAN_GATE.
**Exit gate:** **A3-013** (arbiter determinism + replay), **A3-014** (caution wins, logged),
**A3-015** (hard layer not arbitrated), **A3-016** (silent contraction forbidden), **A3-017**
(wise-caution vs deadlock discrimination), **A3-018** (constitutional exclusion), **A3-019** (DD
never self-resolves; its params un-self-tunable), **A3-020** (escalation dedup).
**Confidence:** CA **HIGH** (deterministic); DD **PARTIAL** — mechanism specified, seed thresholds
honestly empirical, tuned via Class-C gates against real conflict data over the first months.

## Sprint 10 — Lifecycle & safety controls: TL · CB · SH · SEN · SDG · SM + HUMAN_GATE full

**Goal:** the autonomy lifecycle, the freeze/breaker controls, and the full class-tiered gate.

**Builds**
- **TL** auto-fed from HVP/TS/BB/OI; feeds AG; tier-down instant. **CB** with **capability
  freeze** ("stop evolution, keep operation") per `capability_class`. **SH** shadow mode. **SEN**
  pluggable watchers (folder/inbox/webhook/RSS) — e.g. masters land → draft release.
- **SDG** (transitive skill-golden regression block) and **SM** (substrate mapper — scope
  self-improvement proposals to the right primitive per domain).
- **HUMAN_GATE full:** enrich v0 to Class A (auto, EL-logged, batch-reviewable) / B (batched) /
  C (individual); class = risk × reversibility × AG authority, auto-demotes to C on authority
  drop or freeze; **gate expiry, delegation/escalation chain, degraded mode** (absence shrinks
  the agent — capability growth pauses, only trusted reversible ops continue).

**Depends:** AG, EL, TL, CB; SDG (Curator, Skill-CI, CS); SM (TS, LS, Curator).
**Exit gate:** **A3-012** (owner absence: Class-B/C expire to denied, growth pauses, trusted
reversible ops continue — absence never widens authority); **A3-006 full** (freeze isolates
growth from operation); SDG blocks a promote whose dependent's goldens regress; SM rejects a
cross-substrate proposal. **Confidence:** HIGH (KNOWN patterns + EXTEND organs).

---

## Sprint 11 — LS · Living Specification (PHASE 1 — subtraction only)

> **Read `aurum_LS_design.md` first.** LS is the hardest organ and warrants its own design doc:
> Legislative Pipeline framing, safety-as-constraint multi-objective, Shadow + Canary governance
> (and the ceiling that shadow measures *divergence*, not *outcome quality*), the constitution/
> statute meta-rules (region membership is constitutional; **LS cannot legislate LS**), the
> off-policy replay caveat, and the LS-FM failure-mode catalogue. This sprint ships only the
> first rung; later rungs (shadow → canary → semi-autonomous statute) are separate, evidence-
> earned sprints, and **autonomous constitutional change never ships**.

**Goal:** governed self-modification of the agent's operating identity — starting with
*removing* bad governance, which is tractable; *creating* good governance is deferred.
LS is a rule-**evaluation** system that occasionally earns the right to **recommend** — never
a rule-writing engine. Build it the latter way and it is the first organ to fail.

**Builds**
- Constitution in three regions: **Core (unproposable)** / **Adaptive (gated proposals)** /
  **Experimental (gated + auto-expire)**.
- `score(window)`; **Phase 1 = retirement + reweighting ONLY** — DISUSE retirement (unused N
  days) and HARM retirement (in the causal chain of repeated OI-dissatisfied/BB failures),
  with **PROTECTED** rules (GR-priority / CC-concentration) exempt from disuse-retirement.
  **Rule creation deferred** to a later phase earned by strong evidence.
- **Attribution discipline:** benefit claims need A/B or holdout; list confounders from EL;
  label unsupported claims `evidence: weak` (expect most to be weak — correct, not a bug).
- **Entropy limit** (net complexity rises only with matched benefit) + **rule aging**.
- Preferences redirect to PM; Core proposals rejected pre-gate.

**Depends:** BB, PK, EL, PM (mature).
**Exit gate:** **A3-004** (Core-touching proposal rejected before the gate, never queued);
spec LS accept: retire on disuse/harm, weak-evidence labelled, entropy-violating proposal
rejected, preference redirected, `rollback` restores byte-identical. **Confidence:** HARDEST —
expect LS v1 **small**; a flood of confident revisions is a red flag, not success.

---

## Sprint 12 — Late-stage governance: MGC · KVE · CC · RS

**Goal:** the organs that only make sense once there's accumulated state to manage.

**Builds**
- **MGC:** archive≠delete (everything `restore`-able), **lease-aware** (never touches a
  CS-leased artifact), retire long-quarantined tools (with TS+TCM), **lossless structural** EL
  compression. Tool retirement is HUMAN_GATE.
- **KVE:** per-artifact `{confidence, last_verified, volatility_class}` + provenance;
  STATIC/SLOW/**FAST** decay; re-verify FAST artifacts (D365/Azure/Power Platform), quarantine
  stale knowledge from planning before it misleads.
- **CC:** concentration monitor — flag a tool servicing a disproportionate share (do-not-retire
  signal to MGC, harden/split signal to TCM).
- **RS:** priority queue over background organ work (foreground always preempts; starvation
  guard) — add when ≥3 background organs (AA/EG/HVP/LS/MGC/KVE) actually contend.

**Depends:** MGC (CS, BB, Curator, TS, TCM, EL); KVE (BB, Curator, TS, MGC, PK, AG, EL); CC
(TCM, MGC, EL); RS (CG, GR, EL).
**Exit gate:** **A3-002 full** + **A3-003 full** (MGC compress round-trips lossless; MGC scan
omits leased `tool_alpha` with no edges); KVE flags an aging D365 lesson stale and invalidates
on a changed endpoint; CC flags a systemic tool; RS runs higher goal-relevance first and
preempts for foreground. **Confidence:** HIGH (MGC/KVE/CC/RS are well-understood).

---

## Deferred (documented only, post-v1)

- **AO — Agent Orchestrator** (multi-agent authority inheritance, lease arbitration, canonical
  EL). The feeders (AG, CS leases, EL, TL) are already per-entity, so retrofit is clean — keep
  single-agent assumptions out of those interfaces.
- **CS deep projection** (`CS.project` probabilistic risk) — research, not engineering; enable
  only once EL history can validate predictions.

---

## Standing build rules (apply across all sprints)

1. **Kernel first, kernel hardest.** Sprints 1–2 (PK/EL) gate everything; their benchmarks are
   exit criteria, not nice-to-haves.
2. **Governance before capability.** AA (Sprint 6) is the first organ that *creates* rather than
   manipulates information; it ships only after the governance scaffolding around it exists.
3. **Instrument through EL from day one.** IDM/MPD record from Sprint 3 so AG/EG tuning later
   has a real baseline. You cannot tune a feedback loop you never measured.
4. **A3 assertions are the safety-parity gate.** Each lands with its organ; the full
   `AURUM_ERR_001..020` set must pass before the execution loop runs unsupervised.
5. **Expect PARTIAL/HARDEST organs to start small.** HVP may shrink to high-stakes; AA lands as
   an extension engine; LS v1 is mostly subtraction. Let EL evidence — not more design — grow them.
6. **Resist organ #N+1.** Per the spec's "WHERE TO STOP": the remaining returns are in *building*
   and discovering real failure modes, not adding organs. AO stays deferred.

## Language & stack decision

**Organs: Python (strict-typed). Host/launcher: TypeScript. Rust: reserved scalpel, not the substrate.**

Rationale (the instinct "Rust for speed/safety/efficiency" is right in general, wrong as the default here):
- **The decision loop is LLM-bound, not CPU-bound.** `PK.check` and `EL.append` are "hot" *relative to
  the loop*, but the loop is dominated by LLM calls (~hundreds of ms to seconds). A 1ms Python check vs a
  10µs Rust check is noise next to a 2s model call. Raw compute speed is not the bottleneck the kernel
  hardening targets — *correctness, fail-safe ordering, and indexed reads* are, and those are language-neutral.
- **The brain is already Python** (Hermes / Nous Research, MIT). Organs are designed as Python modules.
  Rust means either rewriting Hermes (throws away the fork) or a PyO3 FFI seam per organ. The user's own
  environment already shows the cost: headroom's Rust/PyO3 extension *doesn't build on Python 3.14*
  (CLAUDE.md). A polyglot Py+Rust+TS stack from day one is the exact premature complexity the spec warns is
  the project's real failure mode.
- **The "safety" that matters here is governance correctness + type safety, not memory safety.** Python is
  already memory-safe; Rust's edge is its type system. We get most of that from strict typing — Pydantic +
  mypy, plus the spec's own `TypedDict`/`Literal` schemas — without the iteration tax. Velocity matters more
  than runtime for v1, because the hard part is discovering *what the governance logic should be*, and that
  changes constantly; Rust's compile-time rigor slows exactly the exploration we most need.
- **EL durability/integrity is a SQLite + hash-chain problem, not a language problem.** SQLite gives
  durability; the chain is a few lines of sha256 anywhere. Rust doesn't make the ledger more correct than a
  correct schema + write-then-act ordering does.

**When Rust IS the right call (measurement-triggered, like the EL hot-cache discipline):** a *specific*
component that profiles as genuinely CPU-bound — e.g. CS graph ops over a very large dependency graph, PK
taint-matching over thousands of denied signatures, or `EL.verify_chain` over millions of events — is a
candidate to drop into a surgical PyO3 module. Reach for Rust with a profiler in hand, on one component,
after it demonstrably hurts — never as the default substrate. (A shared high-throughput EL service across
parallel agents could also justify it, but that's Tier-4 AO, deferred.)
