"""LS — Living Specification. Governed self-modification of the agent's identity.

The ONLY organ that evolves the agent's identity; its gate is strict. A VALIDATION system
that occasionally proposes, not a rule-writer with checks bolted on.

Regions: CORE (unproposable — anything weakening guardrails is auto-rejected PRE-gate),
ADAPTIVE (gated), EXPERIMENTAL (auto-expiring). v1 is SUBTRACTION-ONLY: rule RETIREMENT and
REWEIGHTING — removing dead/harmful governance is tractable at low volume; rule CREATION is a
later phase (the hard attribution problem). Invariants enforced here:
  • CORE proposals are auto-rejected before the gate (AURUM_ERR_004).
  • A proposal that is really a PREFERENCE is redirected to PM, not encoded as a rule.
  • CONSTITUTIONAL ENTROPY LIMIT: net adaptive complexity may only rise with matched benefit.
  • NO self-applied revisions — apply is HUMAN_GATE; rollback restores a prior version
    BYTE-IDENTICAL. Rollback of a sufficiently old statute runs CS.whatif first (reversibility
    decays as dependents accrue).
  • Phase-5 eligibility is classified on ACTION reversibility (what the rule enables), not rule
    reversibility — a statute permitting any irreversible action is never autonomously adoptable.
  • RULES ARE EVIDENCED ENTITIES: a rule's standing (grounded/dormant/eroded/unevidenced) is
    DERIVED from the evidence behind it (`rule_evidence`), not asserted — a rule whose justifying
    evidence was contradicted (an MPD-quarantined success) erodes to a retirement candidate, and
    `score` folds that erosion into weakness. The SECOND-ORDER loop (`governance_gaps`) surfaces a
    'must never' breach class that RECURS despite governance — "why isn't the pre-hoc gate stopping
    this?" — for owner review (v1 is subtraction-only, so it never auto-creates the missing rule).
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, TypedDict

from ..types import EvidenceStrength

_IRREVERSIBLE = {"auto_send", "auto_delete", "auto_notify", "auto_post", "auto_pay"}
_OLD_STATUTE_AGE = 30 * 24 * 3600.0  # rollback past this age must run CS.whatif first


class Revision(TypedDict):
    diff: Any
    rationale: str
    evidence_ids: List[str]
    region: str
    complexity_delta: int
    benefit: Any
    evidence_strength: EvidenceStrength
    confounders: List[str]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ls_rules (
    rule_id     TEXT PRIMARY KEY,
    region      TEXT NOT NULL,          -- core | adaptive | experimental
    text        TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    tokens      INTEGER NOT NULL DEFAULT 10,
    introduced_at REAL NOT NULL,
    last_used   REAL,
    supporting_evidence TEXT NOT NULL DEFAULT '[]',
    protected   INTEGER NOT NULL DEFAULT 0,
    expires_at  REAL,
    status      TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS ls_versions (
    version INTEGER PRIMARY KEY,
    rules_json TEXT NOT NULL,
    ts REAL NOT NULL
);
"""


class LivingSpecification:
    ORGAN = "LS"

    def __init__(self, path: str = "aurum_ls.db", el: Any = None, pm: Any = None,
                 max_adaptive_tokens: int = 1000, aging_days: float = 180.0) -> None:
        self._el, self._pm = el, pm
        self.max_adaptive_tokens = max_adaptive_tokens
        self.aging_seconds = aging_days * 24 * 3600.0
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)

    # -- seed / read --------------------------------------------------------
    def add_rule(self, rule: Dict[str, Any]) -> str:
        rid = rule.get("rule_id") or uuid.uuid4().hex
        now = rule.get("now") or time.time()
        self._db.execute(
            "INSERT INTO ls_rules(rule_id,region,text,weight,tokens,introduced_at,"
            "last_used,supporting_evidence,protected,expires_at,status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, rule.get("region", "adaptive"), rule.get("text", ""),
             float(rule.get("weight", 1.0)), int(rule.get("tokens", 10)), now,
             rule.get("last_used"), json.dumps(rule.get("supporting_evidence", [])),
             int(rule.get("protected", 0)), rule.get("expires_at"), "active"),
        )
        return rid

    def current(self, region: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM ls_rules WHERE status='active'"
        args: tuple = ()
        if region is not None:
            q += " AND region=?"
            args = (region,)
        return [self._row(r) for r in self._db.execute(q, args).fetchall()]

    @property
    def version(self) -> int:
        row = self._db.execute("SELECT MAX(version) FROM ls_versions").fetchone()
        return -1 if row[0] is None else row[0]

    # -- scoring: find weak rules (harm, disuse, or evidence erosion) -------
    def score(self, window: Any = None, now: Optional[float] = None,
              contradicted_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Weak = harmful (caller-supplied) OR disused OR EVIDENCE-ERODED. The last makes a rule an
        evidenced entity: if the evidence that JUSTIFIED a rule was later contradicted (e.g. an
        MPD-quarantined success), the rule's grounding no longer holds and it becomes a retirement
        candidate regardless of usage. `contradicted_ids` (from MPD.quarantined_evidence()) is the
        contradicted-evidence set; absent → back-compatible (erosion contributes nothing)."""
        now = time.time() if now is None else now
        harmful = set((window or {}).get("harmful_rules", []))
        contradicted = set(contradicted_ids or [])
        adaptive = self.current("adaptive")
        weak = []
        for r in adaptive:
            if r["protected"]:
                continue  # rare-but-critical rules are never weak on disuse
            disused = (r["last_used"] is None
                       or (now - r["last_used"]) > self.aging_seconds)
            eroded = bool(contradicted) and any(e in contradicted
                                                for e in r["supporting_evidence"])
            if r["rule_id"] in harmful or disused or eroded:
                weak.append(r["rule_id"])
        return {"metric": 1.0 - len(weak) / max(1, len(adaptive)),
                "weak_rules": weak}

    # -- rules as evidenced entities + the second-order loop ----------------
    def rule_evidence(self, now: Optional[float] = None,
                      contradicted_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Each ACTIVE rule as an EVIDENCED ENTITY — its standing TRACED to the evidence behind it,
        not asserted. Standing (priority order): `protected` (rare-but-critical, never weak);
        `eroded` (its supporting evidence was contradicted — `contradicted_ids` from
        MPD.quarantined_evidence()); `unevidenced` (introduced with NO supporting evidence — a
        review candidate); `dormant` (disused past the aging window); else `grounded`. Read-only."""
        now = time.time() if now is None else now
        contradicted = set(contradicted_ids or [])
        out: List[Dict[str, Any]] = []
        for r in self.current():
            ev_ids = r["supporting_evidence"]
            n_contra = sum(1 for e in ev_ids if e in contradicted)
            disused = (r["last_used"] is None
                       or (now - r["last_used"]) > self.aging_seconds)
            out.append({"rule_id": r["rule_id"], "region": r["region"],
                        "age_seconds": now - r["introduced_at"], "disused": disused,
                        "supporting_evidence": len(ev_ids), "evidence_contradicted": n_contra,
                        "standing": self._standing(r, len(ev_ids), n_contra, disused)})
        return out

    def governance_gaps(self, min_recurrence: int = 3,
                        now: Optional[float] = None) -> List[Dict[str, Any]]:
        """The SECOND-ORDER loop: 'why isn't the pre-hoc gate stopping this recurring class?'

        A 'must never' breach that PK PRE-blocks never produces an outcome (the action never runs),
        so it can't show up as a post-hoc demote. Therefore a governance-severity class that DOES
        recur as `outcome_demote` events in EL is, by construction, one the pre-hoc gate is NOT
        stopping — and if it recurs (>= min_recurrence) that is a GOVERNANCE GAP. LS is
        subtraction-only (v1 cannot CREATE a rule — the hard attribution problem), so this only
        SURFACES the gap for owner review (a human-authored rule may be needed); it never
        auto-writes one. Read-only over EL; returns findings, audits each to EL."""
        if self._el is None:
            return []
        counts: Dict[str, int] = {}
        try:
            for ev in self._el.query({"source_organ": "GOV",
                                      "action_type": "GOVERNANCE_DECISION", "limit": 1_000_000}):
                p = ev.get("payload") or {}
                if p.get("outcome") == "outcome_demote" and p.get("severity") == "governance":
                    cls = p.get("severity_class")
                    if cls:
                        counts[cls] = counts.get(cls, 0) + 1
        except Exception:
            return []
        gaps = [{"class": c, "occurrences": n, "kind": "recurring_unstopped_breach",
                 "question": "a 'must never' breach recurs post-hoc — why isn't the pre-hoc "
                             "gate (PK) stopping this class?"}
                for c, n in sorted(counts.items()) if n >= min_recurrence]
        for g in gaps:
            self._audit("GOVERNANCE_GAP", g["class"],
                        f"recurring_unstopped_x{g['occurrences']}")
        return gaps

    @staticmethod
    def _standing(rule: Dict[str, Any], n_evidence: int, n_contradicted: int,
                  disused: bool) -> str:
        if rule["protected"]:
            return "protected"
        if n_contradicted > 0:
            return "eroded"        # the evidence that justified it was contradicted
        if n_evidence == 0:
            return "unevidenced"   # adaptive rule introduced without grounding
        if disused:
            return "dormant"
        return "grounded"

    # -- proposal (subtraction-only in v1; CORE auto-rejected pre-gate) -----
    def propose_revision(self, rule_id: Optional[str] = None, kind: str = "retire",
                         as_preference: bool = False,
                         evidence_ids: Optional[List[str]] = None,
                         new_weight: Optional[float] = None) -> Dict[str, Any]:
        if rule_id is None:  # auto-pick the weakest candidate
            weak = self.score()["weak_rules"]
            if not weak:
                return {"rejected": True, "reason": "no_candidate"}
            rule_id = weak[0]
        rule = self._get(rule_id)
        if rule is None:
            return {"rejected": True, "reason": "unknown_rule"}
        # CORE is unproposable — rejected BEFORE the gate (AURUM_ERR_004)
        if rule["region"] == "core":
            self._audit("PROPOSAL", rule_id, "rejected_core_unproposable")
            return {"rejected": True, "reason": "core_unproposable", "region": "core"}
        # a proposal that's really a preference routes to PM, not the constitution
        if as_preference:
            if self._pm is not None:
                self._pm.add({"statement": rule["text"]}, provenance="inferred")
            return {"rejected": True, "reason": "redirect_to_PM", "route": "PM"}
        evidence = evidence_ids if evidence_ids is not None else rule["supporting_evidence"]
        complexity_delta = -rule["tokens"] if kind == "retire" else 0
        rev: Dict[str, Any] = {
            "diff": {"op": kind, "rule_id": rule_id, "new_weight": new_weight},
            "rationale": f"{kind} rule {rule_id}: weak/harmful per evidence",
            "evidence_ids": evidence, "region": rule["region"],
            "complexity_delta": complexity_delta, "benefit": {"kind": "subtraction"},
            "evidence_strength": "strong" if evidence else "weak",
            "confounders": [], "rejected": False}
        self._audit("PROPOSAL", rule_id, kind)
        return rev

    # -- apply (HUMAN_GATE) + versioned rollback ---------------------------
    def apply_revision(self, revision: Dict[str, Any],
                       approved_by: Optional[str] = None) -> Dict[str, Any]:
        if revision.get("rejected"):
            raise ValueError(f"cannot apply a rejected revision: {revision.get('reason')}")
        if revision["region"] == "core":
            raise ValueError("CORE is unproposable")  # defense in depth
        # entropy limit: net complexity may only rise with matched benefit
        if revision["complexity_delta"] > 0 and not revision.get("benefit_proven"):
            raise ValueError("entropy limit: complexity increase without matched benefit")
        if approved_by is None:
            raise PermissionError("LS.apply_revision is HUMAN_GATE (approved_by required)")
        version = self._save_version()  # snapshot PRE-mutation state for rollback
        diff = revision["diff"]
        if diff["op"] == "retire":
            self._db.execute("UPDATE ls_rules SET status='retired' WHERE rule_id=?",
                             (diff["rule_id"],))
        elif diff["op"] == "reweight" and diff.get("new_weight") is not None:
            self._db.execute("UPDATE ls_rules SET weight=? WHERE rule_id=?",
                             (float(diff["new_weight"]), diff["rule_id"]))
        self._audit("PROPOSAL", diff["rule_id"], f"applied_{diff['op']}")
        return {"applied": True, "version": version}

    def rollback(self, version: int, cs: Any = None, now: Optional[float] = None) -> None:
        """Restore a prior version BYTE-IDENTICAL. For an old statute, estimate blast
        radius via cs.whatif FIRST (reversibility decays with adoption age)."""
        row = self._db.execute(
            "SELECT rules_json, ts FROM ls_versions WHERE version=?", (version,)).fetchone()
        if row is None:
            raise KeyError(f"LS: no version {version}")
        now = time.time() if now is None else now
        if cs is not None and (now - row[1]) > _OLD_STATUTE_AGE:
            cs.whatif({"op": "remove", "node_id": f"ls_version_{version}"})
        rules = json.loads(row[0])
        self._db.execute("DELETE FROM ls_rules")
        for r in rules:
            self._db.execute(
                "INSERT INTO ls_rules(rule_id,region,text,weight,tokens,introduced_at,"
                "last_used,supporting_evidence,protected,expires_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r["rule_id"], r["region"], r["text"], r["weight"], r["tokens"],
                 r["introduced_at"], r["last_used"],
                 json.dumps(r["supporting_evidence"]), int(r["protected"]),
                 r["expires_at"], r["status"]))

    # -- LS-V (validation): grounded evidence, never a causality claim -----
    def validate(self, revision: Dict[str, Any]) -> Dict[str, Any]:
        evidence = revision.get("evidence_ids", [])
        confounders = revision.get("confounders", [])
        grounded = (revision.get("evidence_strength") == "strong"
                    and bool(evidence) and not confounders)
        confidence = min(1.0, 0.5 + 0.1 * len(evidence)) if grounded else 0.2
        return {"grounded": grounded, "confidence": confidence,
                "confounders": confounders}

    def action_reversibility(self, revision: Dict[str, Any]) -> bool:
        enabled = revision.get("enabled_actions", [])
        return not any(a in _IRREVERSIBLE for a in enabled)

    def expire_experimental(self, now: Optional[float] = None) -> List[str]:
        now = time.time() if now is None else now
        rows = self._db.execute(
            "SELECT rule_id FROM ls_rules WHERE region='experimental' "
            "AND status='active' AND expires_at IS NOT NULL AND expires_at<=?",
            (now,)).fetchall()
        expired = [r[0] for r in rows]
        for rid in expired:
            self._db.execute("UPDATE ls_rules SET status='expired' WHERE rule_id=?", (rid,))
        return expired

    def complexity(self) -> Dict[str, int]:
        total = sum(r["tokens"] for r in self.current("adaptive"))
        return {"adaptive_tokens": total, "budget": self.max_adaptive_tokens}

    # -- internals ----------------------------------------------------------
    def _save_version(self) -> int:
        version = self.version + 1
        rules = [self._row(r) for r in self._db.execute("SELECT * FROM ls_rules").fetchall()]
        self._db.execute("INSERT INTO ls_versions(version,rules_json,ts) VALUES (?,?,?)",
                         (version, json.dumps(rules), time.time()))
        return version

    def _get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute("SELECT * FROM ls_rules WHERE rule_id=?",
                               (rule_id,)).fetchone()
        return self._row(row) if row else None

    def _row(self, r) -> Dict[str, Any]:
        cols = ["rule_id", "region", "text", "weight", "tokens", "introduced_at",
                "last_used", "supporting_evidence", "protected", "expires_at", "status"]
        d = dict(zip(cols, r))
        d["supporting_evidence"] = json.loads(d["supporting_evidence"] or "[]")
        return d

    def _audit(self, action_type: str, rule_id: str, note: str) -> None:
        if self._el is None:
            return
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "LS",
            "action_type": action_type, "object_ids": [rule_id],
            "payload": {"capability_class": "constitution", "rule_id": rule_id,
                        "note": note},
            "evidence_confidence": 1.0, "evidence_source": "LS",
            "prev_hash": "", "hash": ""})
