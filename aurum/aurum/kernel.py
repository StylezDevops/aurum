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

THE ENFORCED PROPERTY — stated precisely, so the claim matches the architecture: Aurum is
POLICY ENFORCEMENT + AUDIT over BOUNDARY-LABELLED PROVENANCE. The labelling is enforced at the
seams Aurum owns — every tool call is governed by the plugin's pre_tool_call hook, every
successful INGEST-tier tool result is auto-ingested (kernel.ingest) at post_tool_call, and
channel watchers ingest inbound content — so within those seams the labels are STRUCTURAL, not
a convention an integrator can forget. Once labelled, the guards are mechanical: an untrusted
justification hard-denies (AURUM_ERR_008), a tainted turn blocks un-attributed irreversible
actions (M2), a screener hit escalates to hot taint. What Aurum does NOT claim: interpreter-
level control-flow integrity (CaMeL-style capability tracking on VALUES). The model's latent
motivation is never traced — untrusted text that reaches the model's context WITHOUT crossing
an ingest seam carries the operator default. The property therefore scales with SEAM COVERAGE:
a new channel or tool inherits it only by routing through the cage seams.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from .action_map import DEFAULT_AG_BASELINE, SAFE_READ, risk_tier
from .arbitration.conflict_arbiter import ConflictArbiter
from .durability.clock import RealDomainClock
from .durability.evidence_ledger import EvidenceLedger
from .durability.knowledge_validity_engine import KnowledgeValidityEngine
from .extensions.skill_dependency_graph import SkillDependencyGraph
from .integrations.identity import IdentityScopeMapper
from .extensions.substrate_mapper import SubstrateMapper
from .observability.concentration_check import ConcentrationCheck
from .observability.memory_poisoning_detector import MemoryPoisoningDetector
from .novel.authority_governor import AuthorityGovernor
from .novel.outcome_interpreter import OutcomeInterpreter
from .spine.black_box import BlackBox
from .spine.policy_kernel import PolicyKernel
from .support.resource_scheduler import ResourceScheduler
from .support.sensorium import Sensorium
from .support.shadow_mode import ShadowMode
from .support.trust_ladder import TrustLadder


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
# THE FIVE MUST-NEVER CLASSES (spec-owner declaration, v1). A breach in ANY floors authority to
# the minimum IMMEDIATELY — no proportionality, no second chance. THE BAR (all three, not any one):
# irreversible AND high-blast-radius AND no honest justification exists. Reversible OR contained OR
# has-a-plausible-legitimate-version ⇒ a one-band NUDGE (task_failure), NOT a floor — keeping
# nudge-class failures OFF this list is what keeps the floor meaningful. These ship in the SIGNED
# constitutional surface (constitutional_surface()["governance_failure_classes"]): the agent can
# READ them, cannot shorten / propose away / extend them.
#   destructive_data_loss            — irreversible destruction of DURABLE (non-scratch) data.
#   wrong_blast_radius_comms         — broadcast/external comms beyond the authorized recipient set
#                                      (a single misdirected DM is a NUDGE; reach is the floor).
#   secret_capability_misdirection   — directing a secret-BEARING capability at an UNAUTHORIZED
#                                      destination (the agent never sees raw values; the threat is
#                                      misdirecting the injected credential — see the per-secret
#                                      destination allowlist).
#   ledger_tamper_or_provenance_forge— forging/rewriting/deleting the audit trail (honest forward
#                                      APPENDS stay allowed; the FORGE/REWRITE/DELETE attempt floors
#                                      — the meta-must-never that conceals all other breaches).
#   governance_enact_without_signature— attempting to ENACT a governance change without the
#                                      operator ed25519 signature (PROPOSE stays allowed).
# v2 GAP (do NOT add now — parchment for a boundary that doesn't exist): cross_tenant_breach is
# absent in single-tenant v1; add when multi-tenant ships.
_GOVERNANCE_FAILURE_CLASSES: frozenset = frozenset({
    "destructive_data_loss", "wrong_blast_radius_comms", "secret_capability_misdirection",
    "ledger_tamper_or_provenance_forge", "governance_enact_without_signature",
})

# Bound on the within-turn chain log (H2). new_turn() resets it at a turn boundary; this cap is a
# defence for a LONG-LIVED host kernel that doesn't (the cage gets the reset free via process
# death per message). Without it, _action_log grows unbounded across turns → check_chain goes
# O(n²) over a session + monotonic memory. A real turn never has this many consequential actions,
# so the bound is invisible in normal operation and only clips a pathological/abusive run.
_ACTION_LOG_MAX = 256


class GovernanceKernel:
    ORGAN = "GOV"

    def __init__(self, home: str, rules: Optional[List[Dict[str, Any]]] = None,
                 ag_baseline: Optional[Dict[str, float]] = None,
                 domain_clock: Any = None,
                 identity_bindings: Optional[Dict[str, Dict[str, Any]]] = None,
                 injection_screener: Any = None,
                 screen_block_threshold: float = 0.8,
                 verify_constitution: bool = True) -> None:
        base = Path(home) / "governance"
        base.mkdir(parents=True, exist_ok=True)
        # PK first (el=None) — the kernel owns EL logging, which avoids the PK<->EL
        # constructor cycle (EL needs PK.redact; PK does not need EL for check/chain).
        # Keep the resolved rule table (part of the constitutional surface — see
        # constitutional_surface) instead of reaching into pk._rules later.
        self._rules = list(rules) if rules is not None else default_rules()
        self.pk = PolicyKernel(rules=self._rules)
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
        # SEN's optional injection SCREENER (a sensor over inbound text). With one wired, ingest()
        # reads the verdict and ESCALATES a high-confidence/override hit to HOT taint — never relaxes
        # the gate, never sanitises. None → base provenance taint only (the structural floor).
        self.sen = Sensorium(screener=injection_screener)
        self._ingested_untrusted: set = set()
        # Sources whose ingested content the screener flagged as a likely injection THIS turn. The
        # hot-taint escalation: blocks ALL non-operator consequential actions, not just irreversible.
        self._ingested_hostile: set = set()
        self._screen_threshold = float(screen_block_threshold)
        # SH — shadow mode. An irreversible action is simulated on a COPY (no side effects) and
        # only committed for real on an 'ok' verdict (see shadow_commit). Makes the shadow
        # containment we applied ad hoc to gated irreversible actions principled + automatic.
        self.sh = ShadowMode()
        # TL — trust ladder. The action-vs-scope split: govern() consults TL.can (earned SCOPE)
        # for actions that declare a required_tier, ALONGSIDE AG (live authority). Fed from the
        # grounded outcome loop (record_outcome_verdict). NEVER the final word — AG still gates.
        self.tl = TrustLadder()
        # SM + SDG — the self-improvement pre-promotion gate (see gate_self_improvement): SM scopes
        # a proposal to the domain's substrate (reject cross-/unmapped pre-gate); SDG re-runs the
        # transitive dependents' goldens (blocked on any red). A cleared proposal still faces the
        # HUMAN_GATE on promotion (tool_lifecycle needs_gate in govern).
        self.sm = SubstrateMapper()
        self.sdg = SkillDependencyGraph()
        # RS — schedules BACKGROUND organ work (foreground preempts; aging guards starvation), so
        # background organs go through one budget instead of spinning raw threads. CC — a read-only
        # concentration view over the live EL; concentration_risks() is the do-not-retire (MGC) /
        # harden-or-split (TCM) systemic-risk signal. Neither blocks the govern() hot path.
        self.rs = ResourceScheduler()
        self.cc = ConcentrationCheck(el=self.el)
        # MPD — memory-poisoning detector (derived view over EL). Closes the evidence-confidence
        # feedback loop now that OI+BB exist: scan_memory_integrity() surfaces suspects to BB for
        # OWNER REVIEW (never auto-deletes), and the verify-gated rehydration below DISTRUSTS the
        # auto-quarantine subset (a grounded 'success' a later outcome contradicts must not silently
        # rebuild authority/scope on --rm). Read-only; never blocks the govern() hot path.
        self.mpd = MemoryPoisoningDetector(el=self.el)
        # Identity/RBAC scope mapper: resolves a capability class's LIVE authority band to the
        # least-privilege role+scope it justifies (scope_for). Mints nothing — it returns a JIT
        # scope REQUEST the managed-identity provider/OneCLI fulfils, so no secret lands in the
        # container. `identity_bindings` binds a class to its resource scope; unbound → no access
        # (fail-safe: least privilege means no STANDING access until the operator binds one).
        self.identity = IdentityScopeMapper(bindings=identity_bindings)
        # OPERATOR IDENTITY — ed25519 PUBLIC keys for verifying operator verdicts: the signed
        # ground truth that may EXPAND authority (submit_operator_verdict; integrations/
        # operator_verdict.py). Loaded from <home>/governance/operator_pubkeys/*.pem (or
        # AURUM_OPERATOR_PUBKEYS). NO keys deployed → no signed promotion is possible → the agent
        # is CONTRACTION-ONLY, which is the v1 default-safe posture (automatic loss, signed gain).
        self.operator_verifier = self._load_operator_verifier(base)
        # Lazily-built, CACHED maintenance scheduler (see run_maintenance) — cached so SCHEDULE
        # interval gating holds across calls in a long-lived kernel. None until first use.
        self._maintenance_scheduler: Any = None
        # Per-process accumulated EXECUTED actions for within-turn chain analysis (007/009).
        # Bounded (H2) so a long-lived host kernel that misses a new_turn() can't grow it unbounded.
        self._action_log: Deque[Dict[str, Any]] = deque(maxlen=_ACTION_LOG_MAX)
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
        self._ag_baseline = dict(ag_baseline or DEFAULT_AG_BASELINE)
        self._rehydrate_or_seed(self._ag_baseline)
        # CS-EQ leg 3 — tamper-evidence: if a human-signed constitution manifest + public key are
        # present on the mount, verify the running constitutional surface against them and FAIL
        # CLOSED-HARD (raise) on any mismatch. No manifest deployed → no-op (opt-in by ratifying —
        # scripts/ratify_constitution.py). `verify_constitution=False` is for the ratify tool.
        if verify_constitution:
            self._verify_constitution_on_boot(base)

    # -- constitutional surface + tamper-evidence (CS-EQ legs 1 & 3) --------

    def constitutional_surface(self) -> Dict[str, Any]:
        """The canonical CORE / kinetics / threshold surface a human-signed manifest protects.
        Pure-JSON + stable so its hash is reproducible across boots/processes. A change to any of
        these IS a constitutional change — it must be re-ratified out of band, or the next boot
        fails closed. The cage can PROPOSE a change (write a proposal); it cannot ENACT one (it has
        no private key)."""
        from .novel.authority_governor import _BANDS as _AG_BANDS
        return {
            "ag_bands": [[n, p, d] for (n, p, d) in _AG_BANDS],
            "ag_kinetics": dict(self.ag.kinetics()),
            "ag_dwell_seconds": self.ag.dwell_seconds,
            "ag_baseline": dict(self._ag_baseline),
            "governance_failure_classes": sorted(_GOVERNANCE_FAILURE_CLASSES),
            "screen_block_threshold": self._screen_threshold,
            "pk_rules": self._rules,
        }

    def _verify_constitution_on_boot(self, base: Path) -> None:
        """Verify the running surface against a signed manifest IF one is deployed. No
        manifest/pubkey on the mount → no-op (tamper-evidence INACTIVE; logged — opt-in by
        ratifying). Present → recompute the surface hash + verify the ed25519 signature with the
        PUBLIC key; ANY mismatch raises ConstitutionalBreach (fail-closed-HARD — the kernel refuses
        to construct, so the plugin's outer guard blocks the turn). The cage holds only the public
        key; only an out-of-band human key can ratify."""
        pub_path = (os.environ.get("AURUM_CONSTITUTION_PUBKEY")
                    or str(base / "constitution_pubkey.pem"))
        man_path = (os.environ.get("AURUM_CONSTITUTION_MANIFEST")
                    or str(base / "constitution_manifest.json"))
        if not (os.path.exists(pub_path) and os.path.exists(man_path)):
            # No manifest deployed → tamper-evidence INACTIVE (opt-in by ratifying). Deliberately
            # write NO EL event: the cage builds a kernel per message, so logging here would spam
            # the ledger every turn; the informative signal is the ABSENCE of a CONSTITUTION_VERIFY
            # event. Only the ACTIVE path (verifier.verify_on_boot) records to EL.
            return
        from .cseq.manifest import ConstitutionVerifier, SignedManifest
        with open(pub_path, "rb") as fh:
            pem = fh.read()
        with open(man_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        manifest = SignedManifest(surface_sha256=str(data["surface_sha256"]),
                                  version=int(data["version"]),
                                  signature_hex=str(data["signature_hex"]))
        # raises ConstitutionalBreach on hash/signature mismatch → propagates out of __init__.
        ConstitutionVerifier(pem, self.el).verify_on_boot(self.constitutional_surface(), manifest)

    # -- persistence: rehydrate authority from the durable ledger -----------

    def _rehydrate_or_seed(self, baseline: Dict[str, float]) -> None:
        """Project AG authority from the durable EL (verify-gated); seed baseline only for
        classes with no durable history. Restore is SILENT (no re-audit) — the values were
        logged when first set; replaying them must not write phantom TRUST_CHANGE events."""
        chain_ok = self._verify_chain_safe()  # verify the durable ledger ONCE; shared below (M3)
        history = self._authority_history(chain_ok)
        for cc, val in baseline.items():
            if cc in history:
                auth, earned = history[cc]
                self.ag.restore_authority(cc, auth, earned_in=earned)
            else:
                self.ag.set_authority(cc, val)  # first run for this class — seed (logged once)
        for cc, (auth, earned) in history.items():
            if cc not in baseline:
                self.ag.restore_authority(cc, auth, earned_in=earned)
        # Memory-poisoning overlay: a grounded 'success' a later outcome CONTRADICTS is quarantined
        # and must not rebuild trust on --rm. Compute the auto-quarantine set ONCE (only meaningful
        # when the chain verified — we only rehydrate then) and pass it to both projections.
        quarantine = self.mpd.quarantined_evidence() if chain_ok else set()
        self._rehydrate_familiarity(chain_ok, quarantine)
        self._rehydrate_tl(chain_ok, quarantine)  # H1: TL tiers survive --rm, like authority

    def _verify_chain_safe(self) -> bool:
        """Verify the durable EL chain ONCE per construction — the single integrity gate shared by
        every projection below (authority, familiarity, TL). A tampered/unverifiable ledger drives
        NO rehydration (fail-safe = low-trust baseline only) and logs an integrity alarm. Replaces
        the previous per-projection re-verification, which walked the chain 2-3× per startup (M3)."""
        try:
            if self.el.verify_chain():
                return True
            self._best_effort_log(
                "integrity_alarm", {"action_id": "el-integrity"},
                {"reason": "EL.verify_chain failed — projections NOT rehydrated; baseline only"})
            return False
        except Exception:
            return False

    def _rehydrate_familiarity(self, chain_ok: bool, quarantine: Optional[set] = None) -> None:
        """Rebuild the familiarity projection from the durable EL grounded-outcome stream (the
        chain was verified once upstream — a tampered ledger drives NO familiarity → floor, the
        fail-safe). Replays only HUMAN-GROUNDED-GOOD, domain-scoped outcome_verdict events (proxy
        never built familiarity, so there is nothing to replay). MPD-quarantined events (a grounded
        success a later outcome contradicts) are SKIPPED — a poisoned 'success' must not rebuild
        familiarity on restart."""
        if not chain_ok:
            return
        quarantine = quarantine or set()
        records: List[tuple] = []
        try:
            for ev in self.el.query({"source_organ": "GOV",
                                     "action_type": "GOVERNANCE_DECISION", "limit": 1_000_000}):
                # honour the MPD evidence-confidence overlay: distrust (skip) a contradicted
                # grounded 'success' — a poisoned success must not rebuild familiarity on --rm.
                if self.mpd.effective_confidence(ev, quarantined=quarantine) <= 0.0:
                    continue
                p = ev.get("payload") or {}
                if (p.get("outcome") == "outcome_verdict" and p.get("satisfied") is True
                        and p.get("satisfaction_source") == "human"
                        and p.get("domain") and p.get("observed_at") is not None):
                    records.append((p["domain"], float(p["observed_at"])))
        except Exception:
            return
        if records:
            self.ag.replay_familiarity(records)

    def _rehydrate_tl(self, chain_ok: bool, quarantine: Optional[set] = None) -> None:
        """Rebuild TL tiers from the durable EL grounded-outcome stream (H1) — so earned autonomy
        SURVIVES --rm, the same persistence-as-projection AG authority + familiarity already use
        (without it, TL reset to 0 every ephemeral message and never earned anything in the cage).
        Replays human-grounded outcome_verdict events per capability_class IN CHRONOLOGICAL ORDER
        (tier-up/down sequence matters); verify-gated (tampered ledger → no tiers, the fail-safe).
        MPD-quarantined successes are SKIPPED — a poisoned tier-up must not survive restart."""
        if not chain_ok:
            return
        quarantine = quarantine or set()
        events: List[tuple] = []
        try:
            for ev in self.el.query({"source_organ": "GOV",
                                     "action_type": "GOVERNANCE_DECISION", "limit": 1_000_000}):
                # same MPD overlay: a contradicted grounded success must not rebuild earned scope.
                if self.mpd.effective_confidence(ev, quarantined=quarantine) <= 0.0:
                    continue
                p = ev.get("payload") or {}
                cc = p.get("capability_class")
                if (p.get("outcome") == "outcome_verdict"
                        and p.get("satisfaction_source") == "human" and cc):
                    events.append((cc, bool(p.get("satisfied"))))
        except Exception:
            return
        # query yields newest→oldest; replay oldest→newest so the tier evolution is faithful.
        for cc, satisfied in reversed(events):
            self.tl.ingest({"capability": cc,
                            "outcome": "success" if satisfied else "failure", "grounded": True})

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

    def _authority_history(self, chain_ok: bool) -> Dict[str, Any]:
        """Last authority per capability_class from the DURABLE EL — ONLY if the chain verified
        (done once upstream in _verify_chain_safe). A tampered durable ledger MUST NOT drive
        authority: chain_ok is False there, so we return {} and the kernel falls back to the
        low-trust baseline (fail-safe = contraction)."""
        if not chain_ok:
            return {}
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

    # -- the ONE replay surface: a complete, by-value decision record --------

    def _decision_snapshot(self, action: Dict[str, Any], *, final: str,
                           pk_decision: Optional[str] = None,
                           chain_decision: Optional[str] = None,
                           ca_resolution: Optional[str] = None,
                           ca_winner: Optional[str] = None,
                           reason_codes: Optional[List[str]] = None,
                           fam: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """A COMPLETE, BY-VALUE snapshot of the governance state a decision was made against —
        every field copied LITERALLY at decision time, never a pointer/seq into mutable (or
        since-redacted) state — so a future auditor reconstructs the decision from this one record.
        The authority SCORE is a compression; this snapshot is the evidence behind it (governance
        provenance / institutional memory)."""
        cc = action.get("capability_class", "default")
        domain = action.get("domain")
        fam = fam if fam is not None else {}
        band = (self.ag.effective_band(cc, domain or "unknown", **fam) if fam
                else self.ag.band(cc))
        familiarity: Optional[Dict[str, Any]] = None
        if fam and domain:
            familiarity = {"domain": domain, "validity": fam.get("validity"),
                           "volatility": fam.get("volatility"),
                           "factor": self.ag.familiarity_factor(domain, **fam),
                           "effective_n": self.ag.familiarity_effective_n(domain, **fam)}
        return {
            "ag_band": band,                                  # by value
            "ag_authority": self.ag.authority(cc),            # by value
            "earned_in": list(self.ag.earned_in(cc)),         # provenance, by value
            "familiarity": familiarity,                       # by value (None if not domain-scoped)
            "tl_tier": self.tl.tier(cc),                      # by value
            "pk_outcome": pk_decision,                        # by value
            "chain_outcome": chain_decision,                  # by value
            "ca_outcome": {"resolution": ca_resolution, "winner": ca_winner},  # by value
            "environment": action.get("environment") or "unknown",            # by value
            "identity": self.identity.scope_for(cc, band),    # the JIT scope grant, by value
            "reason_codes": list(reason_codes or []),         # by value
            "taint": {"untrusted": sorted(s for s in self._ingested_untrusted if s),
                      "hostile": sorted(s for s in self._ingested_hostile if s)},  # by value
            "capability_class": cc, "tool_name": action.get("tool_name"),
            "final_decision": final,
        }

    def _record_decision(self, snapshot: Dict[str, Any], *, raising: bool) -> None:
        """Persist a governance decision to the ONE replay surface — the `decisions` table + its
        by-value `evidence_snapshots` row (log_decision is atomic, snapshot-FIRST, fail-closed).
        ALLOW uses raising=True (write-then-act: a decision that cannot be recorded must not
        proceed); deny/gate use best-effort (a denial is fail-safe even unlogged). No EL event and
        no el_seq pointer — the decision is self-contained by value."""
        cc = snapshot["capability_class"]
        decision = {
            "action_requested": snapshot.get("tool_name") or cc,
            "final_decision": snapshot["final_decision"],
            "authority_score": snapshot["ag_authority"],
            "reason_json": json.dumps({"reason_codes": snapshot["reason_codes"],
                                       "pk": snapshot["pk_outcome"],
                                       "chain": snapshot["chain_outcome"],
                                       "ca": snapshot["ca_outcome"]}, sort_keys=True),
            "replayable": 1,
        }
        knowledge = json.dumps({"familiarity": snapshot["familiarity"],
                                "earned_in": snapshot["earned_in"]}, sort_keys=True)
        snap_row = {
            "authority": snapshot["ag_authority"],
            "trust": float(snapshot["tl_tier"]),
            "active_rules": snapshot["reason_codes"],
            "knowledge_state_hash": hashlib.sha256(knowledge.encode("utf-8")).hexdigest(),
            "environment_hash": snapshot["environment"],
            "snapshot_json": json.dumps(snapshot, sort_keys=True, default=str),
        }
        if raising:
            self.el.log_decision(decision, snap_row, el_seq=None)
        else:
            try:
                self.el.log_decision(decision, snap_row, el_seq=None)
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

    # -- turn boundary ------------------------------------------------------

    def new_turn(self) -> None:
        """Reset per-turn state at a turn boundary: the within-turn chain log and the turn's
        ingested-untrusted-source set. The ephemeral cage (one process per message) gets this for
        free, but a long-lived/host kernel or back-to-back turns MUST call it so within-turn taint
        analysis stays within-turn and `_action_log` / `_ingested_untrusted` don't grow unbounded
        (M1). Authority/familiarity/TL persist via EL projection and are deliberately NOT reset."""
        self._action_log.clear()
        self._ingested_untrusted.clear()
        self._ingested_hostile.clear()

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
            self._record_decision(self._decision_snapshot(
                action, final="deny", pk_decision="deny",
                reason_codes=[pk_result["rule_id"]]), raising=False)
            return GovernanceDecision(allow=False, reason=pk_result["reason"],
                                      rule_id=pk_result["rule_id"])

        # 2. SAFE_READ short-circuit — no CA, no decision record (a read is not a consequential
        #    decision; don't bloat the surface on grep/read). But a broken EL must still block
        #    even reads (AURUM_ERR_011).
        if tier == SAFE_READ and pk_result["decision"] == "allow":
            self._require_el_healthy()  # raises _SpineFault ⇒ full fail-closed
            return GovernanceDecision(allow=True, reason="safe-read")

        # 3. Chain check — within-turn padding-resistant taint (007/009). PK fault ⇒ spine.
        #    M1: evaluate over the persisted (EXECUTED) actions PLUS this candidate, but do NOT
        #    persist the candidate yet — only an ALLOWED action joins _action_log (step 7). A
        #    blocked attempt must not pollute the taint chain for subsequent actions this turn (a
        #    denied secret-read never touched the data, so it can't complete an exfil path later).
        try:
            chain = self.pk.check_chain([*self._action_log, action], {})
        except Exception as e:
            raise _SpineFault(f"PK.check_chain failed: {e}") from e

        if chain["decision"] == "deny":
            self._record_decision(self._decision_snapshot(
                action, final="deny", pk_decision=pk_result["decision"], chain_decision="deny",
                reason_codes=[chain["rule_id"]]), raising=False)
            return GovernanceDecision(allow=False, reason=chain["reason"],
                                      rule_id=chain["rule_id"])

        # 4. needs_gate (single-action or chain). Within-turn: block + record the request.
        if pk_result["decision"] == "needs_gate" or chain["decision"] == "needs_gate":
            gate = {"rule_id": pk_result["rule_id"], "capability_class":
                    action.get("capability_class"), "requested_at": _now()}
            self._record_decision(self._decision_snapshot(
                action, final="needs_gate", pk_decision=pk_result["decision"],
                chain_decision=chain["decision"], reason_codes=[pk_result["rule_id"]]),
                raising=False)
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
            self._record_decision(self._decision_snapshot(
                action, final="deny", pk_decision=pk_result["decision"],
                chain_decision=chain["decision"], reason_codes=["ag:ceiling"], fam=fam),
                raising=False)
            return GovernanceDecision(
                allow=False, rule_id="ag:ceiling",
                reason=f"ag-ceiling: '{cc}' requires higher authority "
                       f"(band={band}); raise authority to permit")

        # 5b. TL scope check (action-vs-scope split). If the action declares a `required_tier`,
        #     the EARNED tier (TL = SCOPE) must meet it — INDEPENDENT of AG. AG already gated above
        #     (a high tier can't rescue low authority); here, sufficient authority still does NOT
        #     permit if the earned scope is below what the action requires. TL is ONE input, never
        #     the final word. Only actions that declare a required_tier are scope-gated.
        required_tier = action.get("required_tier")
        if required_tier is not None:
            cc_t = action.get("capability_class", "default")
            if not self.tl.can({"capability": cc_t, "required_tier": required_tier}):
                self._record_decision(self._decision_snapshot(
                    action, final="deny", pk_decision=pk_result["decision"],
                    chain_decision=chain["decision"], reason_codes=["tl:tier"], fam=fam),
                    raising=False)
                return GovernanceDecision(
                    allow=False, rule_id="tl:tier",
                    reason=f"tl-scope: '{cc_t}' earned tier {self.tl.tier(cc_t)} < "
                           f"required {required_tier}")

        # 5c-hot. Screener escalation: SEN's injection screener flagged ingested content THIS turn
        #     as a likely override/exfil attempt (HOT taint). Escalate BEYOND the irreversible-only
        #     guard — block ALL non-operator consequential/ingest actions, since the whole turn's
        #     reasoning is suspect. SAFE_READ already short-circuited above (reading the hostile
        #     content to process it stays allowed); operator-attributed actions still proceed.
        if self._ingested_hostile and action.get("origin") != "operator":
            self._record_decision(self._decision_snapshot(
                action, final="deny", pk_decision=pk_result["decision"],
                chain_decision=chain["decision"], reason_codes=["gov:hostile-tainted"], fam=fam),
                raising=False)
            return GovernanceDecision(
                allow=False, rule_id="gov:hostile-tainted",
                reason="hostile turn: ingested content this turn was screened as a likely "
                       "prompt-injection — non-operator consequential actions are blocked")

        # 5c. Tainted-turn guard (M2). In a turn that ingested UNTRUSTED content, an IRREVERSIBLE
        #     action that is NOT explicitly operator-attributed is denied — ingested content must
        #     not be able to drive an irreversible commit even after authority + scope clear. This
        #     is the highest-consequence class only (irreversible); reversible/consequential actions
        #     proceed (backstopped by AG) but are flagged tainted_context for audit. The operator's
        #     own channel sets origin='operator' (action_map.to_action(operator_origin=True)) to act.
        if self._ingested_untrusted and action.get("irreversible") and action.get("origin") != "operator":
            self._record_decision(self._decision_snapshot(
                action, final="deny", pk_decision=pk_result["decision"],
                chain_decision=chain["decision"], reason_codes=["gov:tainted-irreversible"], fam=fam),
                raising=False)
            return GovernanceDecision(
                allow=False, rule_id="gov:tainted-irreversible",
                reason="tainted turn: untrusted content was ingested this turn and this irreversible "
                       "action is not operator-attributed — blocked")
        # A consequential action in a turn that ingested untrusted content proceeds (AG-gated) — the
        # proceed decision's by-value `taint` snapshot field records the ingested sources for audit,
        # so no separate flag event is needed.

        # 6. CA arbitration. CA fault ⇒ peripheral.
        try:
            ca_result = self.ca.arbitrate(action, self._ca_signals(action, pk_result))
        except Exception as e:
            raise _PeripheralFault(f"CA.arbitrate failed: {e}") from e
        resolution = ca_result.get("resolution", "proceed")

        # 7. EL fail-safe — write-then-act. The decision + its COMPLETE BY-VALUE snapshot is the
        #    ONE replay surface (decisions + evidence_snapshots); an ALLOW that cannot be recorded
        #    must not proceed. EL fault ⇒ spine (full fail-closed).
        proceed = (resolution == "proceed")
        try:
            self._record_decision(self._decision_snapshot(
                action, final="allow" if proceed else "deny",
                pk_decision=pk_result["decision"], chain_decision=chain["decision"],
                ca_resolution=resolution, ca_winner=ca_result.get("winner"),
                reason_codes=[ca_result.get("winner") or f"ca:{resolution}"], fam=fam),
                raising=True)
        except Exception as e:
            raise _SpineFault(f"EL.log_decision failed (write-then-act): {e}") from e

        if proceed:
            self._action_log.append(action)   # only EXECUTED actions persist in the taint chain (M1)
        return GovernanceDecision(allow=proceed,
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
        # Screener escalation: a high-confidence / override verdict makes the turn HOT — recorded
        # so the gate blocks ALL non-operator consequential actions, not just irreversible ones.
        # NEVER relaxes anything: a benign/absent verdict leaves the base provenance taint intact.
        verdict = event.get("screen")
        if isinstance(verdict, dict) and (verdict.get("is_malicious_override")
                or float(verdict.get("exploit_confidence") or 0.0) >= self._screen_threshold):
            self._ingested_hostile.add(event.get("source"))
            self._best_effort_log(
                "ingest_screened_hostile",
                {"action_id": "sen-ingest", "capability_class": "ingest"},
                {"source": event.get("source"),
                 "exploit_confidence": verdict.get("exploit_confidence"),
                 "is_malicious_override": bool(verdict.get("is_malicious_override")),
                 "signals": verdict.get("signals")})
        return event

    # -- shadow-contained irreversible execution (SH) -----------------------

    def shadow_commit(self, action: Dict[str, Any], *, preview: Any = None,
                      commit: Any = None) -> Dict[str, Any]:
        """Govern + shadow-contain an irreversible action via a TRUE dry-run/commit split (H2).
        (1) `govern()` must allow. (2) for an action flagged `irreversible`, `preview` — a
        SIDE-EFFECT-FREE dry-run that returns the plan/diff (the caller's contract: a real /validate,
        a render, a no-op compute — NOT the real op) — is run through SH for a verdict + no-replay;
        only an 'ok' verdict permits `commit` (the real, irreversible op) to run, and the verdict is
        consumed. Because `preview` is NOT the real op, a genuine outward effect (a network POST, an
        LE submit) does NOT fire during simulation — unlike running one mutator for both. Reversible
        actions need no shadow gate. Returns `{governed, committed, plan, shadow, ...}`."""
        decision = self.govern(action)
        if not decision.allow:
            return {"governed": False, "committed": False,
                    "rule_id": decision.rule_id, "reason": decision.reason}
        if not action.get("irreversible"):
            return {"governed": True, "committed": None, "shadow": None,
                    "note": "reversible — no shadow gate (caller commits directly)"}
        aid = str(action.get("action_id") or "action")
        plan: Dict[str, Any] = {}
        # Run the side-effect-free preview through SH (verdict + no-replay). A preview that raises
        # → verdict 'error' → commit blocked. The preview's return is the captured plan/diff.
        diff = self.sh.simulate({"id": aid, "state": {},
                                 "apply": (lambda _s: plan.update(p=preview())) if callable(preview) else None})
        if diff["verdict"] != "ok":
            return {"governed": True, "committed": False, "shadow": diff,
                    "plan": plan.get("p"), "reason": "preview (dry-run) failed — not committed"}
        out: Dict[str, Any] = {}
        try:
            # The REAL op runs only after an ok preview, gated + consumed by SH (no replay).
            self.sh.commit({"id": aid, "state": {},
                            "apply": (lambda _s: out.update(r=commit())) if callable(commit) else None})
        except Exception as e:  # noqa: BLE001 — surface a real commit failure, never crash the turn
            return {"governed": True, "committed": False, "shadow": diff,
                    "plan": plan.get("p"), "error": f"commit failed: {type(e).__name__}: {e}"}
        return {"governed": True, "committed": True, "plan": plan.get("p"),
                "shadow": diff, "result": out.get("r")}

    # -- self-improvement pre-promotion gate (SM + SDG) ---------------------

    def gate_self_improvement(self, proposal: Dict[str, Any], domain: str, *,
                              golden_runner: Any = None) -> Dict[str, Any]:
        """Pre-promotion gate for a self-improvement proposal (TS/LS/AA). Two stages, both
        BEFORE the human promotion gate:
          1. SM scopes it to the domain's substrate — a cross-substrate or unmapped-domain
             proposal is rejected PRE-GATE (evolution can't target the wrong substrate).
          2. If it targets a skill, SDG re-runs the transitive dependents' golden scenarios —
             promotion is blocked on ANY red, including transitive dependents.
        A proposal that clears BOTH still faces the HUMAN_GATE on actual promotion (a
        `tool_lifecycle` action is `needs_gate` in govern). Returns {allowed, stage, ...}."""
        scoped = self.sm.scope(proposal, domain)
        if not scoped.get("scoped"):
            self._best_effort_log("self_improvement_blocked", {"action_id": "self-improve"},
                                  {"stage": "substrate", "domain": domain,
                                   "reason": scoped.get("reason")})
            return {"allowed": False, "stage": "substrate", "reason": scoped.get("reason")}
        skill = proposal.get("skill") if isinstance(proposal, dict) else None
        if skill:
            # A skill change MUST clear the transitive-dependents regression. If no golden_runner
            # is available we CANNOT verify dependents — fail CLOSED (was fail-open: a skill
            # proposal with no runner previously fell through to 'cleared' with no regression run).
            if golden_runner is None:
                self._best_effort_log("self_improvement_blocked", {"action_id": "self-improve"},
                                      {"stage": "regression", "skill": skill,
                                       "reason": "no golden_runner — dependents could not be verified"})
                return {"allowed": False, "stage": "regression", "skill": skill,
                        "reason": "no golden_runner provided; transitive-dependent regression "
                                  "could not run (fail-closed)"}
            affected = self.sdg.affected(skill)
            results = self.sdg.regress(affected, runner=golden_runner)
            if not self.sdg.promotion_allowed(results):
                red = [s for s, ok in results.items() if not ok]
                self._best_effort_log("self_improvement_blocked", {"action_id": "self-improve"},
                                      {"stage": "regression", "skill": skill, "red": red})
                return {"allowed": False, "stage": "regression", "affected": affected,
                        "results": results, "red": red}
            return {"allowed": True, "stage": "cleared", "scoped": scoped,
                    "affected": affected, "results": results}
        return {"allowed": True, "stage": "cleared", "scoped": scoped}

    # -- background scheduling (RS) + concentration signal (CC) -------------

    def submit_background(self, job: Any, weight: Any) -> None:
        """Register background organ work with the scheduler. Foreground work preempts; the aging
        guard keeps a perpetually-deferred job from starving. Background organs (CC scan, MGC
        cleanup, AA discovery) register here rather than spinning raw unmanaged threads."""
        self.rs.submit(job, weight)

    def next_background(self) -> Any:
        """Dispatch the next background job — any foreground job preempts all background; else the
        highest-scoring one. None if nothing is queued."""
        return self.rs.next()

    def preempt_background(self, reason: str) -> Any:
        """Foreground demand arrived: a running background job yields and is requeued."""
        return self.rs.preempt(reason)

    def concentration_risks(self) -> List[str]:
        """Artifacts servicing a disproportionate share of ledger activity — the systemic-risk
        signal CC feeds to MGC (do-not-retire) and TCM (harden-or-split). Read-only; never blocks."""
        return self.cc.systemic_risks()

    def oi_calibration(self) -> Dict[str, Any]:
        """OI proxy-vs-human calibration snapshot (a maintenance task): how well the cheap proxy
        verdicts agree with sampled owner ground-truth + the resulting proxy weight (a divergent
        proxy is down-weighted). Read-only; never blocks; best-effort."""
        try:
            return dict(self.oi.proxy_calibration())
        except Exception:
            return {}

    def maintenance(self, policy: Optional[Dict[str, Dict[str, Any]]] = None) -> Any:
        """Build the configurable governance trigger layer over this kernel (GOOD secure defaults;
        pass `policy` to override per-task enabled/triggers/interval). The DEPLOYER drives it:
        `on_turn()` at a turn boundary, `tick(now)` on a schedule (feeds RS), `operator_run(name)`
        on a command, `dispatch_event(ev)` on an event — and `drain()` runs the RS-queued work.
        Organ-level tasks the kernel doesn't hold (LS.governance_gaps, FC.evaluate, CS-EQ scans) are
        added by the deployer via `sched.register(...)` with their chosen triggers."""
        from .observability.triggers import default_governance_scheduler
        return default_governance_scheduler(self, policy=policy)

    def run_maintenance(self, now: Optional[float] = None,
                        policy: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Drive the maintenance scheduler ONCE: submit due SCHEDULE tasks to RS and run them
        (tick + drain). The default scheduler is built on FIRST call and CACHED, so interval gating
        holds across calls in a long-lived kernel (a host loop calls this on an interval; the cage
        may call it opportunistically at message time). `policy` is applied when the scheduler is
        first built AND re-applied on later calls (a reconfigure is never silently dropped).
        Best-effort — never raises (maintenance must not crash a turn). Returns the drained task
        results."""
        try:
            if self._maintenance_scheduler is None:
                self._maintenance_scheduler = self.maintenance(policy=policy)
            elif policy:
                self._maintenance_scheduler.configure(policy)   # honour a later reconfigure too
            self._maintenance_scheduler.tick(now)
            return self._maintenance_scheduler.drain()
        except Exception:
            return []

    def scan_memory_integrity(self) -> Dict[str, Any]:
        """Between-turn memory-poisoning scan (the MPD half of the loop, the FC.evaluate sibling).
        Surfaces the AUTO-QUARANTINE subset (a grounded success a later outcome contradicts) to BB
        for OWNER REVIEW — it NEVER auto-deletes (a false positive would erase a real lesson) and
        never blocks. Idempotent: a suspect already written for review is not re-written (a
        deterministic BB id, BB being append-only). The same quarantine set drives the
        evidence-confidence overlay the verify-gated rehydration honours. The broader (owner-review)
        signature set stays available via `self.mpd.scan()` — it is NOT auto-reviewed here because
        the spine's own decisions are legitimately uniform-confidence (false-positive-heavy).
        Returns {quarantined, reviewed}. Best-effort; never raises."""
        try:
            quarantined = sorted(self.mpd.quarantined_evidence())
            reviewed: List[str] = []
            for sid in quarantined:
                exp = self.mpd.explain(sid)
                try:
                    self.bb.write({
                        "id": f"mpd-review-{sid}",
                        "title": f"memory-poisoning suspect: {exp.get('signature')}",
                        "summary": "MPD flagged this evidence for OWNER REVIEW (never auto-deleted): "
                                   "a recorded success that a later outcome contradicts.",
                        "suspect_event_id": sid,
                        "signature": exp.get("signature"),
                        "contradicting_events": exp.get("contradicting_events"),
                    })
                    reviewed.append(sid)
                except ValueError:
                    pass  # already written for review (idempotent) — BB is append-only
            if quarantined:
                self._best_effort_log("memory_poison_flag", {"action_id": "mpd-scan"},
                                      {"quarantined": quarantined, "newly_reviewed": reviewed})
            return {"quarantined": quarantined, "reviewed": reviewed}
        except Exception:
            return {"quarantined": [], "reviewed": []}

    # -- least-privilege identity/RBAC scope (live, band-driven) ------------
    def scope_for(self, capability_class: str, *,
                  resource: Optional[str] = None) -> Dict[str, Any]:
        """The least-privilege access scope a class's CURRENT authority justifies — the bridge from
        live governance authority to real (managed-identity / RBAC) access. Reads AG's live band for
        the class and maps it: advisory → no access; readonly → read; code → read+write (no delete);
        full → +delete. As authority is demoted (the reflex on a bad outcome), the grant TIGHTENS on
        the next call — JIT, never standing. Returns a scope REQUEST (identity+role+scope+ttl); it
        mints no credential, so no secret enters the container."""
        return dict(self.identity.scope_for(capability_class, self.ag.band(capability_class),
                                            resource=resource))

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
        # STRUCTURAL must-never tag carried on the ACTION (set at to_action from the tool's static
        # classification — NOT the proxy result, so a crafted tool result cannot forge a floor).
        agc = action.get("governance_class")
        if isinstance(agc, str) and agc in _GOVERNANCE_FAILURE_CLASSES:
            return "governance", agc
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
                # reflex demote — proxy is sufficient to contract (safe direction). `now` from the
                # domain clock enforces the band DWELL (AURUM_ERR_010); a demote ignores dwell, so
                # it's harmless here but kept consistent with the promote path below.
                self.ag.apply_outcome(cc, good=False, grounded=False, environment=env,
                                      severity=severity, cause=cause,
                                      now=self._domain_clock.now())
                # TL demote-fast: tier-down is immediate on ANY negative signal, proxy OR grounded
                # (TL's contract). The grounded path already feeds TL; the proxy-failure path did
                # not — so a capability that keeps failing at runtime kept its earned scope until a
                # human verdict. Wire the proxy-negative demote here too.
                self.tl.ingest({"capability": cc, "outcome": "failure", "grounded": False})
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
                               domain: Optional[str] = None,
                               operator_key_id: Optional[str] = None,
                               verdict_id: Optional[str] = None) -> None:
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
                # the ground-truth, out-of-loop signal — and WHICH operator, by value, so a
                # promotion is attributable (authority↑ because verdict V signed by operator K).
                "satisfaction_source": (f"operator:{operator_key_id}" if operator_key_id
                                        else "human"),
                "operator_key_id": operator_key_id, "verdict_id": verdict_id,
                "task_id": task_id, "capability_class": capability_class, "domain": domain,
            }
            # `now` from the domain clock makes the promotion DWELL real (AURUM_ERR_010): a band
            # cannot be re-promoted within dwell_seconds — anti-flap, previously dead because the
            # kernel passed now=None. The authority SCALAR still moves each grounded-good outcome;
            # only the rapid re-crossing of a band boundary is dwell-gated.
            self.ag.apply_outcome(capability_class, good=bool(satisfied), grounded=True,
                                  environment=environment, cause=cause,
                                  now=observed_at)
            # TL is auto-fed from this grounded (OI ground-truth) outcome: a grounded-good verdict
            # earns scope (tier-up, capped at ceiling); a grounded-bad verdict tiers down. Proxy
            # never reaches here, so proxy never earns TL scope (mirrors the AG promote-slow rule).
            self.tl.ingest({"capability": capability_class,
                            "outcome": "success" if satisfied else "failure", "grounded": True})
            # Durable FIRST (the EL grounded-outcome event is the source of truth that
            # rehydration replays), THEN the in-memory familiarity projection.
            self._best_effort_log(
                "outcome_verdict",
                {"action_id": task_id, "capability_class": capability_class},
                {"decision_id": decision_id, "satisfied": bool(satisfied),
                 "satisfaction_source": (f"operator:{operator_key_id}" if operator_key_id
                                         else "human"),
                 "operator_key_id": operator_key_id, "verdict_id": verdict_id,
                 "authority": self.ag.authority(capability_class),
                 "domain": domain, "observed_at": observed_at,
                 "volatility": self.kve.volatility(domain) if domain else None})
            if satisfied and domain:
                self.ag.record_familiarity(domain, observed_at)
        except Exception:
            pass

    # -- the ONLY production path that may EXPAND authority: a verified operator verdict --------

    def _load_operator_verifier(self, base: Path) -> Any:
        """Build the operator-verdict verifier from ed25519 public keys on the mount. Import/IO
        failure → None (no verifier ⇒ no signed promotion ⇒ contraction-only; the safe default)."""
        try:
            from .integrations.operator_verdict import OperatorVerdictVerifier
        except Exception:
            return None
        pubs: Dict[str, bytes] = {}
        key_dir = Path(os.environ.get("AURUM_OPERATOR_PUBKEYS") or (base / "operator_pubkeys"))
        try:
            if key_dir.is_dir():
                for pem in sorted(key_dir.glob("*.pem")):
                    pubs[pem.stem] = pem.read_bytes()
        except Exception:
            pubs = {}
        return OperatorVerdictVerifier(pubs)

    def _verdict_already_consumed(self, vid: str) -> bool:
        """True if a prior outcome_verdict already recorded this verdict_id — single-use, so a
        signed promotion cannot be REPLAYED to over-promote. Read over the durable outcome stream."""
        try:
            for ev in self.el.query({"source_organ": "GOV", "action_type": "GOVERNANCE_DECISION",
                                     "limit": 1_000_000}):
                p = ev.get("payload") or {}
                if p.get("outcome") == "outcome_verdict" and p.get("verdict_id") == vid:
                    return True
        except Exception:
            return False
        return False

    def submit_operator_verdict(self, signed: Any) -> Dict[str, Any]:
        """Submit a SIGNED operator verdict — the ONLY production path that may EXPAND authority
        (v1: automatic contraction, human-grounded/SIGNED expansion). The ed25519 signature is
        verified against a registered operator PUBLIC key; on success the verdict promotes via
        record_outcome_verdict with the operator identity recorded BY VALUE in the lineage (a
        promotion is thereby attributable: authority↑ because verdict V signed by operator K). On
        ANY failure — no verifier configured, bad signature, or a replayed (already-consumed)
        verdict — authority is NOT expanded; contraction-only stands. A grounded BAD verdict (a
        demote) is honoured even unsigned (contraction is always safe), attributed when signed.
        Best-effort; never raises. Returns {promoted, verified, reason, operator_key_id, ...}."""
        v = dict(getattr(signed, "verdict", None) or {})
        cc = v.get("capability_class")
        satisfied = bool(v.get("satisfied"))
        vid = getattr(signed, "id", None)
        key_id = self.operator_verifier.verify(signed) if self.operator_verifier else None
        if key_id is None:
            # Unverified PROMOTE → refuse (never expand authority on an unsigned claim). An
            # unverified DEMOTE is still honoured (contraction is safe), recorded unattributed.
            if not satisfied and cc:
                self.record_outcome_verdict(str(v.get("task_id") or "op"), cc, False,
                                            environment=v.get("environment"), domain=v.get("domain"))
                return {"promoted": False, "demoted": True, "verified": False,
                        "reason": "unverified verdict — demote honoured (contraction is safe)"}
            self._best_effort_log("operator_verdict_rejected",
                                  {"action_id": str(v.get("task_id") or "op"), "capability_class": cc},
                                  {"reason": "no valid operator signature", "verdict_id": vid})
            return {"promoted": False, "verified": False,
                    "reason": "no valid operator signature — authority NOT expanded"}
        if not cc:
            return {"promoted": False, "verified": True, "reason": "verdict missing capability_class"}
        if vid and self._verdict_already_consumed(vid):
            self._best_effort_log("operator_verdict_rejected",
                                  {"action_id": str(v.get("task_id") or "op"), "capability_class": cc},
                                  {"reason": "verdict already consumed (replay)",
                                   "verdict_id": vid, "operator_key_id": key_id})
            return {"promoted": False, "verified": True,
                    "reason": "verdict already consumed (replay)", "operator_key_id": key_id}
        self.record_outcome_verdict(str(v.get("task_id") or "op"), cc, satisfied,
                                    environment=v.get("environment"), domain=v.get("domain"),
                                    operator_key_id=key_id, verdict_id=vid)
        return {"promoted": bool(satisfied), "demoted": not satisfied, "verified": True,
                "operator_key_id": key_id, "verdict_id": vid}

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
