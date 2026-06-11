#!/usr/bin/env python3
"""Organ evidence audit — which organs have FIRED in live ledger data vs merely instrumented.

The input to the parked earn-complexity-or-experimental-wall decision. The whole audit is one
distinction, and the ledger already has the data to draw it:

  FIRED        — the organ demonstrably CHANGED AN OUTCOME in live data: a deny/needs_gate
                 decision is attributed to its rule (rule_id prefix), it WON a CA arbitration
                 (conflicts.winner), or it authored a state-changing event (TRUST_CHANGE,
                 QUARANTINE, EXCEPTION, BRANCH, PROMOTE/DEPRECATE, ARCHIVE, COST_ANOMALY, …).
  INSTRUMENTED — wired and emitting (it has authored ledger events) but nothing it wrote ever
                 changed an outcome: observational rows only (VOTE, PROPOSAL, TEST, …).
  SILENT       — zero ledger presence. Wired in code is not evidence.

READ-ONLY by construction: every database is opened with SQLite URI mode=ro — an audit must
not be able to write the thing it audits (the append-only triggers are the second lock).

Usage:
  python scripts/organ_evidence_audit.py <el.db> [<el.db> ...]   # e.g. groups/*/.hermes/governance/el.db
  python scripts/organ_evidence_audit.py --json <el.db>          # machine-readable
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List

_PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "aurum"))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from aurum.build_state import BUILT  # noqa: E402

# Authored event types that CHANGE state, vs observational telemetry. An organ whose only
# rows are observational is instrumented, not load-bearing.
_CONSEQUENTIAL = {
    "TRUST_CHANGE", "QUARANTINE", "UNQUARANTINE", "EXCEPTION", "BRANCH",
    "PROMOTION", "PROMOTE", "DEPRECATE", "ARCHIVE", "COST_ANOMALY", "GOVERNANCE_DECISION",
}

# rule_id → organ attribution for deny/needs_gate decisions. Prefix before ':' names the
# organ whose rule blocked; a bare rule_id (no colon) is a PK rule-table entry. GOV covers
# the kernel's own turn guards; gov:hostile-tainted exists only because the SEN screener
# fired, so SEN is co-credited for it.
_PREFIX_ORGAN = {"ag": "AG", "pk": "PK", "tl": "TL", "gov": "GOV", "ca": "CA"}


def _attribute(rule_id: str) -> List[str]:
    if not rule_id:
        return []
    if ":" not in rule_id:
        return ["PK"]                          # PK rule-table id (e.g. promote-needs-gate)
    prefix = rule_id.split(":", 1)[0].lower()
    organs = [_PREFIX_ORGAN.get(prefix, prefix.upper())]
    if rule_id == "gov:hostile-tainted":
        organs.append("SEN")                   # the screener verdict is what fired this guard
    return organs


def _ro(path: str) -> sqlite3.Connection:
    uri = f"file:{path.replace(os.sep, '/')}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def audit(paths: List[str]) -> Dict[str, Any]:
    """Aggregate the three evidence legs across one or more ledgers. Pure read."""
    organs: Dict[str, Dict[str, Any]] = {
        code: {"authored": 0, "consequential": 0, "attributed_blocks": 0,
               "conflict_wins": 0, "last_event": None}
        for code in sorted(set(BUILT) | {"GOV"})
    }
    decision_totals: Dict[str, int] = {}

    def row(code: str) -> Dict[str, Any]:
        return organs.setdefault(code, {"authored": 0, "consequential": 0,
                                        "attributed_blocks": 0, "conflict_wins": 0,
                                        "last_event": None})

    for path in paths:
        db = _ro(path)
        try:
            # Leg 1 — authored events (instrumented) + state-changing subset (fired).
            for organ, atype, n, last in db.execute(
                    "SELECT source_organ, action_type, COUNT(*), MAX(timestamp) "
                    "FROM evidence_ledger GROUP BY source_organ, action_type"):
                r = row(organ)
                r["authored"] += n
                if atype in _CONSEQUENTIAL:
                    r["consequential"] += n
                if r["last_event"] is None or (last and last > r["last_event"]):
                    r["last_event"] = last
            # Leg 2 — governance decisions live in the `decisions` table (the ONE by-value replay
            # surface): final_decision allow|deny|needs_gate, and reason_json.reason_codes attribute
            # a block to the organ whose rule fired. (GOVERNANCE_DECISION EVENTS are now OUTCOME
            # events only — outcome_verdict/_demote/_hold — counted under authored events in leg 1,
            # never per-action decisions.)
            for final, reason_json in db.execute(
                    "SELECT final_decision, reason_json FROM decisions"):
                decision_totals[final] = decision_totals.get(final, 0) + 1
                if final in ("deny", "needs_gate"):
                    try:
                        codes = json.loads(reason_json).get("reason_codes", [])
                    except (TypeError, ValueError, AttributeError):
                        codes = []
                    seen: set = set()
                    for code in codes:
                        for organ in _attribute(str(code)):
                            if organ not in seen:
                                row(organ)["attributed_blocks"] += 1
                                seen.add(organ)
            # Leg 3 — arbitration wins (the organ's contraction carried the outcome).
            # winner='none' is CA recording that NO contraction won (proceed) — not an organ.
            for winner, n in db.execute(
                    "SELECT winner, COUNT(*) FROM conflicts GROUP BY winner"):
                if not winner or str(winner).lower() == "none":
                    continue
                row(winner)["conflict_wins"] += n
        finally:
            db.close()

    for code, r in organs.items():
        fired = r["attributed_blocks"] or r["conflict_wins"] or r["consequential"]
        r["verdict"] = "FIRED" if fired else ("INSTRUMENTED" if r["authored"] else "SILENT")
    return {"organs": organs, "decisions": decision_totals, "ledgers": list(paths)}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Which organs FIRED in live EL data (read-only).")
    ap.add_argument("ledgers", nargs="+", help="path(s) to el.db (host: groups/*/.hermes/governance/el.db)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    report = audit(args.ledgers)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    d = report["decisions"]
    print(f"ledgers: {len(report['ledgers'])}   decisions: {d.get('allow', 0)} allow / "
          f"{d.get('deny', 0)} deny / {d.get('needs_gate', 0)} needs_gate\n")
    hdr = f"{'organ':<6} {'verdict':<12} {'authored':>8} {'stateful':>8} {'blocks':>7} {'arbwins':>7}  last_event"
    print(hdr + "\n" + "-" * len(hdr))
    order = {"FIRED": 0, "INSTRUMENTED": 1, "SILENT": 2}
    for code, r in sorted(report["organs"].items(), key=lambda kv: (order[kv[1]["verdict"]], kv[0])):
        print(f"{code:<6} {r['verdict']:<12} {r['authored']:>8} {r['consequential']:>8} "
              f"{r['attributed_blocks']:>7} {r['conflict_wins']:>7}  {r['last_event'] or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
