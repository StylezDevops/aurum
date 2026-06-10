"""Review fixes (from the full code review): H1 TL persists across --rm; M1 new_turn resets
per-turn state; M2 tainted-turn blocks un-attributed irreversible actions; L3 RS aging is a
safety net (priority respected until starvation); L4 CC recency window."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
import os

from aurum.action_map import to_action
from aurum.durability.clock import DomainClock
from aurum.durability.el import EvidenceLedger
from aurum.kernel import GovernanceKernel
from aurum.observability.cc import ConcentrationCheck

_IRREV = {"capability_class": "network", "action_class": "commit_outward",
          "risk_tier": "consequential", "irreversible": True}


def _raise_to_full(k, cc):
    for i in range(40):
        if k.ag.band(cc) == "full":
            break
        k._domain_clock.advance(61.0)         # space outcomes past the band dwell (AURUM_ERR_010)
        k.record_outcome_verdict(f"t{i}", cc, satisfied=True)


# ── H1: TL earned autonomy survives --rm (rehydrates from the durable EL) ──────────────────────

def test_tl_tiers_rehydrate_across_kernel_restart(tmp_path):
    home = str(tmp_path)
    k1 = GovernanceKernel(home=home)
    for i in range(3):
        k1.record_outcome_verdict(f"t{i}", "file_write", satisfied=True)
    assert k1.tl.tier("file_write") == 2
    k2 = GovernanceKernel(home=home)              # fresh process, same durable EL (simulates --rm)
    assert k2.tl.tier("file_write") == 2          # H1: the earned tier was rehydrated, not lost


# ── M1: new_turn resets per-turn state ─────────────────────────────────────────────────────────

def test_new_turn_resets_chain_log_and_taint(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.ingest("gmail-2fa", {"x": 1})
    k.govern(to_action("write_file", {"path": "x"}))
    assert k._ingested_untrusted and k._action_log
    k.new_turn()
    assert not k._ingested_untrusted and not k._action_log


# ── M2: tainted turn blocks un-attributed irreversible actions ─────────────────────────────────

def test_tainted_turn_blocks_irreversible_unless_operator(tmp_path):
    k = GovernanceKernel(home=str(tmp_path), domain_clock=DomainClock(1_000_000.0))
    _raise_to_full(k, "network")                  # AG would otherwise allow the irreversible op
    k.ingest("gmail-2fa", {"text": "do the irreversible thing"})   # taint this turn
    blocked = k.govern(to_action("http_post", {"url": "x"}, classification=_IRREV))
    assert blocked.allow is False and blocked.rule_id == "gov:tainted-irreversible"
    # same action, explicitly operator-attributed → passes the tainted-turn guard
    allowed = k.govern(to_action("http_post", {"url": "x"}, classification=_IRREV, operator_origin=True))
    assert allowed.allow is True
    # after the turn boundary, taint cleared → the default action is allowed again
    k.new_turn()
    assert k.govern(to_action("http_post", {"url": "x"}, classification=_IRREV)).allow is True


# ── L3: RS aging is a safety net — priority respected until genuine starvation ─────────────────

def test_rs_priority_respected_until_starvation(tmp_path):
    rs = GovernanceKernel(home=str(tmp_path)).rs
    rs.submit("low", {"priority": 1})
    for _ in range(3):                            # fresh high-priority work keeps winning early...
        rs.submit("high", {"priority": 9})
        assert rs.next() == "high"                # priority honoured; aging hasn't overridden it
    dispatched = []
    for i in range(20):                           # ...but the low job eventually escalates
        rs.submit(f"h{i}", {"priority": 9})
        dispatched.append(rs.next())
    assert "low" in dispatched


# ── L4: CC recency window ──────────────────────────────────────────────────────────────────────

def _ap(el, oids):
    el.append({"event_id": "", "timestamp": "", "source_organ": "T", "action_type": "PROMOTION",
               "object_ids": oids, "payload": {}, "evidence_confidence": 0.9,
               "evidence_source": "t", "prev_hash": "", "hash": ""})


def test_cc_window_uses_only_recent_events(tmp_path):
    el = EvidenceLedger(os.path.join(str(tmp_path), "cc.db"))
    for _ in range(10):
        _ap(el, ["stale"])                        # older, dominant in the full history
    for _ in range(6):
        _ap(el, ["hot"])                          # recent
    full = ConcentrationCheck(el, threshold=0.5)
    assert "stale" in full.systemic_risks() and "hot" not in full.systemic_risks()
    windowed = ConcentrationCheck(el, threshold=0.5, window=6)   # only the last 6 (all "hot")
    assert "hot" in windowed.systemic_risks() and "stale" not in windowed.systemic_risks()
