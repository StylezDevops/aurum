"""Organ evidence audit (scripts/organ_evidence_audit.py) — the query behind the parked
earn-complexity-or-experimental-wall decision: FIRED (changed an outcome in live data) vs
INSTRUMENTED (emits, never load-bearing) vs SILENT (zero ledger presence). Driven end-to-end:
a REAL kernel produces the ledger, the audit classifies it, and read-only is enforced.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import importlib.util
import os
import sqlite3

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts",
                       "organ_evidence_audit.py")

_IRREV = {"capability_class": "network", "action_class": "commit_outward",
          "risk_tier": "consequential", "irreversible": True}


def _load_audit():
    spec = importlib.util.spec_from_file_location("organ_evidence_audit", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live_ledger(tmp_path) -> str:
    """Produce a realistic ledger: a safe read (not logged), a consequential allow, a grounded
    outcome verdict (AG TRUST_CHANGE + OI VOTE), and a denied irreversible (ag:ceiling)."""
    k = GovernanceKernel(home=str(tmp_path))
    k.govern(to_action("read_file", {"path": "x"}))
    k.govern(to_action("write_file", {"path": "x"}))
    k.record_outcome_verdict("t1", "file_write", satisfied=True)
    denied = k.govern(to_action("http_post", {"url": "x"}, classification=_IRREV))
    assert denied.allow is False and denied.rule_id == "ag:ceiling"
    return str(tmp_path / "governance" / "el.db")


def test_fired_vs_instrumented_vs_silent(tmp_path):
    audit = _load_audit().audit
    report = audit([_live_ledger(tmp_path)])
    organs = report["organs"]
    # AG changed outcomes twice over: authored TRUST_CHANGE (stateful) AND its ceiling rule
    # is the attributed block on the denied irreversible.
    assert organs["AG"]["verdict"] == "FIRED"
    assert organs["AG"]["attributed_blocks"] >= 1 and organs["AG"]["consequential"] >= 1
    # GOV (the kernel) authored the decision events themselves.
    assert organs["GOV"]["verdict"] == "FIRED"
    # OI emitted only an observational VOTE — wired and instrumented, but it never changed
    # an outcome here. THE distinction the audit exists to draw.
    assert organs["OI"]["verdict"] == "INSTRUMENTED"
    assert organs["OI"]["authored"] >= 1 and organs["OI"]["consequential"] == 0
    # CS never touched the ledger — wired in code is not evidence.
    assert organs["CS"]["verdict"] == "SILENT" and organs["CS"]["authored"] == 0
    # Decision totals read from the events surface (the table the kernel actually writes).
    assert report["decisions"].get("deny") == 1
    assert report["decisions"].get("proceed") == 1


def test_audit_is_read_only_by_construction(tmp_path):
    mod = _load_audit()
    path = _live_ledger(tmp_path)
    ro = mod._ro(path)
    with pytest.raises(sqlite3.OperationalError):       # mode=ro: writes are impossible,
        ro.execute("INSERT INTO conflicts(conflict_id, ts, action, winner, loser, "
                   "winner_position, loser_position) VALUES ('x','t','a','AG','OI','p','q')")
    ro.close()
    report = mod.audit([path])                          # and a full audit pass leaves the
    assert report["organs"]["AG"]["verdict"] == "FIRED"  # ledger byte-identical
    k2 = GovernanceKernel(home=str(tmp_path))
    assert k2.el.verify_chain() is True


def test_aggregates_across_multiple_ledgers(tmp_path):
    audit = _load_audit().audit
    a = _live_ledger(tmp_path / "g1")
    b = _live_ledger(tmp_path / "g2")
    report = audit([a, b])
    assert report["decisions"].get("deny") == 2          # one per group ledger
    assert report["organs"]["AG"]["attributed_blocks"] >= 2
