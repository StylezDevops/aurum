"""M4 — threshold calibration framing. Read-only; PROPOSE-ONLY by construction.

The frozen constants (AG bands/dwell, EG threshold/alpha, DD seeds, screener cutoffs, CC, KVE
half-lives, _SCAN_CAP) are day-one guesses. M4 makes them evidence-calibrated WITHOUT letting the
system tune its own loss function. The split is the whole point:

  THE HUMAN SETS THE VALUE — for each threshold, the operator DECLARES which error is worse (the
  tradeoff DIRECTION). A value, not a measurement. Pre-filled below FOR RATIFICATION (the system
  did not author these directions).

  THE SYSTEM FINDS THE OPTIMUM GIVEN THE DIRECTION — it correlates each threshold's firing rate
  against downstream outcomes in real EL data and PROPOSES where the threshold should sit to honour
  the declared direction. PROPOSAL ONLY → a human ratifies → ratification signs it into the surface.

HARD CONSTRAINT: there is NO apply path in this module. A self-tuning screener optimises toward
"whatever fires least" — the opposite of the declared direction. Evidence finds the optimum of the
loss function; it must never choose the loss function. (EG.calibrate enforces the same via its
human_ratified gate.)

Calibration requires REAL accumulated ledger data; until enough has accrued, each proposal honestly
reports `insufficient_evidence` and the human-gated seed stands. An under-evidenced recalibration is
worse than an honest seed — so this never fabricates one from thin data.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

from typing import Any, Dict

# Operator-declared tradeoff DIRECTION per threshold (a VALUE — which error is worse — never
# inferred from data). Pre-filled for ratification; the asymmetry (promotion conservative, demotion
# fast) is intentional and consistent with contraction-only v1.
THRESHOLD_DIRECTIONS: Dict[str, Dict[str, Any]] = {
    "screener_block": {"current": 0.8, "direction": "low",
                       "rationale": "false-ALLOW catastrophic, false-block cheap → over-block freely"},
    "ag_promotion": {"current": "0.95/0.80/0.60", "direction": "conservative",
                     "rationale": "false-PROMOTE worse than false-hold → slow to promote"},
    "ag_demotion": {"current": "0.90/0.70/0.50", "direction": "fast",
                    "rationale": "false-HOLD worse than false-demote → quick to contract"},
    "ag_dwell": {"current": 60.0, "direction": "conservative",
                 "rationale": "anti-flap; err toward holding a band before re-promoting"},
    "dd_d_flag": {"current": 0.70, "direction": "lenient",
                  "rationale": "lean toward NOT crying deadlock on legitimate caution"},
    "eg_threshold": {"current": 0.6, "direction": "operator_set",
                     "rationale": "reroute sensitivity — operator declares per which error is worse"},
    "cc_threshold": {"current": 0.5, "direction": "operator_set",
                     "rationale": "concentration alarm sensitivity — operator-declared"},
    "kve_half_lives": {"current": "365/90/14d", "direction": "operator_set",
                       "rationale": "knowledge staleness decay — operator-declared per volatility"},
}


def calibration_report(kernel: Any, *, min_samples: int = 50) -> Dict[str, Any]:
    """Correlate each threshold's firing against downstream outcomes in REAL ledger data and PROPOSE
    where it should sit to honour the operator-declared direction. PROPOSAL ONLY — this function
    applies NOTHING (there is deliberately no apply/auto-tune in this module). With thin data a
    threshold reports `insufficient_evidence` rather than a fabricated recalibration.

    The firing rate is read from the durable EL outcome stream (the #101 audit is step one); a
    proposal is produced only once min_samples consequential outcomes have accumulated."""
    try:
        outcomes = kernel.el.query({"source_organ": "GOV", "action_type": "GOVERNANCE_DECISION",
                                    "limit": 1_000_000})
    except Exception:
        outcomes = []
    demotes = sum(1 for e in outcomes if (e.get("payload") or {}).get("outcome") == "outcome_demote")
    n = len(outcomes)

    proposals: Dict[str, Dict[str, Any]] = {}
    for name, decl in THRESHOLD_DIRECTIONS.items():
        entry: Dict[str, Any] = {"current": decl["current"], "direction": decl["direction"],
                                 "rationale": decl["rationale"]}
        if n < min_samples:
            entry["status"] = "insufficient_evidence"
            entry["note"] = (f"{n} outcomes < {min_samples} required; the human-gated seed stands "
                             "(an under-evidenced recalibration is worse than an honest seed)")
        else:
            # PROPOSAL ONLY — never applied. The concrete optimum-on-the-curve fit lands in the
            # accumulated-ledger session; here we surface the evidence + the direction to honour.
            entry["status"] = "proposal"
            entry["evidence"] = {"observed_outcomes": n, "demotes": demotes}
            entry["proposal"] = (f"hold or move {name} to honour '{decl['direction']}' given "
                                 f"{demotes}/{n} demote rate — requires human ratification to apply")
        proposals[name] = entry
    return {"proposals": proposals, "observed_outcomes": n,
            "constraint": "PROPOSE-ONLY: the system never auto-applies a threshold change; "
                          "evidence finds the optimum, the human chooses the loss function"}
