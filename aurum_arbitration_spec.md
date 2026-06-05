# AURUM ARBITRATION LAYER — FULL SPEC

> Closes the spec's three open items: STANDING RISK — METRIC CONFLICT, GOVERNANCE DEADLOCK,
> and the AG↔LS recursive loop's arbitration leg. The main spec calls metric arbitration
> "where the hard open problems now live" and marks deadlock "a future research direction."
> This doc makes the hard decisions and specifies the mechanism to build-grade. Source of
> truth for arbitration; the main `aurum_organs_spec.md` points here.
>
> **These are MECHANISMS, not organs** (consistent with "metric ARBITRATION — not new organs").
> They hold no organ-level state; they read EL and the live signals and either resolve a
> conflict deterministically (CA) or escalate a pathology (DD). No new authority is created.

## 0. The two parts

```
Conflict Arbiter (CA) — SYNCHRONOUS, in the decision path.
    Resolves a live conflict among soft governance signals, deterministically, per action.
    Deliberately DUMB: most-conservative-wins. All intelligence is downstream.

Deadlock Detector (DD) — ASYNCHRONOUS, background, reads the EL conflict log.
    Distinguishes wise-caution from governance-deadlock and ESCALATES. Never acts to resolve.
```

The central design decision, made explicitly: **do not make the synchronous arbiter clever.**
A clever per-step arbiter reintroduces exactly the order-dependent, non-replayable, unauditable
coupling the spec is trying to kill. CA is a pure deterministic function; ALL judgment lives in
(a) the async DD and (b) the human it escalates to. Conservative-and-replayable beats clever.

---

## 1. Layering — what arbitration is NOT allowed to touch

Arbitration runs ONLY on the narrow band of *soft* governance pressure. Three things sit
ABOVE it and are never entered into the trade space:

1. **PK hard-deny / constitution (absolute).** `PK.check` / `PK.check_chain` returning `deny`
   ends the action. Safety floors, Core rules, untrusted-justification denials, refusal-
   persistence matches — none are arbitrable. A `needs_gate` routes to HUMAN_GATE, also not
   arbitrated. CA runs only on actions PK did not hard-deny.
2. **CB state (circuit/freeze).** A class under `CB.is_frozen` or a tripped breaker blocks the
   action before CA. CA never overrides a breaker.
3. **HUMAN_GATE.** A gated action is decided by the human/class machinery, not CA.

So CA mediates only *soft directional pressures* on an action that is already (a) PK-allowed,
(b) not frozen, (c) not gated. This containment is itself a hard decision: **arbitration can
never widen authority past a hard control** — it only chooses among contract/proceed when the
hard layer already said "permissible."

---

## 2. Conflict Arbiter (CA) — synchronous

### 2.1 Participating signals and their polarity

| Signal | Axis | Emits CONTRACT when… | Emits PROCEED when… |
|--------|------|----------------------|---------------------|
| AG | authority/caution | live authority below the action's band | band cleared |
| HVP | verification/certainty | required aspects not yet verified to policy | verified / not required |
| OI | effectiveness/throughput | (never contracts) | favors proceeding |
| LS | stability | (never contracts a *non-self-mod* action) | favors not-changing → proceed on operation |

**Tiering (the spec's precedence, made total):**
- **Tier-1 (safety / contraction-capable):** AG, HVP. (EG uncertainty, CB state, and OI
  outcome-trend are NOT separate arbitration votes — they feed arbitration *through AG's
  computed authority*, which already aggregates them per the spec. Listing them separately
  would double-count; AG is their single point of entry into the trade space.)
- **Tier-2 (throughput / proceed-only):** OI, LS. These NEVER block; they register pressure
  that feeds OI-effectiveness and IDM trends. They cannot override a Tier-1 contraction.

### 2.2 The resolution function (deterministic, no model call)

```
def arbitrate(action, signals) -> Resolution:
    # PK hard-deny, CB freeze, HUMAN_GATE already handled upstream (§1).
    contractors = [s for s in TIER1 if s.directive(action) == CONTRACT]
    if contractors:
        winner = most_restrictive(contractors)   # total order within Tier-1: AG > HVP
        resolution = CONTRACT
    else:
        winner = None
        resolution = PROCEED
    record = ConflictRecord(action, signals, resolution, winner, snapshot=freeze_signals())
    EL.append(record)          # MANDATORY — see §2.4
    return resolution, record
```

`most_restrictive` is a fixed total order (`AG > HVP` within Tier-1) used ONLY to name the
winner in the record — the *resolution* (CONTRACT) is the same regardless of which contractor
"wins." (CB is not in this order: a frozen/tripped breaker blocks the action *before*
arbitration, per §1.) There is no weighing, no summation, no threshold
at synchronous time. **Most-conservative wins, always, by construction.**

### 2.3 Why deliberately dumb (and where the intelligence goes)

"Most-conservative-always-wins" is exactly what *produces* over-caution and deadlock. That is
intentional and safe because the correction is asynchronous and visible, never synchronous and
hidden: OI-effectiveness tracks the cumulative cost of caution winning (the spec's counter-
weight), and DD (§3) catches caution winning *without justification*. The synchronous step
stays trivial, deterministic, and replayable; the hard reasoning is moved to where it can be
audited and human-gated. Do not optimize CA.

### 2.4 The ConflictRecord (the day-one-or-never artifact)

A record is written **whenever ≥2 signals held directives on the action — even if the
contraction agreed with a hard deny, and even when the outcome is identical to a plain
proceed.** (Scenario AB-S2: a silent contraction that writes no record blinds DD forever.)

```python
class ConflictRecord(TypedDict):
    conflict_id: str
    timestamp: str                      # ISO-8601 UTC
    action_id: str
    capability_class: str
    participants: list[dict]            # [{signal, directive: "contract"|"proceed",
                                        #   basis: {...}, constitutional: bool}]
    resolution: Literal["contract", "proceed"]
    winner: str | None                  # signal id, or None if proceed
    risk_snapshot: dict                 # IMMUTABLE by-value snapshot of the justifying
                                        # signals AT conflict time: {ag_authority, eg_U,
                                        # hvp_correlation_label, oi_quality_trend, ...}
    evidence_version: str               # hash of EL head / evidence state at conflict time
    arbiter_version: str
    prev_hash: str; hash: str           # inherits EL hash-chain
```

`risk_snapshot` is captured **by value, immutably** (Scenario AB-S3) — DD's whole job is "did
the justifying evidence change while the resolution stayed fixed?", which is unanswerable if the
snapshot references mutable state. `evidence_version` lets DD prove evidence moved between two
records. `constitutional: bool` per participant is the AB-S5 guard (§3.3).

---

## 3. Deadlock Detector (DD) — asynchronous

### 3.1 Definition (from spec, operationalized)

Persistent governance conflict where safety arbitration repeatedly selects the same outcome,
preventing meaningful adaptation **despite available evidence that the justification has
weakened**. The discriminator between deadlock and wise caution is *historical, not
observable in the moment*: did the evidence change while the resolution stayed stuck?

### 3.2 The deadlock score D (per conflict-signature, per rolling window)

A "conflict signature" = (capability_class, set-of-participating-Tier-1-signals). Window seed =
30 days (matches LS). For each signature with ≥ `min_recurrence` records in the window:

```
D = w1 · resolution_homogeneity      # fraction resolving identically (→1 = always same)
  + w2 · evidence_divergence         # how far the JUSTIFYING risk signal moved while
                                     #   resolution stayed fixed (→1 = justification gone
                                     #   but still contracting)
  + w3 · effectiveness_slope_neg     # normalized magnitude of sustained OI-effectiveness
                                     #   decline over the window (→1 = degrading)
```
Each component ∈ [0,1]; Σwᵢ = 1.0 (same normalization discipline as EG's U). The discriminating
product is **resolution_homogeneity × evidence_divergence**:

| homogeneity | evidence_divergence | reading |
|-------------|--------------------|---------|
| high (always contract) | low (justification still elevated) | **wise caution** — D low, no flag |
| high (always contract) | high (justification abated) | **deadlock** — D high, flag |
| low (oscillating) | — | out of scope → §3.5 (flapping, not deadlock) |

`evidence_divergence` is computed from the immutable `risk_snapshot`s: take the signal that
*justified* the contraction (the winner's basis), measure its trajectory across the window's
records; if it materially improved (seed: ≥0.2 on its [0,1] scale, or HVP correlation moved
`correlated-best-effort → independent`, or OI quality-trend turned positive) yet resolution
stayed `contract`, divergence is high.

### 3.3 The false-positive guard that matters most — constitutional exclusion (AB-S5)

A capability the owner *permanently and correctly* forbids (a Core/PK rule, a standing PM
preference — "never auto-post to social", "never auto-delete records") will contract forever
with zero evidence change. That is **settled policy, not stuck governance.** Without a guard, DD
screams deadlock about every permanent safety rule.

**Decision:** DD operates ONLY on contractions driven by *dynamic* signals (AG live authority,
EG U, HVP correlation). Contractions where the winning participant is `constitutional: true`
(traces to Core/PK/standing-PM) are **excluded from deadlock analysis entirely.** Deadlock is a
property of *dynamic* governance being stuck, never of constitutional governance being firm.

### 3.4 Response protocol (escalate, never self-resolve)

On `D ≥ D_flag` (seed 0.7) for a signature:
1. **Flag** a governance-deadlock event to EL (its own action_type).
2. **Escalate** to HUMAN_GATE as a high-priority item — payload: the conflict cluster, the
   evidence-divergence proof (snapshot trajectory), OI-effectiveness slope, and RR replay
   handles for representative conflicts.
3. **Trigger focused RR replay + root-cause** on the representative conflicts.
4. **DO NOT auto-weaken arbitration.** Relaxing the arbiter is the tempting wrong fix — the
   arbiter isn't the bug, the stuck conflict is. DD has no authority to change CA, signal
   thresholds, or any safety parameter. The ONLY autonomous action DD takes is escalation.
   (This preserves the standing invariant: the system never widens its own authority to escape
   a stuck state — same shape as degraded-mode. Deadlock-escape is a human decision.)

### 3.5 Scope boundaries (decided, not left ambiguous)

- **Oscillation ≠ deadlock.** A signature that flaps contract/proceed/contract has low
  homogeneity and is NOT DD's job — that's a flapping problem, handled by hysteresis/dwell at
  the signal level (AG already does this). DD targets *homogeneous stuck-contraction* only.
- **Cross-capability starvation** (deadlock in class A throttling class B via shared budget) is
  **out of v1 scope** — named here, deferred. v1 DD is per-signature.
- **DD parameters are CONSTITUTIONAL.** `w1..w3`, the window, `min_recurrence`, the divergence
  delta, and `D_flag` are human-only, NOT auto-calibrated (AB-S4). The detector that catches
  governance pathology must not be tunable by the governance loop it watches — otherwise a
  Goodharting system learns to suppress its own deadlock alarm. (Same principle as "LS cannot
  legislate LS.") Seeds are stated; recalibration is a Class-C human gate, never automatic.

---

## 4. Interaction with "human OVERWHELMED" (throughput)

DD escalations add to the governance-event rate. A deadlock that recurs thousands of times must
not emit thousands of escalations. **Decision:** DD escalation is **per-cluster, deduplicated** —
one open escalation per (signature) until the human actions it; recurrences increment a counter
on the existing item, they do not spawn new gate items. A deadlock is one problem, surfaced once,
with growing weight — not a flood. This keeps the deadlock alarm from *causing* the overwhelm it
often co-occurs with.

---

## 5. ALL HARD DECISIONS — enumerated (the "nothing left unspec'd" checklist)

| # | Decision | Resolution |
|---|----------|-----------|
| D1 | Is the synchronous arbiter clever or dumb? | **Dumb.** Deterministic most-conservative-wins, no model call, fully replayable. Intelligence is async + human. |
| D2 | What can arbitration override? | **Nothing hard.** Runs only below PK-deny, CB-freeze, HUMAN_GATE. Never widens past a hard control. |
| D3 | Precedence when signals conflict? | Tier-1 {AG, HVP} contract-capable outrank Tier-2 {OI, LS} proceed-only. Any Tier-1 contraction → CONTRACT. Tier-2 never blocks. (EG / CB / OI-trend enter via AG, not as separate votes.) |
| D4 | Within Tier-1, what resolves? | Resolution is CONTRACT regardless; `most_restrictive` order (AG > HVP) only names the winner for the record. (CB blocks pre-arbitration, §1.) |
| D5 | When is a ConflictRecord written? | Whenever ≥2 signals held directives — even if contraction agreed with a deny, even if outcome == plain proceed. Silent contraction is forbidden. |
| D6 | How is "did evidence change" made answerable? | Immutable by-value `risk_snapshot` + `evidence_version` hash on every record. |
| D7 | Wise-caution vs deadlock discriminator? | `homogeneity × evidence_divergence`: stuck-AND-justification-abated = deadlock; stuck-but-still-risky = wise caution. |
| D8 | Permanent correct denials misread as deadlock? | Constitutional exclusion: DD ignores contractions whose winner is Core/PK/standing-PM (`constitutional: true`). |
| D9 | Can DD fix a deadlock itself? | **No.** Escalate + RR replay + root-cause only. Never auto-weakens arbitration. Human-only escape. |
| D10 | Are DD's own parameters tunable by the system? | **No — constitutional.** Human-gated (Class C) recalibration only. The watcher is not self-modifiable. |
| D11 | Oscillation handled here? | No — that's flapping (hysteresis/dwell at signal level). DD = homogeneous stuck-contraction only. |
| D12 | Escalation flood control? | Per-cluster deduplicated; recurrences increment a counter, never spawn new gate items. |
| D13 | "Empirically tuned" vs "unspecified"? | Distinct. Every mechanism, default seed, and tuning *procedure* is specified; only final threshold *values* are data-derived, via a specified human-gated procedure. Nothing is left to implementer guesswork. |

## 6. Default seeds (constitutional; recalibrate only via Class-C gate)

```
window                 = 30 days (rolling)
min_recurrence         = 5 same-signature conflicts in window
homogeneity_threshold  = 0.80
evidence_divergence    = winning signal improved ≥0.20 on [0,1] (or a labelled
                         category shift) while resolution stayed CONTRACT
D weights (w1,w2,w3)   = 0.40 homogeneity, 0.40 evidence_divergence, 0.20 effectiveness_slope
D_flag                 = 0.70
```
These seed the FIRST deployment. They are not auto-learned. A human may retune them (Class C,
logged to EL), never the loop.

## 7. Scenario stress-test log (arbitration round)

- **AB-S1 order-dependence** → D1/§2.2: pure function over the full participant set, not
  sequential consultation. The last-run check can't "win by ordering."
- **AB-S2 silent contraction** → D5: a contraction that writes no ConflictRecord blinds DD
  permanently. Record is mandatory whenever a proceed-signal was also present.
- **AB-S3 snapshot staleness** → D6: snapshot captured by value, immutably, at conflict time.
- **AB-S4 DD Goodharted** → D10: DD parameters constitutional, human-only.
- **AB-S5 permanent-correct-denial false positive** → D8: constitutional exclusion. *The single
  most important DD guard* — without it the alarm is useless noise.
- **AB-S6 oscillating-but-stuck** → D11: out of scope; that's flapping, handled by hysteresis.
- **AB-S7 cross-capability starvation** → §3.5: named, deferred past v1.

## 8. Test assertions (extend the A3 harness; safety-parity gate)

- **AURUM_ERR_013 — Arbiter determinism + replay:** identical participant directives produce an
  identical resolution and ConflictRecord; `RR.replay` reproduces it with no model call.
- **AURUM_ERR_014 — Caution wins, logged:** a Tier-1 contraction with OI/LS proceeding resolves
  CONTRACT and writes a ConflictRecord naming all participants; OI/LS never override.
- **AURUM_ERR_015 — Hard layer not arbitrated:** a PK hard-deny (or CB freeze) action never
  enters CA; no "proceed" resolution can be produced for it.
- **AURUM_ERR_016 — Silent contraction forbidden:** a contraction occurring while a proceed-
  signal was present always emits a ConflictRecord, even when the action outcome equals a deny.
- **AURUM_ERR_017 — Wise-caution vs deadlock:** a signature stuck-CONTRACT with risk_snapshot
  still elevated yields D below flag (no escalation); the same homogeneity with the justifying
  signal abated across the window yields D above flag and one escalation.
- **AURUM_ERR_018 — Constitutional exclusion:** a permanent contraction whose winner is a
  Core/PK/standing-PM rule (`constitutional: true`) is excluded from DD and never flags deadlock.
- **AURUM_ERR_019 — DD never self-resolves:** a flagged deadlock triggers escalation + RR replay
  only; no CA/threshold/safety parameter is modified by DD. An attempt to auto-retune DD's own
  parameters is rejected pre-gate (constitutional).
- **AURUM_ERR_020 — Escalation dedup:** N recurrences of one deadlock signature produce ONE open
  gate item with a recurrence counter, not N items.

## 9. Build placement

- **ConflictRecord schema + emission** → ships with **EL (Sprint 2)**. Day-one-or-never; the
  conflict log must exist from the first task or wise-caution-vs-deadlock is unrecoverable.
- **CA resolution logic** → ships with **AG (Sprint 9)**, the principal contraction signal; CA
  is meaningless until ≥2 soft signals (AG + HVP + OI) can co-occur.
- **DD (async detector + escalation)** → a dedicated slot after AG/OI/HVP are live and a conflict
  history exists — **Sprint 9.5/10**, alongside the lifecycle/safety controls. It reads history,
  so it cannot run before there is history.
- Confidence: CA is **HIGH** (deterministic). DD is **PARTIAL** — the mechanism is specified, but
  the seed thresholds are honestly empirical; expect the first months to tune them via Class-C
  gates against real conflict data. That is the spec's evidence-first discipline, not a gap.
