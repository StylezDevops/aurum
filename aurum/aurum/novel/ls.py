"""LS — Living Specification. Governed self-modification of the agent's identity.

Regions: CORE (unproposable), ADAPTIVE (gated), EXPERIMENTAL (auto-expiring).
Entropy limit + rule aging keep it lean. Attribution discipline: benefit needs
A/B or holdout, confounders listed, weak evidence labelled. LOW-VOLUME regime:
retirement keys off harm or disuse, not significance; critical rules protected.
PHASE 1 = retire/reweight only; creation is later.

LS is a VALIDATION system (LS-V) that occasionally proposes (LS-P), not a rule-
writer with checks bolted on. Most complexity lives in validation: attribution,
shadow evaluation, confounder detection, holdouts, canaries. Phase-5 autonomous
adoption is classified on ACTION reversibility (what the rule enables), not rule
reversibility. Rollback of an old statute runs CS.whatif first (reversibility
decays as dependents accrue). Validation-capture defense (validator diversity /
rotation) is a roadmap item, not v1 — but LS-V must leave room for it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from ..base import unbuilt
from ..types import EvidenceStrength


class Revision(TypedDict):
    diff: Any
    rationale: str
    evidence_ids: List[str]
    region: str                    # "core" | "adaptive" | "experimental"
    complexity_delta: int
    benefit: Any
    evidence_strength: EvidenceStrength
    confounders: List[str]


class LivingSpecification:
    ORGAN = "LS"

    def current(self, region: Optional[str] = None) -> Any:
        raise unbuilt(self.ORGAN, "current")

    def score(self, window: Any) -> Dict[str, Any]:
        """-> {metric, weak_rules:[id]}"""
        raise unbuilt(self.ORGAN, "score")

    def propose_revision(self) -> Revision:  # HUMAN_GATE; CORE proposals auto-rejected
        raise unbuilt(self.ORGAN, "propose_revision")

    def action_reversibility(self, revision: "Revision") -> bool:
        """Phase-5 eligibility: True only if every action the rule enables is
        reversible. A reversible statute that permits an irreversible action
        (auto-send, auto-delete) is NOT autonomously adoptable."""
        raise unbuilt(self.ORGAN, "action_reversibility")

    def rollback(self, version: int, cs: Any = None) -> None:
        """Reversibility decays with adoption age: for a sufficiently old statute,
        run cs.whatif to estimate blast radius BEFORE executing the rollback."""
        raise unbuilt(self.ORGAN, "rollback")

    def validate(self, revision: "Revision") -> Dict[str, Any]:
        """LS-V: the hard part. Prove a change is actually better post-approval —
        shadow vs actual, confounders, holdout, canary. Returns grounded evidence,
        never a causality claim. -> {grounded:bool, confidence:float, confounders:[]}"""
        raise unbuilt(self.ORGAN, "validate")

    def expire_experimental(self) -> None:
        raise unbuilt(self.ORGAN, "expire_experimental")

    def complexity(self) -> Dict[str, int]:
        """-> {adaptive_tokens, budget}"""
        raise unbuilt(self.ORGAN, "complexity")
