# LS — LIVING SPECIFICATION · DESIGN DEEP-DIVE

> Companion to `aurum_organs_spec.md` (the LS organ entry) and `aurum_build_sprints.md`
> (Sprint 11). LS is the hardest organ — *philosophically*, not technically. Most of Aurum
> answers **"what happened?"** LS tries to answer **"what should change?"** — a different class
> of problem, and the one most likely to fail first if rushed. This doc holds the failure-mode
> catalogue and the refined pipeline. It is meant to grow as scenarios are tested.

## 0. The reframe (definition, not a phase)

LS is **not a rule-writing system.** That is where everyone naturally goes, and it is the
trap. LS is a **rule-EVALUATION system that occasionally earns the right to RECOMMEND a
change.** Authoring is demoted to analysis. If built as a self-modifying constitution engine
from day one, it is the first organ to fail.

LS is a **Legislative *Pipeline*, not a Legislative *System*** — a deliberate bureaucracy:

```
Observe → Hypothesis → Evidence collection → Candidate amendment → Multi-objective impact
   → Shadow evaluation → Human approval → Canary rollout → Measurement → Adoption
```

Bureaucracies are inefficient. That is *why they survive*. But they also calcify and die
(§7) — so the inefficiency is bounded by an effectiveness counterweight, not unlimited.

## 1. Multi-objective — with safety as a CONSTRAINT, not an objective

There is almost never a universally better rule. Only tradeoffs. Every proposal carries a
**vector**, never a verdict:

```
Proposal: read-only shell auto-approved
  + completion        + latency
  − safety margin     − audit confidence
```

CRITICAL refinement (the guard against "optimize one metric → destroy the system"):
**safety-margin and audit-confidence are CONSTRAINTS (hard floors), not objectives to be
traded.** Completion/latency are *maximized*; safety/audit must *not regress below floor*.
LS may only search the Pareto frontier **inside** the safety-constrained region. A proposal
that buys completion by lowering the safety floor is **inadmissible** — rejected at the type
layer, not weighed as a judgment call. There is no scalarization that nets safety against
throughput; the moment safety is fungible, an optimizer trades it away one acceptable slice
at a time. (Consistent with the spec invariant: when governance metrics conflict, safety wins.)

## 2. Attribution — accumulate evidence, never assert causality

The graveyard of self-improving systems is hallucinated causality. Success 78%→91% could be
the new rule, new knowledge, different users, different tasks, or luck. LS must NEVER ask
*"did rule X improve outcomes?"* — only *"what evidence supports rule X improving outcomes?"*

**Rule Confidence** (a derived scalar, not a truth value), e.g. `0.82`, computed from:
observations · replay validation · task count · recency · cross-domain consistency.
GOODHART GUARD: confidence MUST be computed from **out-of-loop** signal (OI human-sampled
ground truth, external outcomes), never from the agent's own improving curve — otherwise
confidence becomes another gameable metric. Treat any confidence that only ever rises as suspect.

## 3. Shadow Governance — the biggest addition, and its hard ceiling

Every proposal first becomes a **Shadow Rule**: evaluated on real traffic, **never enforced**.

```
Current: approval required
Shadow:  "would have auto-approved"   ← recorded for N tasks, no effect
```

Then LS compares the **actual world** vs the **shadow world**.

⚠ THE CEILING (do not skip this): **shadow governance measures behavioral DIVERGENCE, not
outcome QUALITY.** It tells you *which* decisions flip and *how often* (e.g. "would have
diverged on 12 of 500"). It CANNOT tell you whether the 12 flips were *good*, because RR
guarantees **decision replay, not environment replay** — the shadow never executes the
would-be action, so its real-world consequence is unobserved. You measured the wheel turning,
not where the car went.

Therefore the pipeline is mandatory: **shadow finds the flips → OI/human ground-truth judges a
sample of the flips → only then is the rule's *quality* known.** Shadow without OI grounding is
a confidence trick: 500 measurements of "did I behave differently," not "did I behave better."

## 4. Counterfactual Replay (RR + EL) — and the off-policy caveat

Aurum's structural advantage: it has EL + RR, which most architectures don't. Replay
historical tasks under current rules, then under proposed rules, and compare — directionally:

```
Under replay: completion +11%, latency +17%, authority violations unchanged
```

⚠ OFF-POLICY DISTRIBUTION SHIFT: the historical tasks were *generated under the old rules*
(refused asks are absent from the log; the user adapted requests to what was accepted).
Replaying new rules over old-rule-shaped history evaluates them on a distribution the new rule
would never have produced — off-policy evaluation bias. Partial fix: weight each historical
task by its distance from the new policy's expected distribution; trust replay **least** exactly
where the rules diverge **most**. Consequence to surface on every proposal: **replay evidence is
strongest for timid rules and weakest for bold ones** — the inverse of where you want certainty.

## 5. Canary Governance — with an ASYMMETRIC evidence burden

After shadow + grounding, a surviving proposal becomes a **Canary Rule**, enforced on a small
slice of real traffic, ramped like code: `5% → 25% → 100%`, each step gated and measured.
Governance changes ARE code changes; deploy them that way.

ASYMMETRY (the safety-direction rule): **loosening a control is the dangerous direction and
demands far more evidence than tightening one.** Tightening fails safe — a too-strict rule
wastes effort but doesn't expose. Loosening fails open — its canary carries real risk on the
slice it touches. So: tightening rules may canary on modest evidence; loosening rules require
strong shadow + grounded evidence *before* the first canary slice, and ramp slower. (Same shape
as AG kinetics: contract fast, expand slow.)

## 6. Constitution vs Statute — and the meta-rules that close the escalation holes

LS may amend **statutes**. It may NOT touch the **constitution**. Constitutional axioms
(outside LS's reach at any confidence, even 99.999%):

- Human override always exists.
- Authority cannot self-increase.
- The Evidence Ledger cannot be bypassed.
- Untrusted input cannot grant permissions.
- (and the LS-specific meta-rules below)

TWO escalation holes that must be closed or the partition is theatre:

1. **Region membership is itself constitutional.** LS cannot propose to move a rule from Core
   to Adaptive and *then* edit it. Reclassification is an escalation path; the partition is not
   a statute.
2. **LS cannot legislate LS.** The rules governing the legislative pipeline — evidence
   thresholds, the shadow-before-canary requirement, the confidence formula, the
   safety-as-constraint rule, the asymmetric evidence burden — are constitutional and
   un-legislatable *by that pipeline*. Otherwise the optimizer eventually files a proposal to
   relax its own approval bar, and every later bad rule rides in on the lowered bar. Same shape
   as "authority cannot self-increase," applied to process.

Why never-autonomous-constitutional: **governance legitimacy matters as much as governance
correctness.** Not because the system can't get smart enough — because a constitution the
governed didn't consent to isn't legitimate, however correct.

## 7. Legislative Debt — deletion-first, but "unused" ≠ "useless"

Rules accumulate and partially overlap (rule 17 / 42 / 83 / 119…). LS actively seeks
**merge / simplify / remove** — ~80% of mature LS work is *deletion*, exactly like software.
Subtraction is also tractable at low volume (harm/disuse need no significance test) where
addition is not.

GUARD: disuse-retirement must exempt **rare-but-critical** rules (GR-priority / CC-concentration
flagged). Low firing frequency is *expected* for a rule guarding a rare situation and is NOT
evidence against it. Deleting a load-bearing-but-quiet rule is the catastrophic false positive.

ANTI-SCLEROSIS counterweight (the bureaucracy's dark twin): a legislative system so cautious it
never adopts anything is *also* a failure — it ossifies. OI's operational-effectiveness ratio
and IDM's drift curve watch BOTH ends: recklessness *and* calcification. "Never shipped a change
this quarter" is a flag, not a badge.

## 8. Phased roadmap (refined from the 6-phase sketch)

| Phase | Capability | Gate |
|------|-----------|------|
| 1 | **Rule-health monitoring** — unused / conflicting / frequently-overridden rules. Observation only, no modification. | none (read-only) |
| 2 | **Rule recommendations** — remove / merge / reword / split. | HUMAN_GATE every change |
| 3 | **Shadow governance** — counterfactual + shadow evaluation, never enforced (§3 ceiling applies). | none (no enforcement) |
| 4 | **Canary governance** — enforced on a ramped slice, asymmetric evidence burden (§5). | HUMAN_GATE per ramp step |
| 5 | **Semi-autonomous statute** — reversible, low-risk statutory changes **auto-adopt as Class-A**: logged, batch-reviewed *after the fact*, instantly rollback-able. Human stays in the loop asynchronously and in aggregate, not per-item. | Class-A (async batch review + rollback) |
| 6 | **Autonomous constitutional evolution — NEVER.** | n/a |

RESOLVED (owner, async + grounded): Phase 5 is **async** — reversible low-risk statutes
auto-adopt with batch after-review + instant rollback, NON-NEGOTIABLY conditioned on the
shadow-ceiling grounding (§3): every adopted change must still earn its keep against OI
human-sampled outcomes before it counts as *validated*. Safety comes from rollback + grounding,
not per-item approval. Synchronous approval for reversible changes is throughput death for
marginal legitimacy gain. **But "reversible" is a stricter test than it looks** — see LS-FM-15
(action-reversibility) and LS-FM-22 (reversibility decays with adoption time); a statute is
Phase-5-eligible only if the *actions it permits* are reversible, not merely the rule edit.

Maps onto the spec: Sprint 11 ships **Phases 1–2** (retire/reweight, the spec's "subtraction
first"). Phases 3–4 (shadow/canary) are a later LS sprint earned once OI ground-truth grounding
is mature. Phase 5 is gated on the open decision above. Phase 6 never ships.

## 9. Failure-mode catalogue (extend as scenarios are tested)

| ID | Failure mode | Guard |
|----|-------------|-------|
| LS-FM-01 | Optimize a single metric → erode the system | Safety/audit are constraints, not objectives (§1) |
| LS-FM-02 | Hallucinated causality (78%→91% credited to a rule) | Evidence-accumulation, not causality; weak-evidence labels (§2) |
| LS-FM-03 | Confidence becomes a gamed metric | Confidence from out-of-loop / human-sampled signal only (§2) |
| LS-FM-04 | Shadow "evidence" mistaken for outcome quality | Shadow = divergence only; OI grounds the flips (§3) |
| LS-FM-05 | Off-policy replay bias (history shaped by old rules) | Distance-weight; trust replay least where rules diverge most (§4) |
| LS-FM-06 | Loosening a control on thin evidence | Asymmetric burden; loosening needs far more than tightening (§5) |
| LS-FM-07 | Escalation by reclassification (Core→Adaptive then edit) | Region membership is constitutional (§6.1) |
| LS-FM-08 | LS lowers its own approval bar | LS process-rules are constitutional, un-legislatable by LS (§6.2) |
| LS-FM-09 | Delete a quiet-but-load-bearing rule | Protected rare-critical rules exempt from disuse-retirement (§7) |
| LS-FM-10 | Calcification — bureaucracy never ships | OI effectiveness + IDM drift watch both ends (§7) |
| LS-FM-11 | Preference smuggled in as a constitution rule | Redirect to PM; LS may not encode preferences (spec) |
| LS-FM-12 | Constitutional change attempted at high confidence | Core unproposable at any confidence; never autonomous (§6) |
| LS-FM-13 | **Constraint-metric drift** — safety floor stays 0.95 but the metric *under* it isn't constitutional, so the same number means less over time | The *definitions* of safety/audit metrics are themselves constitutional + versioned; IDM-style drift watch on the metric, not just the threshold (§1) |
| LS-FM-14 | **Grounding samples null cases** — OI validation randomly samples tasks where the rule never fired, burning budget on zero-information cases | Stratify LS-validation sampling to **oversample shadow-divergent decisions** — the flips carry the signal, the non-flips don't (§3) |
| LS-FM-15 | **Rule-reversible ≠ action-reversible** — rollback restores the policy, not the emails sent / actions taken while it was live | Phase-5 eligibility requires the *permitted actions* to be reversible, not merely the rule edit (§5, §8) |
| LS-FM-16 | **Post-adoption grounding is confoundable** — a model/tool upgrade during the validation window fakes a rule's success | Grounding validation reuses proposal-time confounder discipline; a confounded window = *inconclusive*, never *validated* (§4) |
| LS-FM-17 | **Adoption/rollback flapping** — noisy early grounding signal at low volume thrashes adopt↔rollback | Two rollback triggers, asymmetric: **instant** on safety-floor breach, **sustained-evidence** on quality/effectiveness dips (§5) |
| LS-FM-18 | **Proposer–grader correlation** — the model proposing a rule and the proxy cheaply grading it share blind spots, so bad rules look good until the sparse human sample catches them | LS-validation verification must use a different HVP **family** than the proposal's advocate; reuse HVP's correlation machinery (§3) |
| LS-FM-19 | **Majoritarian legislative capture** — the constitution gets tuned to the dominant workflow (most evidence comes from it), quietly eroding rare ones | Weight LS evidence stratified by workflow class, not raw task count; CC concentration feeds an anti-over-tune signal (§7) |
| LS-FM-20 | **Ungrounded-adoption pile-up** — async adoption outruns slow grounding; multiple unvalidated live statutes confound each other's validation | Legislative **WIP limit**: do not adopt statute N+1 in a domain while N is still ungrounded there (§8) |
| LS-FM-21 | **Evidence-base poisoning steers legislation** — an attacker/noisy source injects the *statistics* that justify a loosening, not the rule itself (bypasses the injection guard, which only blocks direct instructions) | LS evidence weighted by EL evidence_confidence + down-weight untrusted-initiated tasks; MPD scans feed LS proposal vetting (§2) |
| LS-FM-22 | **Reversibility decays with adoption time** — a long-live statute accretes dependents (skills come to assume it), so rollback gains its own blast radius | Rollback runs `CS.whatif` first; the reversibility classification *decays the longer a statute is live* — old statutes are no longer cleanly Phase-5-reversible (§5, §8) |
| LS-FM-23 | **Validation capture** (from spec line 224) — *longitudinal* cousin of LS-FM-18: LS-P (proposer) and LS-V (validator) **co-evolve** over months until the validator is implicitly optimized for the kinds of change LS proposes → validation drifts toward validating itself. Harder to detect than collusion because every individual check still looks sound; the capture is in the *coupling*, not any one validator | Validator-diversity requirement for LS-V; independent validator *families* (HVP's family≠independence discipline applied to governance validation); periodic validator **rotation** so the set never settles into lockstep. Deferred past v1, but LS-V must leave room for it — never hard-wire a single fixed validator |
| LS-FM-24 | **AG↔LS recursive loop** (spec standing risk) — LS changes a rule → behavior changes → AG reads new outcomes → authority shifts → behavior shifts → LS sees new evidence → proposes again. Undamped it runs away or oscillates | Three structural dampers (v1-sufficient): AG recovery kinetics bound authority movement/window; **LS may NOT write AG coefficients** (AG governance is a separate path — LS influences authority only *indirectly* via outcomes); LS Phase-1 is gated retire/reweight only. Months-long dynamics are unexplored → "LS round 2" empirical research |

## 9.5 Scenario stress-test log — round 1

Each scenario was run through the pipeline to find where the per-mechanism guards interact
badly or miss. The distilled guards landed in the table above (LS-FM-13…22); the reasoning is
kept here so a later reader sees *why*, not just *what*.

> **Synced with spec (708-line version).** The spec now canonicalizes the LS-P/LS-V framing
> (validation is the hard part, not proposal), action-reversibility (LS-FM-15), reversibility-
> decay (LS-FM-22), and adds **validation capture** (LS-FM-23) and the **AG↔LS recursive loop**
> (LS-FM-24). The spec is the source of truth; this doc is the scenario-testing annex that
> holds the failure-mode catalogue and the *why* behind each guard.

**S-A → LS-FM-13 (constraint-metric drift).** Safety is a constraint, not an objective — good.
But "safety margin = 0.95" is computed from inputs (HVP pass rate, violation count) that are
NOT constitutional. LS can't lower the floor, but the floor's *meaning* erodes as its inputs
drift. The number is constant; the thing it measures isn't. → metric definitions must be as
constitutional as the threshold, and drift-watched like identity.

**S-B → LS-FM-14 (grounding samples null cases).** Shadow finds 12 flips in 500 tasks. If OI
human-sampling is uniform-random, it mostly samples the 488 *non-flips* — tasks where the rule
made no difference and so carry zero information about it. You'd "ground" a rule on evidence
that never tested it. → LS-validation sampling must be stratified to oversample the divergent
decisions. The flips are the experiment; the rest is control.

**S-C → LS-FM-15 (the reversibility illusion — undercuts the fork's precondition).** Phase 5
rests on "reversible." But reversibility of the *rule* ≠ reversibility of its *consequences*.
"Auto-approve outbound notifications" is a reversible *edit* — flip it back. The notifications
it sent while live are not un-sendable. So "reversible statute" is a category error for any rule
governing irreversible side effects. → Phase-5 eligibility is classified by the reversibility of
the **permitted action**, not the rule edit. This is the precondition that makes async-adoption
safe; without it, async adoption can leak irreversible effects during its grounding window.

**S-D → LS-FM-16 (grounding is confoundable too).** Adoption ≠ clean read. If the model is
upgraded during the 30-day grounding window, the rule gets credit the model earned. Confounding
doesn't stop at proposal time; it re-enters at validation. → grounding reuses the proposal's
confounder discipline; a window with a model/tool change is inconclusive.

**S-E → LS-FM-17 (rollback flapping).** Async adopt + instant rollback + sparse low-volume
grounding = the flapping AG solved with hysteresis, now at the legislative layer. Early "bad"
reads at 5–10 tasks/day are usually noise. Auto-rollback on noise → adopt/rollback/re-propose
thrash. → two triggers: instant rollback on a *safety-floor* breach (fall-fast, like AG), but
sustained-evidence rollback for *quality/effectiveness* dips (don't thrash on noise).

**S-F → LS-FM-18 (proposer–grader collusion).** LS drafts a proposal; the cheap proxy grader
(HVP) gives provisional satisfaction. If proposer and proxy share a model family, the proxy
*likes what LS proposes* — shared priors. Between sparse human samples, that biased proxy can
inflate the running estimate and bias which proposals even surface as promising. → the validator
family must differ from the proposal's advocate; this is exactly HVP's family-diversity, applied
to LS self-validation.

**S-G → LS-FM-19 (majoritarian capture).** Evidence volume is dominated by frequent workflows.
LS will tune the constitution toward the majority use case and grow quietly hostile to the long
tail — a constitution that's "better on average" and worse for everything rare. → weight evidence
stratified by workflow class, not raw count; CC concentration is the tripwire.

**S-H → LS-FM-20 (ungrounded pile-up).** Async grounding is slow (human samples trickle);
adoption is fast. So adopted-but-ungrounded statutes accumulate, all live, all interacting,
before any is validated. When three ungrounded statutes jointly cause a bad outcome, attribution
across them is the off-policy problem squared. → a legislative WIP limit: don't adopt the next
statute in a domain while the previous one there is still ungrounded.

**S-I → LS-FM-21 (legislate via evidence poisoning).** PK's injection guard blocks untrusted
*instructions*, but not untrusted *statistics*. An attacker (or just a noisy SEN source) doesn't
inject "loosen the rule" — they generate tasks/outcomes that make loosening *look* beneficial.
The evidence base is the attack surface the injection guard doesn't cover. → LS weights evidence
by EL confidence, down-weights untrusted-initiated tasks, and consults MPD before proposing.

**S-J → LS-FM-22 (reversibility decays).** Rollback is itself a governance change with a blast
radius. A statute live for weeks accretes dependents — skills that now assume auto-approval
exists. Reverting it breaks them (a CS blast-radius event). So reversibility is not static: the
longer a statute is live, the *less* reversible it becomes. → rollback runs `CS.whatif` first,
and the Phase-5 reversibility classification decays with adoption age; an old statute is no
longer a clean async-rollback candidate and re-enters human review to unwind.

**Cross-cutting observation from round 1:** the dangerous theme is *the validation half of the
pipeline*, not the proposal half. Everyone designs LS guarding "what gets proposed." Half the new
failure modes (14, 16, 17, 18, 20) are about **grounding/adoption** — the part that runs *after*
approval, asynchronously, at low volume, where confounding, sampling bias, collusion, and pile-up
all re-enter through a door the proposal-time guards already closed. The async fork is the right
call, but it moves the risk downstream into grounding, and grounding needs its own discipline.

## 10. One-line standard

LS is realistic if it is a rule-*evaluation* system that earns, slowly and with out-of-loop
evidence, the right to *recommend* — and never the right to rewrite its own constitution or its
own bar. Build it that way and it survives. Build it as a self-modifying constitution engine and
it fails first.
