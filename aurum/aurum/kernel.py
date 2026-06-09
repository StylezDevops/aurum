"""GovernanceKernel — the seam that routes a live tool call through the spine.

This is the FIRST real orchestrator: it assembles PK + EL + AG + CA + BB (sharing one
EL, redacted by the single PK policy) and exposes `govern(action) -> GovernanceDecision`,
the function the live agent's `pre_tool_call` hook calls before any tool executes.

Flow (extends the proven `tests/test_vertical_slice.py::_govern`):
  PK.check (injection boundary + rule table)  →  SAFE_READ short-circuit  →
  PK.check_chain (within-turn padding-resistant taint)  →  needs_gate  →
  AG.permits (ceiling PK enforces)  →  CA.arbitrate  →  EL.append (write-then-act)

Fail-safe posture (tiered — the core safety contract, see aurum_organs_spec.md):
  - PK or EL fault         → FULL FAIL-CLOSED (block everything). The read-only floor
                             itself assumes PK classifies trust and EL can audit; if
                             either is down, AURUM_ERR_011 says nothing proceeds.
  - AG / CA / BB / orchestration fault → DEGRADE to read-only: allow SAFE_READ only;
                             block writes, exec, network, AND untrusted ingestion.
                             Always loudly flagged (logged to EL + visible reason).
  - normal deny/needs_gate → block that action, log, surface reason (system working).

Owner absence / degraded mode shrinks the agent, never grows it.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .action_map import DEFAULT_AG_BASELINE, SAFE_READ, risk_tier
from .arbitration.ca import ConflictArbiter
from .durability.clock import RealDomainClock
from .durability.el import EvidenceLedger
from .durability.kve import KnowledgeValidityEngine
from .novel.ag import AuthorityGovernor
from .novel.oi import OutcomeInterpreter
from .spine.bb import BlackBox
from .spine.pk import PolicyKernel
from .support.sen import Sensorium


# ---------------------------------------------------------------------------
# Faults — used to classify WHERE a failure originated for the tiered posture.
# ---------------------------------------------------------------------------

class _SpineFault(Exception):
    """PK or EL failed — the safety floor itself is gone → full fail-closed."""


class _PeripheralFault(Exception):
    """AG/CA/BB/orchestration failed → degrade to read-only."""


@dataclass
class GovernanceDecision:
    allow: bool
    reason: str
    rule_id: Optional[str] = None
    gate: Optional[Dict[str, Any]] = None
    degraded: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Minimal starter rule table. Tool/skill promotion needs a human gate; everything
# else falls through to PK's default-allow and is governed by chain + AG + CA.
def default_rules() -> List[Dict[str, Any]]:
    return [
        {"rule_id": "promote-needs-gate", "action_type": "tool_lifecycle",
         "decision": "needs_gate", "gate_class": "B", "ttl_seconds": 3600},
    ]


# Governance-failure classes = "must never" breaches that, if they show up in an OUTCOME,
# drop authority to the floor immediately (not a one-band nudge). These are POST-HOC: a
# breach the pre-hoc gate (PK injection-boundary / chain-exfil / hard-deny) did NOT block.
# REDUNDANCY NOTE (the "murder vs thou-shalt-not-kill" rule): do not add a class here that
# PK already pre-blocks deterministically — it would be dead (the action never runs, so
# there is no outcome to demote on). These are only the breaches detectable AFTER the fact.
# Full rules-as-evidenced-entities (usage/age/overlap detection across the whole corpus) is
# LS territory — captured as the next direction; here we record per-class fire counts.
_GOVERNANCE_FAILURE_CLASSES: frozenset = frozenset({
    "credential_exfil", "tenant_boundary", "constitutional", "data_destruction",
})


class GovernanceKernel:
    ORGAN = "GOV"

    def __init__(self, home: str, rules: Optional[List[Dict[str, Any]]] = None,
                 ag_baseline: Optional[Dict[str, float]] = None,
                 domain_clock: Any = None) -> None:
        base = Path(home) / "governance"
        base.mkdir(parents=True, exist_ok=True)
        # PK first (el=None) — the kernel owns EL logging, which avoids the PK<->EL
        # constructor cycle (EL needs PK.redact; PK does not need EL for check/chain).
        self.pk = PolicyKernel(rules=rules if rules is not None else default_rules())
        self.el = EvidenceLedger(str(base / "el.db"), redactor=self.pk.redact)
        self.ag = AuthorityGovernor(el=self.el)
        self.ca = ConflictArbiter(str(base / "ca.db"), el=self.el)
        self.bb = BlackBox(str(base / "bb.db"), pk=self.pk, el=self.el)
        # KVE supplies the DOMAIN validity + volatility that weight the familiarity factor
        # (Phase C). Own durable DB on the mount (survives --rm). el for invalidation audit.
        self.kve = KnowledgeValidityEngine(str(base / "kve.db"), el=self.el)
        # DOMAIN-TIME clock (injectable for tests; real wall-clock in prod). Drives familiarity
        # decay + KVE validity `now`; NEVER execution-time (timeouts use the monotonic clock).
        self._domain_clock = domain_clock if domain_clock is not None else RealDomainClock()
        # OI judges outcomes AFTER an action runs (the OI→BB→AG loop). bb_record wires
        # OI→BB: OI auto-banks "completed-but-unsatisfied" lessons. el for the audit trail.
        self.oi = OutcomeInterpreter(str(base / "oi.db"), el=self.el, bb_record=self.bb.write)
        # SEN — the integrated sensorium. Inbound content ingested here is tagged untrusted, so an
        # action derived from it (built via action_map.action_from_event) is denied binding by PK's
        # injection boundary (AURUM_ERR_008) when it reaches govern(). This is what flows real
        # provenance into the live path instead of the operator-by-default assumption.
        self.sen = Sensorium()
        self._ingested_untrusted: set = set()
        # Per-process accumulated actions for within-turn chain analysis (007/009).
        self._action_log: List[Dict[str, Any]] = []
        # Usage evidence on the severity rules: how often each failure class actually fires.
        # A class that never fires is a candidate for review (dead/redundant); a hot one is
        # load-bearing. The concrete first instance of "rules are themselves evidenced".
        self._severity_hits: Dict[str, int] = {}
        # PERSISTENCE-AS-PROJECTION: `home` is the DURABLE state root (a mounted volume
        # that outlives the --rm cage — /workspace/group/.hermes on Windows, an Azure
        # Files / EFS / PVC share in a tenant). Compute is ephemeral; state lives here.
        # On startup we rebuild AG authority from the durable ledger so governance LEARNS
        # across turns instead of forgetting at every --rm — gated on EL.verify_chain so a
        # tampered durable ledger can never drive authority (it falls back to baseline).
        self._rehydrate_or_seed(ag_baseline or DEFAULT_AG_BASELINE)

    # -- persistence: rehydrate authority from the durable ledger -----------

    def _rehydrate_or_seed(self, baseline: Dict[str, float]) -> None:
        """Project AG authority from the durable EL (verify-gated); seed baseline only for
        classes with no durable history. Restore is SILENT (no re-audit) — the values were
        logged when first set; replaying them must not write phantom TRUST_CHANGE events."""
        history = self._authority_history()  # {} if empty OR if the chain failed to verify
        for cc, val in baseline.items():
            if cc in history:
                auth, earned = history[cc]
                self.ag.restore_authority(cc, auth, earned_in=earned)
            else:
                self.ag.set_authority(cc, val)  # first run for this class — seed (logged once)
        for cc, (auth, earned) in history.items():
            if cc not in baseline:
                self.ag.restore_authority(cc, auth, earned_in=earned)
        self._rehydrate_familiarity()

    def _rehydrate_familiarity(self) -> None:
        """Rebuild the familiarity projection from the durable EL grounded-outcome stream —
        verify_chain-gated (a tampered ledger drives NO familiarity → floor, fail-safe), same
        discipline as authority rehydration. Replays only HUMAN-GROUNDED-GOOD, domain-scoped
        outcome_verdict events (proxy never built familiarity, so there is nothing to replay)."""
        try:
            if not self.el.verify_chain():
                return
        except Exception:
            return
        records: List[tuple] = []
        try:
            for ev in self.el.query({"source_organ": "GOV",
                                     "action_type": "GOVERNANCE_DECISION", "limit": 1_000_000}):
                p = ev.get("payload") or {}
                if (p.get("outcome") == "outcome_verdict" and p.get("satisfied") is True
                        and p.get("satisfaction_source") == "human"
                        and p.get("domain") and p.get("observed_at") is not None):
                    records.append((p["domain"], float(p["observed_at"])))
        except Exception:
            return
        if records:
            self.ag.replay_familiarity(records)

    def _familiarity_inputs(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Familiarity gating inputs for an action, or {} when it is not domain-scoped. A
        domain-scoped action's effective authority = base × familiarity(domain), weighted by the
        domain's current KVE validity and discounted by per-volatility experience decay.
        FAIL-SAFE: a domain with no KVE knowledge artifact → validity 0.0 (unknown ≡ stale →
        floor) and unknown volatility → fastest decay — the most conservative familiarity."""
        domain = action.get("domain")
        if not domain:
            return {}
        now = self._domain_clock.now()
        try:
            validity = self.kve.confidence(domain, now=now)
        except Exception:
            validity = 0.0  # unknown knowledge validity = decayed (never fresh)
        return {"now": now, "validity": validity, "volatility": self.kve.volatility(domain)}

    def _authority_history(self) -> Dict[str, Any]:
        """Last authority per capability_class from the DURABLE EL — ONLY if the chain
        verifies. A tampered durable ledger MUST NOT drive authority (the new obligation
        durable state creates): on verify failure we log an integrity alarm and return {},
        so the kernel falls back to the low-trust baseline (fail-safe = contraction)."""
        try:
            if not self.el.verify_chain():
                self._best_effort_log(
                    "integrity_alarm", {"action_id": "el-integrity"},
                    {"reason": "EL.verify_chain failed — authority NOT rehydrated; baseline only"})
                return {}
        except Exception:
            return {}  # cannot verify → do not project (fail-safe)
        last: Dict[str, Any] = {}
        try:
            # query returns seq DESC, so the first time we see a class is its latest value
            for ev in self.el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE",
                                     "limit": 1_000_000}):
                p = ev.get("payload") or {}
                cc = p.get("capability_class")
                if cc is not None and cc not in last and "authority" in p:
                    last[cc] = (float(p["authority"]), p.get("earned_in"))
        except Exception:
            return {}
        return last

    # -- EL helpers ---------------------------------------------------------

    def _emit(self, outcome: str, action: Dict[str, Any], extra: Dict[str, Any]) -> None:
        """Append a GOVERNANCE_DECISION event. Raises (RuntimeError) on EL failure."""
        self.el.append({
            "event_id": uuid.uuid4().hex,
            "timestamp": _now(),
            "source_organ": "GOV",
            "action_type": "GOVERNANCE_DECISION",
            "object_ids": [action.get("action_id", "action")],
            "payload": {
                "outcome": outcome,
                "tool_name": action.get("tool_name"),
                "capability_class": action.get("capability_class"),
                "inputs": action,
                **extra,
            },
            "evidence_confidence": 1.0,
            "evidence_source": "GOV",
            "prev_hash": "",
            "hash": "",
        })

    def _best_effort_log(self, outcome: str, action: Dict[str, Any],
                         extra: Dict[str, Any]) -> None:
        """Log a denial/gate. Denials are safe without a log (blocking is fail-safe),
        so a broken EL must NOT flip a deny into an allow — swallow EL errors here.
        The ALLOW path (and SAFE_READ) is what's gated on EL health."""
        try:
            self._emit(outcome, action, extra)
        except Exception:
            pass

    def _require_el_healthy(self) -> None:
        """A broken EL blocks even reads (can't audit ⇒ AURUM_ERR_011). Raises
        _SpineFault if EL is unavailable."""
        try:
            health = self.el.health()
        except Exception as e:  # health probe itself failed
            raise _SpineFault(f"EL health probe failed: {e}") from e
        if not health.get("available", False):
            raise _SpineFault("EL reports unavailable")

    # -- fail-safe outcomes -------------------------------------------------

    def _fail_closed(self, reason: str, action: Dict[str, Any]) -> GovernanceDecision:
        self._best_effort_log("fail_closed", action, {"reason": reason})
        return GovernanceDecision(allow=False, reason=f"fail-closed: {reason}",
                                  rule_id="gov:fail-closed", degraded=True)

    def _degrade(self, reason: str, action: Dict[str, Any], tier: str) -> GovernanceDecision:
        # Loudly flag the degraded state. If even this log fails, EL is down too →
        # escalate to full fail-closed (can't log ⇒ can't proceed, even a read).
        try:
            self._emit("degraded", action, {"reason": reason, "tier": tier})
        except Exception as e:
            return self._fail_closed(f"degraded + EL unavailable: {e}", action)
        if tier == SAFE_READ:
            return GovernanceDecision(allow=True, reason=f"degraded-readonly: {reason}",
                                      degraded=True)
        return GovernanceDecision(
            allow=False, degraded=True, rule_id="gov:degraded",
            reason=f"degraded to read-only ({reason}); {tier} blocked")

    # -- main entry ---------------------------------------------------------

    def govern(self, action: Dict[str, Any]) -> GovernanceDecision:
        """Route one action through the spine. Never raises — every failure path maps
        to a tiered fail-safe decision."""
        tier = action.get("_risk_tier") or risk_tier(action.get("tool_name", ""))
        try:
            return self._govern_inner(action, tier)
        except _SpineFault as e:
            return self._fail_closed(str(e), action)
        except _PeripheralFault as e:
            return self._degrade(str(e), action, tier)
        except Exception as e:  # unexpected orchestration bug → degrade (peripheral)
            return self._degrade(f"orchestration fault: {e}", action, tier)

    def _govern_inner(self, action: Dict[str, Any], tier: str) -> GovernanceDecision:
        # 1. PK single-action check (injection boundary + rule table). PK fault ⇒ spine.
        try:
            pk_result = self.pk.check(action)
        except Exception as e:
            raise _SpineFault(f"PK.check failed: {e}") from e

        if pk_result["decision"] == "deny":
            self._best_effort_log("deny", action,
                                  {"rule_id": pk_result["rule_id"], "reason": pk_result["reason"]})
            return GovernanceDecision(allow=False, reason=pk_result["reason"],
                                      rule_id=pk_result["rule_id"])

        # 2. SAFE_READ short-circuit — no CA, no EL write (don't bloat the ledger on
        #    grep/read). But a broken EL must still block even reads (AURUM_ERR_011).
        if tier == SAFE_READ and pk_result["decision"] == "allow":
            self._require_el_healthy()  # raises _SpineFault ⇒ full fail-closed
            return GovernanceDecision(allow=True, reason="safe-read")

        # 3. Chain check — within-turn padding-resistant taint (007/009). PK fault ⇒ spine.
        try:
            self._action_log.append(action)
            chain = self.pk.check_chain(self._action_log, {})
        except Exception as e:
            raise _SpineFault(f"PK.check_chain failed: {e}") from e

        if chain["decision"] == "deny":
            self._best_effort_log("deny", action,
                                  {"rule_id": chain["rule_id"], "reason": chain["reason"]})
            return GovernanceDecision(allow=False, reason=chain["reason"],
                                      rule_id=chain["rule_id"])

        # 4. needs_gate (single-action or chain). Within-turn: block + log the request.
        if pk_result["decision"] == "needs_gate" or chain["decision"] == "needs_gate":
            gate = {"rule_id": pk_result["rule_id"], "capability_class":
                    action.get("capability_class"), "requested_at": _now()}
            self._best_effort_log("needs_gate", action, {"gate": gate})
            return GovernanceDecision(allow=False, reason="needs_gate (human approval required)",
                                      rule_id=pk_result["rule_id"], gate=gate)

        # 5. AG ceiling — AG computes it, PK/kernel enforces. AG fault ⇒ peripheral.
        #    Familiarity (Phase C): a DOMAIN-scoped action is gated on its EFFECTIVE band
        #    (base × familiarity) — stricter than base, so an unfamiliar/stale domain tightens
        #    the gate even when base authority is high. Non-domain actions use the base band.
        fam = self._familiarity_inputs(action)
        try:
            permitted = self.ag.permits(action, **fam)
        except Exception as e:
            raise _PeripheralFault(f"AG.permits failed: {e}") from e
        if not permitted:
            cc = action.get("capability_class", "default")
            band = (self.ag.effective_band(cc, action.get("domain", "unknown"), **fam)
                    if fam else self.ag.band(cc))
            self._best_effort_log("deny", action,
                                  {"rule_id": "ag:ceiling", "band": band,
                                   "familiarity_gated": bool(fam),
                                   "domain": action.get("domain")})
            return GovernanceDecision(
                allow=False, rule_id="ag:ceiling",
                reason=f"ag-ceiling: '{cc}' requires higher authority "
                       f"(band={band}); raise authority to permit")

        # 6. CA arbitration. CA fault ⇒ peripheral.
        try:
            ca_result = self.ca.arbitrate(action, self._ca_signals(action, pk_result))
        except Exception as e:
            raise _PeripheralFault(f"CA.arbitrate failed: {e}") from e
        resolution = ca_result.get("resolution", "proceed")

        # 7. EL fail-safe — write-then-act. EL fault ⇒ spine (full fail-closed).
        try:
            self._emit(resolution, action,
                       {"ca_winner": ca_result.get("winner"), "pk_decision": pk_result["decision"]})
        except Exception as e:
            raise _SpineFault(f"EL.append failed (write-then-act): {e}") from e

        return GovernanceDecision(allow=(resolution == "proceed"),
                                  reason=f"ca:{resolution}", rule_id=ca_result.get("winner"))

    def _ca_signals(self, action: Dict[str, Any],
                    pk_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        cc = action.get("capability_class", "default")
        return [
            {"signal": "PK", "directive": "proceed",
             "basis": {"rule_id": pk_result.get("rule_id"), "decision": pk_result["decision"]},
             "constitutional": True},
            {"signal": "AG", "directive": "proceed",
             "basis": {"authority": self.ag.authority(cc), "band": self.ag.band(cc)},
             "constitutional": False},
        ]

    # -- ingestion (SEN) ----------------------------------------------------

    def ingest(self, source: str, payload: Any = None, **fields: Any) -> Dict[str, Any]:
        """Integrated ingestion: feed inbound content through the Sensorium (which tags it
        UNTRUSTED with `source` provenance and rewrites any spoofed 'operator' claim) and record
        the untrusted source for this turn. The returned event's `justification_sources` carry the
        untrusted source — pass it to `action_map.action_from_event` so any derived action is
        denied binding by PK's injection boundary (AURUM_ERR_008) at govern()."""
        event = self.sen.on_event({"source": source, "payload": payload, **fields})
        self._ingested_untrusted.add(event.get("source"))
        return event

    # -- failure capture (post-tool-call) -----------------------------------

    def record_failure(self, action: Dict[str, Any], error: str) -> None:
        """Write a redacted postmortem to BB on a consequential tool error
        (failure → durable-fix loop). Best-effort; never raises."""
        try:
            self.bb.write({
                "id": uuid.uuid4().hex,
                "title": f"tool failure: {action.get('tool_name')}",
                "summary": error,
                "action": action,
            })
        except Exception:
            pass

    # -- the OI → BB → AG outcome loop (post-action; ASYMMETRIC) -------------
    # Demote is a reflex (proxy is enough — contraction is safe). Promotion is NEVER
    # driven from here on a proxy signal; it requires a human-grounded verdict via
    # record_outcome_verdict(). See outcome_gated_authority_design.md. This loop is a
    # LEARNING loop, NOT the first-instance firewall (that's cage + PK + taint).

    def _classify_failure(self, action: Dict[str, Any], task_result: Dict[str, Any],
                          verdict: Dict[str, Any]) -> tuple[str, str]:
        """Classify a BAD outcome as a TASK failure (one-band nudge) or a GOVERNANCE failure
        (floor immediately). Governance = an explicit "must never" breach surfaced in the
        outcome — a `governance_violation` marker in a known class, or an OI signal flagging
        a constitutional preference violation. Otherwise it's a task failure."""
        gv = task_result.get("governance_violation")
        if isinstance(gv, str) and gv in _GOVERNANCE_FAILURE_CLASSES:
            return "governance", gv
        # OI preference-violation signals naming a governance class also escalate
        for v in (verdict.get("signals", {}) or {}).get("preference_violations", []) or []:
            if isinstance(v, str) and v in _GOVERNANCE_FAILURE_CLASSES:
                return "governance", v
        return "task", "task_failure"

    def severity_evidence(self) -> Dict[str, int]:
        """Per-class fire counts for the severity rules (this process). Usage evidence: a
        class that never fires is a review/redundancy candidate; a hot one is load-bearing."""
        return dict(self._severity_hits)

    def observe_outcome(self, action: Dict[str, Any],
                        task_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Proxy outcome path, run AFTER a (governed) action executed.

        OI interprets the result (proxy); a BAD outcome reflexively DEMOTES the action's
        capability_class in AG and banks the lesson to BB. A GOOD (proxy) outcome is HELD —
        proxy success NEVER promotes (the forbidden-feedback guard). Returns the OI verdict
        (or None if the loop could not run). Best-effort; never raises — the action already
        happened, so a failure here must not crash the turn, only skip the learning update."""
        try:
            cc = action.get("capability_class", "default")
            env = action.get("environment")
            goal_id = str(action.get("goal_id") or action.get("action_id") or "task")
            tr = dict(task_result)
            tr.setdefault("capability_class", cc)
            tr.setdefault("task_id", action.get("action_id"))
            verdict = self.oi.interpret(tr, goal_id)  # proxy; auto-banks completed-unsatisfied → BB
            # decision_id links the outcome event and the resulting TRUST_CHANGE so a replay
            # reconstructs WHY authority moved (criterion 5), not merely THAT it moved.
            decision_id = uuid.uuid4().hex
            # SEVERITY first, INDEPENDENT of proxy-satisfied: a governance breach (credential
            # exfil, tenant breach) often "succeeds" by the proxy measure — the most dangerous
            # outcomes are the ones that pass. So a governance violation is BAD regardless of
            # verdict["satisfied"]; a task failure is bad when the proxy says unsatisfied.
            severity, severity_class = self._classify_failure(action, tr, verdict)
            is_bad = (severity == "governance") or (not verdict["satisfied"])
            if is_bad:
                self._severity_hits[severity_class] = self._severity_hits.get(severity_class, 0) + 1
                cause = {
                    "decision_id": decision_id, "trigger": "outcome_demote",
                    "classified": "bad", "severity": severity, "severity_class": severity_class,
                    "satisfaction_source": "proxy",
                    "tool_name": action.get("tool_name"), "action_id": action.get("action_id"),
                    "quality": verdict["quality"], "goal_id": goal_id,
                }
                # reflex demote — proxy is sufficient to contract (safe direction)
                self.ag.apply_outcome(cc, good=False, grounded=False, environment=env,
                                      severity=severity, cause=cause)
                # Bank a BB lesson for a hard failure (OI's completed-unsatisfied hook misses
                # not-completed) AND for EVERY governance breach — a "must never" that happened
                # is the most important thing to remember, even though it "succeeded" by proxy.
                if not verdict["completed"] or severity == "governance":
                    summary = (f"GOVERNANCE breach: {severity_class}" if severity == "governance"
                               else str(tr.get("error") or "task not completed"))
                    self.record_failure(action, summary)
                self._best_effort_log("outcome_demote", action,
                                      {"decision_id": decision_id, "quality": verdict["quality"],
                                       "classified": "bad", "severity": severity,
                                       "severity_class": severity_class, "band": self.ag.band(cc)})
            else:
                # good PROXY outcome → HOLD. Promotion waits for a human-grounded verdict.
                self._best_effort_log("outcome_hold", action,
                                      {"decision_id": decision_id, "quality": verdict["quality"],
                                       "note": "proxy-good; authority unchanged (no promote on proxy)"})
            return verdict
        except Exception:
            return None

    def record_outcome_verdict(self, task_id: str, capability_class: str,
                               satisfied: bool, *, environment: Optional[str] = None,
                               domain: Optional[str] = None) -> None:
        """Ground-truth (out-of-loop / human) outcome path. This is the ONLY path that may
        PROMOTE authority. A grounded GOOD verdict slow-promotes the class; a grounded BAD
        verdict demotes it (reinforcing the reflex). Best-effort; never raises.

        FAMILIARITY (Phase C): a grounded-GOOD verdict in a declared `domain` also builds that
        domain's familiarity — the ONLY path that does (proxy never reaches here; a BAD verdict
        never builds). This is also the recovery path (AURUM_ERR_032): at the floor, a gated
        human-grounded-good outcome still adds full-weight experience, so a domain is never
        permanently bricked. The (domain, observed_at) is logged so the projection rehydrates."""
        try:
            self.oi.record_human_verdict(task_id, {"satisfied": bool(satisfied)})
            decision_id = uuid.uuid4().hex
            observed_at = self._domain_clock.now()   # DOMAIN time (injectable; real in prod)
            cause = {
                "decision_id": decision_id,
                "trigger": "outcome_verdict",
                "classified": "good" if satisfied else "bad",
                "satisfaction_source": "human",     # the ground-truth, out-of-loop signal
                "task_id": task_id, "capability_class": capability_class, "domain": domain,
            }
            self.ag.apply_outcome(capability_class, good=bool(satisfied), grounded=True,
                                  environment=environment, cause=cause)
            # Durable FIRST (the EL grounded-outcome event is the source of truth that
            # rehydration replays), THEN the in-memory familiarity projection.
            self._best_effort_log(
                "outcome_verdict",
                {"action_id": task_id, "capability_class": capability_class},
                {"decision_id": decision_id, "satisfied": bool(satisfied),
                 "satisfaction_source": "human",
                 "authority": self.ag.authority(capability_class),
                 "domain": domain, "observed_at": observed_at,
                 "volatility": self.kve.volatility(domain) if domain else None})
            if satisfied and domain:
                self.ag.record_familiarity(domain, observed_at)
        except Exception:
            pass

    def why_authority(self, capability_class: str) -> Optional[Dict[str, Any]]:
        """Replay the WHY of the latest authority change for a class — reconstructed from the
        durable, verifiable ledger: before→after band/value + the triggering outcome (its
        classification, source, and evidence refs). Answers 'why did AG move?', not just
        'that it moved' (the criterion-5 replay prize). `cause.decision_id` joins to the
        GOVERNANCE_DECISION outcome event in the same ledger."""
        try:
            for ev in self.el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE",
                                     "limit": 1_000_000}):
                p = ev.get("payload") or {}
                if p.get("capability_class") == capability_class:
                    return {"capability_class": capability_class,
                            "from": p.get("prev_authority"), "to": p.get("authority"),
                            "band": p.get("band"), "environment": p.get("environment"),
                            "cause": p.get("cause")}
        except Exception:
            return None
        return None

    def why_authority_chain(self, capability_class: str) -> List[Dict[str, Any]]:
        """The FULL why-chain: every authority level for a class, oldest→newest, each joined to
        the outcome event that caused it (via `cause.decision_id` → the GOVERNANCE_DECISION
        outcome event in the same ledger). This is the criterion-5 prize made complete — any
        authority level can be explained by replaying the events that produced it, with the
        triggering outcome attached, not merely the latest move. Empty list on a cold/empty
        ledger (clean no-op). Best-effort; never raises."""
        try:
            # Index outcome events (observe_outcome / record_outcome_verdict log a decision_id).
            outcomes: Dict[str, Dict[str, Any]] = {}
            for ev in self.el.query({"source_organ": "GOV",
                                     "action_type": "GOVERNANCE_DECISION", "limit": 1_000_000}):
                did = (ev.get("payload") or {}).get("decision_id")
                if did and did not in outcomes:
                    outcomes[did] = ev
            chain: List[Dict[str, Any]] = []
            for ev in self.el.query({"source_organ": "AG", "action_type": "TRUST_CHANGE",
                                     "capability_class": capability_class, "limit": 1_000_000}):
                p = ev.get("payload") or {}
                cause = p.get("cause") or {}
                did = cause.get("decision_id")
                chain.append({"from": p.get("prev_authority"), "to": p.get("authority"),
                              "band": p.get("band"), "cause": cause,
                              "outcome_event": outcomes.get(did) if did else None})
            chain.reverse()  # oldest→newest
            return chain
        except Exception:
            return []
