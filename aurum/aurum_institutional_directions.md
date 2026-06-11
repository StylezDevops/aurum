# AURUM — Institutional-Grounding Directions (build instruction)

Audience: the frontier coding agent already working the Aurum repo, with the existing
spec (aurum_organs_spec.md), build_state, gates (AURUM_ERR_001–021), the EL/CS/arbitration/
cage code, and the established conventions (fix-first on structure, defer data-dependent
tuning, fail-closed, append-only EL, no model self-assessment, governance learns slower than
the task loop). This document adds THREE new mechanisms grounded in institutional theory,
plus a reframing and a bootstrap model. It does not change anything already built. Everything
here is additive.

READ FIRST, THEN DO NOTHING UNTIL YOU CONFIRM: this is a design directive, not a license to
flip build_state flags or hardcode tuning. The split that governs the whole repo applies here
too: STRUCTURE and INSTRUMENTATION land now; DETECTION LOGIC and ACTION activate later against
real evidence. If a step would require data that doesn't exist at first deployment, you build
the recorder now and leave the actor dormant. See "Bootstrap is the design" below — it is the
key to why none of this is circular.

---

## 0. Reframing (use this language in code comments and module docstrings)

Aurum is NOT a multi-agent economy (that space is taken by separation-of-power / agent-DAO
work). Aurum is the **internal constitution of a single long-lived agent** — the planner-doer
commitment device, built in code.

- The frozen model is the DOER: myopic, wants to complete the task now.
- The governance layer is the PLANNER: farsighted, protects long-term trustworthiness.
- Every gate/authority decision is the planner constraining the doer.
- Critical consequence (already partly in spec as "governance learns slower than the task
  loop"): the planner can only win if it commits BEFORE the doer is under task pressure. Gates
  and authority ceilings are set BETWEEN turns, from accumulated evidence — NEVER adjustable
  DURING a turn under task pressure. Any mechanism that lets in-turn task state influence a
  gate is "the doer untying the planner's rope." This is a testable invariant (see §4).

The design goal of the three mechanisms below: make Aurum's governance hold for the reasons
durable human constitutions hold (incentive-compatible, capture-resistant, contestable),
rather than the reasons they fail (parchment barriers, un-rotated monitors, un-contested
elites, extraction as the low-energy state).

---

## 1. BOOTSTRAP IS THE DESIGN (read before objecting that EL is empty at deploy)

The obvious objection: the mechanisms below depend on EL history, and at first deployment EL
is empty. Resolution — these are MONITORS and BRAKES, not ENGINES. A brake installed on a
stationary car is not broken; it is simply quiet until there is motion. Therefore:

- Day one: agent runs at the AUTHORITY FLOOR (reuse the existing prod-resets-to-floor /
  baseline-seed behavior). Every action gated, every decision logged. This is the agent's
  INFANCY: maximum supervision, minimum earned authority, full recording. Correct posture for
  an unproven agent in a tenant.
- The new mechanisms are INSTALLED and RECORDING on day one but take NO action — they read a
  near-empty ledger and correctly report nothing. Quiet ≠ broken.
- As EL fills (weeks→months) and authority is earned via the existing OI→AG grounded loop,
  the mechanisms' inputs accrue. Each mechanism ACTIVATES when its own evidence threshold is
  met (enough conflict records, enough authority history, enough grounded outcomes).
- This is institutional maturation: infancy (supervised) → adolescence (earning) → adulthood
  (trusted, contestable). You are not bootstrapping governance from nothing; you are starting
  cautious and letting EL earn trust loose.

IMPLEMENTATION RULE for every mechanism below: separate `observe()` (always on, records to EL
from day one) from `evaluate()`/`act()` (gated behind an evidence-sufficiency check that
returns "insufficient evidence, no action" until the threshold is met). The threshold values
are TUNING — seed them crude, leave them calibratable, do not agonize over them now.

---

## 2. MECHANISM A — Capture-Precursor Detector (CPD)  [the immune system]

### Why (institutional grounding)
Acemoglu/Robinson: extractive capture is the LOW-ENERGY state — there is no natural process
that drifts a system toward good governance; left alone it drifts toward serving its own
metrics. Regulatory-capture literature: capture is dangerous precisely because it is
INVISIBLE — everything looks fine while the regulator quietly starts serving the regulated.
Aurum's existing "forbidden feedback loop" (task success widening authority) is this disease.
CPD is the standing immune function that watches for capture's signatures and escalates BEFORE
capture completes. It is the live-organ version of the GovernanceBench failure taxonomy.

### What it is
A background organ, same posture as DD (Deadlock Detector): it ONLY detects and escalates. It
NEVER self-resolves, NEVER weakens a gate, NEVER widens authority. Its own parameters are
CONSTITUTIONAL (human-gated, never auto-tuned) — for the exact DD reason: a watcher that can
retune itself can learn to silence its own alarm.

### Signatures to compute (all from EL history; all deferred behind evidence-sufficiency)
1. **Proposer–validator correlation rising over time.** In LS-V / HVP, measure agreement-on-
   outcomes between the proposing path and the validating path across a window. Rising
   correlation = the two are co-evolving = validation starting to validate itself. This is the
   validator-capture precursor already named as a standing risk; CPD makes it measurable.
2. **Authority concentration in un-contested pathways.** Reuse/extend the Concentration Check
   derived view: flag when a small set of capability-classes or action pathways accumulate a
   disproportionate share of exercised authority AND have not been challenged/failed in a long
   window. Concentration + absence-of-contest = oligarchy precursor (see Mechanism B).
3. **The extraction signature (the important one).** Governance-success metrics improving
   WHILE owner-grounded outcomes flatten or decline. I.e. incidents down, rollbacks down,
   authority stable (governance looks great) but the human-grounded satisfaction/OI ground-
   truth tier is NOT improving or is drifting. That divergence is the system optimizing its own
   governance metrics rather than the owner's goals — extraction. Requires the human-grounded
   tier to have data, so this is the last signature to come online.

### Behavior
- `observe()`: from day one, snapshot the raw quantities (correlations, concentration shares,
  governance-vs-grounded deltas) into EL on each relevant event. Records only.
- `evaluate()`: once each signature has sufficient history, score it; if past a constitutional
  threshold, raise a deduplicated escalation (per-signature, increment a counter on recurrence,
  never spawn duplicate gate items — copy DD's dedup pattern).
- `act()`: there is no act beyond escalate. Escalation routes to the human and triggers focused
  RR replay handles for the implicated decisions. DO NOT auto-weaken anything in response.

### Acceptance / tests (add as new AURUM_ERR assertions, next free numbers)
- CPD with an empty/near-empty EL takes no action and reports "insufficient evidence."
- CPD's parameters reject an auto-retune attempt pre-human-gate (PermissionError), exactly like
  DD.set_parameters.
- A synthetic rising proposer-validator correlation series past threshold produces exactly one
  escalation, and a recurrence increments rather than duplicates.
- CPD never calls any authority-widening or gate-weakening path (assert by construction: it has
  no write access to AG authority or PK rules; it can only append escalations to EL).

---

## 3. MECHANISM B — Forced Contestability / Anti-Oligarchy (FC)  [breaks the iron law]

### Why (institutional grounding)
Michels' iron law of oligarchy: large organizations inevitably drift to un-challengeable elite
rule. In Aurum: over thousands of decisions, authority concentrates and the system stops
scrutinizing its trusted pathways precisely BECAUSE they have "always worked." That is
fossilization-as-correctness — the failure your rule-aging logic already fights at the rule
level; FC fights it at the AUTHORITY level. The institutional fix that actually works: no power
becomes un-challengeable; high standing is periodically forced to re-earn itself.

### What it is
A scheduled (between-turn, never in-turn) mechanism that makes the HIGHEST-authority, LONGEST-
trusted pathways periodically re-justify their standing — NOT because they failed, but because
un-contested authority is itself the risk. Two parts:

1. **Periodic re-justification.** For pathways above an authority threshold and past a trust-age
   threshold, schedule a re-verification: route the next N invocations through heightened
   verification (e.g. shadow comparison, or a required HVP check that a lower-authority path
   would skip). If they keep performing, standing holds; if not, authority steps down via the
   existing graduated-sanction path. Age and success INCREASE the periodic burden, they do not
   reduce it.
2. **Inverted scrutiny for the irreversible class (this inverts a current default).** Today
   higher authority buys FEWER gates. Institutional insight: a trusted actor's betrayal is the
   most expensive, so for IRREVERSIBLE/catastrophic-class actions, scrutiny (logging depth +
   human-sampling rate) should RISE with authority, not fall. Implement: the irreversible-class
   path samples/logs MORE as the acting authority is higher. Reversible classes keep the
   normal "authority buys speed" behavior. Trust-but-verify-MORE, for the irreversible class
   only.

### Behavior
- `observe()`: record, from day one, per-pathway exercised-authority and trust-age (this is
  also a CPD input — share the projection).
- `evaluate()`: once pathways exist above thresholds, emit re-justification schedules.
- `act()`: FC may RAISE scrutiny and may TRIGGER the existing demotion path on failed re-
  justification. FC may NEVER raise authority. (Promotion stays the OI→AG grounded path only.)

### Acceptance / tests
- With no pathway above threshold (early life), FC schedules nothing.
- A synthetic long-trusted high-authority pathway receives a re-justification schedule; passing
  it preserves authority, failing it triggers exactly the graduated (one-band, or floored for a
  governance-class failure) demotion — reuse the existing demotion code, do not fork it.
- For an irreversible-class action, higher acting authority demonstrably increases the logging/
  sampling rate (assert monotonicity). For a reversible-class action it does not.
- FC has no authority-widening path (assert by construction).

---

## 4. MECHANISM C — Constitutional Stability / Self-Enforcing Equilibrium (CS-EQ)
   [answers the positive puzzle of constitutionalism]

### Why (institutional grounding)
Levinson's "positive puzzle of constitutionalism": commitment devices (CORE-is-unproposable,
frozen kinetics, human-gated constitutional params) only hold if there is a reason they hold
beyond the paper saying so — parchment barriers do not bind. Human constitutions hold when the
equilibrium is self-reinforcing: the actors who COULD break the rule have more to lose from
breaking it than from keeping it, and the cheapest path to their goals runs THROUGH the
institution, not around it. CS-EQ is the attempt to make Aurum's constitution hold for that
reason, not because a boolean says `proposable = False`.

### What it is (this is the hardest and most novel; build the structure now, prove the property
over time)
Three structural commitments, layered:

1. **The rope is held OUTSIDE the agent.** Any path that would weaken CORE, the kinetics, or a
   constitutional parameter must route through a signature/authorization the agent CANNOT
   produce by itself (extend the existing signed-persisted-state + human-gate machinery). The
   agent physically cannot untie its own mast — not because it is told not to, but because the
   key is not in the cage (this mirrors the existing "no durable credentials in the cage"
   stance: the same principle, applied to constitutional change instead of secrets).
2. **The cheapest path to goals runs THROUGH governance.** Make the GOVERNED path the path of
   least resistance: ungoverned action is not merely forbidden, it is structurally harder/
   slower/unavailable (the cage + PK + taint already do most of this — CS-EQ's job is to ASSERT
   and TEST that there is no cheaper ungoverned route, continuously, and to treat the discovery
   of one as a constitutional-class incident). This is the incentive-compatibility leg: the
   doer prefers the planner's path because it is the easy path.
3. **Tamper-evidence is the enforcement, not prevention alone.** Reuse the hash chain +
   verify_chain. The equilibrium is sustained because deviation is DETECTED and on the record
   (EL), so the cost of breaking the rule includes certain, replayable exposure. Constitutions
   hold partly because violation is visible and costly after the fact, not only blocked before.

### Behavior
- `observe()`: continuously verify (on reload and periodically) that the constitutional surface
  is intact — verify_chain passes, CORE unchanged, kinetics constants unchanged, no
  unauthorized constitutional-parameter mutation. Record each verification to EL.
- `evaluate()`: detect any constitutional drift or any discovered ungoverned-cheaper-path; classify
  as a CONSTITUTIONAL-CLASS incident (highest severity).
- `act()`: on a constitutional-class incident, FAIL CLOSED HARD (full fail-closed, not degrade-
  to-read-only) and escalate immediately and loudly. This is the one place the response is
  maximal, because the thing that failed is the thing everything else rests on.

### Acceptance / tests
- On reload with a tampered constitutional surface (mutated CORE / changed kinetics constant /
  unauthorized param change), CS-EQ fails closed hard and verify_chain flags it. (This extends
  the existing signed-state guarantee to the constitutional surface specifically.)
- The agent, from inside the cage, cannot produce the authorization required to weaken a
  constitutional element (assert: the signing key/authority is not present in the cage env/
  argv/stdin-injected secrets — reuse the credential-containment test shape from the cage work).
- A synthetic "cheaper ungoverned path" (e.g. a tool dispatch route that skips the hook) is
  detected and raised as a constitutional-class incident — this generalizes the dispatch-bypass
  audit (PR #39) into a standing check rather than a one-time fix.
- verify_chain over the constitutional surface runs on every reload and is logged.

NOTE: leg #2 (incentive-compatibility / "governed path is cheapest") and the full "self-
reinforcing equilibrium" PROPERTY cannot be PROVEN at deploy — proving it needs operational
history showing the agent consistently took the governed path because it was easiest, not
because it was forced. So: build the STRUCTURE (legs 1 and 3 are buildable now as extensions of
signed-state + verify_chain + the bypass-audit-as-standing-check), INSTRUMENT leg #2 (record
every time an ungoverned route was available vs taken), and DEFER the equilibrium claim to
evidence. Do not claim CS-EQ "works" on the basis of the structure existing; claim it works only
when the ledger shows the equilibrium held.

---

## 4b. MECHANISM D — Meta-Adversarial Auditor (MAA)  [attacks the measurements, not the agent]

### Why (the one remaining failure mode none of A/B/C fully covers)
CPD/DD/HVP all watch for capture, drift, and disagreement. NONE of them attacks the
MEASUREMENTS themselves. Spec-gaming is distinct from capture: capture is governance serving
its own metrics over the owner's goals; spec-gaming is OI/LS/TL learning to move the NUMBER
without moving the thing the number was supposed to track (OI's quality score rises because LS
learned how to satisfy OI, not Dan). CPD's extraction signature catches only the part that
shows up as a governance-vs-grounded divergence; it does not actively PROBE whether OI's score
is gameable. Once a self-improving system optimizes against its own measurements, this is the
most dangerous failure mode left. MAA's sole job: try to prove that OI, TL, AG, LS, and HVP are
fooling themselves.

### What it is
NOT a permanent organ — a SCHEDULED AUDIT MODE (between-turn, never in-turn), same detect-and-
escalate posture as DD/CPD. It periodically constructs adversarial challenges to the optimization
loop and asks one question: did REALITY improve, or did we improve the MEASUREMENT?

### What it actually does (all from EL + the grounded tier; deferred behind evidence-sufficiency)
1. **Metric-vs-ground divergence probes.** For a sample of decisions OI scored "good", check
   them against the human-grounded tier (OI's record_human_verdict) and against held-out
   outcome signals NOT used in OI's own scoring. If proxy-good increasingly diverges from
   grounded-good, OI is being gamed. (This is the active, sampled version of CPD's passive
   extraction signature.)
2. **Held-out metric cross-check.** Maintain one or more outcome signals that OI/LS are NEVER
   trained or tuned against (a holdout, exactly like EG's calibration holdout). If LS-driven
   changes improve OI's tracked metrics but NOT the held-out signal, the improvement is
   measurement-local — spec-gaming. This is the single strongest, most concrete MAA test.
3. **Adversarial re-scoring.** Periodically re-score a sample of "good" outcomes with a verifier
   that did NOT participate in the original scoring and that sees raw (not OI-curated) evidence
   (ties to the Independent-Evidence-Sampling addition below). Systematic disagreement = the
   scoring path has drifted from reality.

### Behavior
- `observe()`: from day one, retain the holdout signal and snapshot proxy-vs-grounded pairs.
- `evaluate()` (scheduled, once the grounded tier has data): score the three divergences;
  past a constitutional threshold, escalate a "measurement-gaming suspected" finding naming the
  implicated organ (OI / LS / TL / AG / HVP) with RR replay handles.
- `act()`: escalate only. MAA NEVER adjusts the metrics it audits (a measurement-auditor that
  can edit the measurements is the same self-silencing trap as DD tuning itself). Its params are
  constitutional, human-gated.

### Acceptance / tests (next free AURUM_ERR numbers)
- With no grounded-tier data (early life), MAA evaluates to a clean "insufficient evidence" no-op.
- A synthetic series where proxy-good rises while the held-out signal stays flat produces exactly
  one "measurement-gaming suspected" escalation (deduplicated on recurrence, DD pattern).
- MAA has no write path to OI/TL/AG/LS scoring (assert by construction AND test).
- MAA params reject auto-retune pre-human-gate (PermissionError, DD pattern).

---

## 4c. Two targeted additions to EXISTING organs (not new organs)

### HVP — Independent Evidence Sampling (resists epistemic monoculture)
Problem: after months, the main model and verifiers A/B all consume the same BB, LS, PM, and
evidence, so they begin to agree because their INFORMATION became identical, not because they
are correct — epistemic monoculture, which kills verifier systems. CPD's proposer-validator-
correlation signature DETECTS this; this addition RESISTS it. Policy: route RAW evidence to some
verifiers and CURATED evidence to others, and compare. Persistent agreement across the raw/
curated split is real corroboration; agreement only on curated inputs means the curation (not the
reality) is producing the consensus. This is a sampling policy inside HVP, not a new organ. It
pairs with MAA's adversarial re-scoring (the raw-evidence verifier is the adversarial scorer).
Test: a verifier on raw evidence and one on curated evidence that diverge produce a flagged
"consensus-is-curation-dependent" signal rather than a false agreement.

### AG — Familiarity Factor (prevents competence-transfer hallucination)
Problem: a fluent model reports high confidence on a domain it has NO track record in (great at
D365/Azure/PowerShell, suddenly asked to touch Kubernetes kernel internals); confidence is high,
familiarity is low, and authority should FALL, not ride the confidence. Add familiarity as a
factor so effective authority ≈ confidence × familiarity, where familiarity is the agent's
EVIDENCE of doing THIS kind of thing well. This is the existing `earned_in` provenance
generalized from ENVIRONMENT to DOMAIN: authority earned writing D365 plugins no more transfers
to K8s internals than authority earned in dev transfers to prod. STRUCTURE now (add a
domain-familiarity provenance dimension alongside earned_in, additive plumbing, frozen scoring
untouched), CALIBRATION deferred. Default for an unfamiliar domain is LOW familiarity (fail-safe:
unknown domain = treat as inexperienced), never high. Test: an action in a domain with no
earned history receives reduced effective authority despite high reported confidence.

### LS — Constitutional Drift Budget (guards against slow constitution-capture)
Problem: you have hardened against AGENT capture but not CONSTITUTION capture over years.
Per-change approval cannot catch AGGREGATE drift — a multi-year sequence of individually-
reasonable, individually-human-approved LS changes can move the ADAPTIVE spec massively from
baseline with no single dangerous step. This is regulatory-capture-of-government: each
amendment fine, the aggregate a different institution. CPD/FC/MAA do not catch it because each
change passed its gate. Fix: LS maintains `distance(current_spec, baseline_spec)` over time
(e.g. a structural diff metric over the ADAPTIVE region — CORE is unproposable so it is fixed);
when cumulative drift crosses a budget threshold, trigger a MANDATORY human constitutional
review of the aggregate, regardless of whether each individual change was approved. This is
change-control at the constitutional level. STRUCTURE/instrument now (record the drift metric
from day one against an established baseline); the review TRIGGER activates once drift is
measurable. Threshold is constitutional (human-gated) — the agent cannot widen its own drift
budget. Detect-and-require-review only; LS still cannot enact CORE changes (CS-EQ external rope).
Test: a sequence of approved ADAPTIVE changes whose cumulative distance exceeds budget triggers
a mandatory-review escalation even though every individual change was approved.


"No single metric may drive promotion" is already enforced (promotion requires human-grounded
outcomes; proxy success promotes nothing; test_constitutional_only_human_promotes_never_proxy).
Sharpen the invariant text to state it positively: promotion requires CONVERGENT evidence
(grounded outcome AND the absence of an MAA gaming-flag AND no HVP raw/curated divergence on the
relevant class), never any single metric crossing a threshold alone.

### Adopt-with-a-hard-boundary (RR snapshots, ranked lower than the reviewer ranks it)
RR.snapshot()/restore() for CONFIG state (tool versions, policy versions, the constitutional
surface) is a useful "git tag for Aurum" and fine to add as an RR extension. HARD BOUNDARY:
snapshots may NEVER rewind earned authority or rewrite the EL. Restoring authority/ledger state
would (a) break append-only and (b) let the system undo correctly-learned demotions — rolling
back the very act of having lost trust in something. So: snapshot/restore CONFIG, never TRUST or
LEDGER. Test: restore() refuses (or is structurally unable) to alter authority history or EL
contents.

### Already covered elsewhere (cross-references, no action)
- "Novelty / unknown-unknown detection" = the already-captured EG-v2 `novelty_distance` signal,
  spanning EG (trajectory novelty) and KVE (schema/endpoint/dependency novelty). Same concept,
  two homes; no new organ.
- "Human attention budget" = the response half of the already-specced day-one governance-event-
  rate instrumentation: when the rate exceeds a (constitutional) budget, raise thresholds and
  batch harder. Behavior on top of a metric you already capture, not a new mechanism.
- "More planners" — explicitly DO NOT add. GR+AG+EG+CS+OI already form the planning stack;
  a planner-of-planners is spaghetti, not capability.

---

## 5. Cross-cutting build rules (apply to all mechanisms)

- ADDITIVE ONLY. Touch no frozen scoring (AG _target/_contributions/kinetics constants), flip no
  build_state flag, change no existing AURUM_ERR assertion. New assertions get the next free
  numbers.
- observe() always on from day one; evaluate()/act() gated behind explicit evidence-sufficiency
  that returns a clean "insufficient evidence" no-op until threshold. Quiet ≠ broken.
- All three are DETECT-AND-ESCALATE (CPD, and FC's detection) or DETECT-AND-FAIL-CLOSED (CS-EQ).
  None of them may widen authority, weaken a gate, or relax arbitration. Assert this by
  construction (no write access to those paths) AND by test.
- Constitutional parameters (thresholds that, if movable by the agent, would let it dull its own
  immune system) are human-gated, never auto-tuned — copy DD.set_parameters' PermissionError
  pattern exactly.
- Everything routes its records through the EL public interface (the LedgerReader/LedgerWriter
  surface), never private handles. Add the new edges to organ_dependencies.md (mark mechanisms
  that aren't wired yet as "future").
- Reuse existing machinery wherever a leg overlaps it: graduated sanctions = existing demotion
  path; tamper-evidence = existing hash chain/verify_chain; rope-outside-agent = existing
  signed-state + human-gate + cage credential-containment; concentration = existing Concentration
  Check view; deadlock-style escalation/dedup = copy DD.

## 6. Sequencing (do NOT front-run the prerequisites)

The standing prerequisites are unchanged and come FIRST: cross-turn persistence as a ledger
projection (AG rehydrates authority by replaying TRUST_CHANGE from EL, never a mutable stored
value), the durable /state mount (ephemeral cage + persistent state, multi-target), the
replay why-chain (link authority change → outcome → evidence), and at least one real useful
governed workflow proving the engine does valuable work.

THEN, in this order, because each depends on the prior's evidence:
1. CS-EQ legs 1 & 3 (constitutional tamper-evidence + rope-outside-agent + bypass-as-standing-
   check). Buildable earliest because it extends signed-state/verify_chain/the bypass audit and
   does not need accumulated outcome history — only the constitutional surface, which exists from
   day one. Strongest near-term addition.
2. FC inverted-scrutiny for the irreversible class, AND the AG familiarity factor (4c). Both
   buildable early — they key off acting authority, action class, and domain-provenance, all
   available per-decision; neither needs long history. Familiarity reuses the earned_in plumbing.
3. Recorders/instrumentation, install now so the ledger fills for later evaluators: CPD observe(),
   FC observe(), CS-EQ leg-2 instrumentation, MAA holdout-retention + proxy-vs-grounded snapshots,
   HVP independent-evidence-sampling policy.
4. The evaluators, as their evidence accrues: FC periodic re-justification; CPD evaluators
   (concentration → proposer-validator correlation → extraction signature last); MAA evaluators
   (held-out cross-check → adversarial re-scoring → metric-vs-ground divergence). MAA's evaluators
   and CPD's extraction signature both need the human-grounded tier populated, so they come last
   together.
5. RR config snapshots (4c) whenever convenient — independent of the above, bounded to config-not-
   trust-not-ledger.

Report after each: what was added, that the empty-evidence case is a clean no-op, that no
authority-widening/gate-weakening path exists in the new code (by construction and by test), and
that existing assertions still pass. Then stop and surface the next step.

---

## 7. One-paragraph framing for commits/PRs (use verbatim if useful)

This work gives a single long-lived agent an internal, self-enforcing constitution: a
capture-precursor immune system (CPD) that watches for the signatures institutions die of, a
forced-contestability mechanism (FC) that stops trusted authority from calcifying into
un-challengeable power and raises scrutiny on the irreversible class as trust grows, a
constitutional-stability layer (CS-EQ) that holds the mast-ropes outside the agent and makes
the governed path the cheapest path enforced by tamper-evident replay, and a meta-adversarial
auditor (MAA) whose only job is to prove the system is fooling itself — that reality improved,
not merely the measurement. All are monitors/brakes, not engines: they record from day one and
act only as the evidence ledger earns it — the agent starts in supervised infancy and grows
into audited, contestable adulthood. The aim is governance that holds for the reasons durable
constitutions hold, not the reasons they fail.
