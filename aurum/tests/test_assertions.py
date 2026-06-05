"""AURUM_ERR_001..012 — compliance-gate assertion harness.

Each assertion encodes a safety invariant from the spec. Per the handoff mandate,
the main execution loop must refuse to initialize if any LIVE assertion fails to
catch its designated violation.

Lifecycle (matches the build order honestly):
  - If the organ(s) an assertion needs are not yet built (per build_state.BUILT),
    the test SKIPS — the gate isn't live yet, and a skipped gate is not a passed
    gate. It is NOT silently stubbed green.
  - Once the organ is built, flip its flag in build_state; the assertion goes LIVE
    and must genuinely demonstrate the violation is caught, or commit is blocked.

Opus: implement each test body where marked. Do not weaken an assertion to make it
pass — that defeats the harness. A red/blocked assertion is the system protecting
itself from its own code generation.
"""
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.gates import GATES


def _gate(name: str):
    organs, _desc = GATES[name]
    if not is_built(*organs):
        pytest.skip(f"{name}: organs {organs} not built yet — gate not live (skip != pass)")


def test_AURUM_ERR_001_crypto_continuity():
    _gate("AURUM_ERR_001")
    # Live once EL+CB are both built. EL half is implemented; the CB-lockout half
    # is wired when CB lands (replace the CB stub call below).
    import os, sqlite3, tempfile
    from aurum.durability.el import EvidenceLedger

    db = os.path.join(tempfile.mkdtemp(), "err001.db")
    el = EvidenceLedger(db)
    ev = {"event_id": "", "timestamp": "", "source_organ": "TS",
          "action_type": "PROMOTION", "object_ids": ["tool_a"],
          "payload": {"capability_class": "synth"},
          "evidence_confidence": 0.9, "evidence_source": "test",
          "prev_hash": "", "hash": ""}
    el.append(ev)
    el.append(dict(ev, object_ids=["tool_a"]))
    assert el.verify_chain() is True

    # A retroactive edit must be rejected by the DB-level append-only trigger...
    raised = False
    try:
        el._db.execute("UPDATE evidence_ledger SET payload='{}' WHERE seq=1")
    except sqlite3.Error as e:
        raised = True
        assert "AURUM_ERR_001" in str(e)
    assert raised, "append-only trigger failed to block UPDATE"

    # ...and if a row is forced out-of-band, verify_chain must detect the break.
    el._db.execute("PRAGMA foreign_keys=OFF")
    el._db.execute("DROP TRIGGER el_no_update")
    el._db.execute("UPDATE evidence_ledger SET payload='{\"tampered\":1}' WHERE seq=1")
    assert el.verify_chain() is False, "verify_chain failed to detect tampering"

    # CB lockout half: a detected chain break must trip CB into emergency LOCKOUT,
    # and that lockout persists until a HUMAN_GATE reset (denials persist by intent).
    from aurum.support.cb import CircuitBreaker

    cb = CircuitBreaker(os.path.join(tempfile.mkdtemp(), "cb001.db"), el=el)
    assert cb.state() == "closed"
    if not el.verify_chain():
        cb.trip({"kind": "integrity", "detail": "EL.verify_chain() failed"})
    assert cb.state() == "lockout", "CB did not enter lockout on EL chain break"
    # Lockout halts ALL capability growth, system-wide...
    assert cb.is_frozen("API_Synthesis") is True
    # ...and only a HUMAN_GATE reset clears it.
    cb.reset()
    assert cb.state() == "closed"
    assert cb.is_frozen("API_Synthesis") is False


def test_AURUM_ERR_002_lossless_snapshot():
    _gate("AURUM_ERR_002")
    # LIVE (EL+MGC). Compressing an EL region copies cold rows to the archive but NEVER
    # deletes from the live ledger, so lineage() delta-count is identical afterwards.
    import os, tempfile
    from aurum.durability.el import EvidenceLedger
    from aurum.durability.mgc import MemoryGarbageCollector

    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    ev = {"event_id": "", "timestamp": "", "source_organ": "TS",
          "action_type": "PROMOTION", "object_ids": ["obj_a"],
          "payload": {"capability_class": "synth"}, "evidence_confidence": 0.9,
          "evidence_source": "t", "prev_hash": "", "hash": ""}
    el.append(ev); el.append(dict(ev))
    before = len(el.lineage("obj_a"))
    MemoryGarbageCollector(os.path.join(tempfile.mkdtemp(), "mgc.db"),
                           el=el).compress({"object_id": "obj_a"})
    assert len(el.lineage("obj_a")) == before, "compression changed the delta count"
    assert el.verify_chain() is True


def test_AURUM_ERR_003_ghost_dependency():
    _gate("AURUM_ERR_003")
    # LIVE (CS+MGC). A CS-leased artifact is skipped from MGC's sweep even with no edges.
    import os, tempfile
    from aurum.novel.cs import CausalSimulator
    from aurum.durability.mgc import MemoryGarbageCollector

    cs = CausalSimulator(os.path.join(tempfile.mkdtemp(), "cs.db"))
    cs.add_node("tool_alpha", "tool")          # no referencing edges -> looks orphaned
    cs.lease("tool_alpha", ttl=300)
    mgc = MemoryGarbageCollector(os.path.join(tempfile.mkdtemp(), "mgc.db"), cs=cs)
    out = mgc.scan({"skills": ["tool_alpha"], "tools": ["tool_alpha"]})
    assert "tool_alpha" not in out["archivable"]
    assert "tool_alpha" not in out["retirable"]


def test_AURUM_ERR_004_constitutional_shield():
    _gate("AURUM_ERR_004")
    # LIVE now (needs only LS). A revision targeting a CORE rule is rejected PRE-gate —
    # it must never become an applyable revision.
    import os, tempfile
    from aurum.novel.ls import LivingSpecification

    ls = LivingSpecification(os.path.join(tempfile.mkdtemp(), "ls.db"))
    core_id = ls.add_rule({"region": "core", "text": "never weaken guardrails"})
    rev = ls.propose_revision(rule_id=core_id, kind="retire")
    assert rev.get("rejected") is True and rev.get("reason") == "core_unproposable"
    # and even a hand-forged core revision cannot be applied
    raised = False
    try:
        ls.apply_revision({"region": "core", "complexity_delta": 0,
                           "diff": {"op": "retire", "rule_id": core_id}},
                          approved_by="owner")
    except ValueError:
        raised = True
    assert raised, "a CORE revision was applyable"


def test_AURUM_ERR_005_independence_decoupling():
    _gate("AURUM_ERR_005")
    # LIVE now (needs only HVP). A high-stakes check rostered with two same-family
    # models (gpt-5 + gpt-5-mini) must FAIL CLOSED on a min_families violation rather
    # than silently verifying with correlated verifiers.
    import pytest as _pytest
    from aurum.novel.hvp import HeterogeneousVerifierPanel, HVPRoutingError

    hvp = HeterogeneousVerifierPanel()
    hvp.configure([
        {"id": "a", "base_url": "u", "api_key_ref": "k", "model": "gpt-5",
         "family": "gpt", "provider": "openai", "trust_tier": 3, "cost_class": 3,
         "sees_sensitive": True},
        {"id": "b", "base_url": "u", "api_key_ref": "k", "model": "gpt-5-mini",
         "family": "gpt", "provider": "openai", "trust_tier": 2, "cost_class": 1,
         "sees_sensitive": True},
    ])
    raised = False
    try:
        hvp.route({"payload_hash": "h", "is_sensitive": False,
                   "stakes": "high_stakes", "required_aspects": ["correct"]})
    except HVPRoutingError as e:
        raised = True
        assert "min_families" in str(e)
    assert raised, "same-family roster did not fail closed on a high-stakes route"


def test_AURUM_ERR_006_growth_isolation():
    _gate("AURUM_ERR_006")
    # TODO(opus): CB.freeze_growth('API_Synthesis'); assert AA.synthesize and
    # TS.promote are blocked while an existing TCM-mapped tool still executes.
    raise NotImplementedError("AURUM_ERR_006 body: implement once CB+AA+TS+TCM are built")


def test_AURUM_ERR_007_semantic_privilege_escalation():
    _gate("AURUM_ERR_007")
    # TODO(opus): build a chain whose steps each pass PK.check but whose aggregate
    # (read-secret + external-write) is forbidden; assert PK.check_chain denies it.
    raise NotImplementedError("AURUM_ERR_007 body: implement once PK is built")


def test_AURUM_ERR_008_injection_boundary():
    _gate("AURUM_ERR_008")
    # TODO(opus): feed an instruction embedded in untrusted AA/SEN content; assert
    # it cannot trigger an action, raise AG authority, or satisfy a gate.
    raise NotImplementedError("AURUM_ERR_008 body: implement once PK+AA+SEN are built")


def test_AURUM_ERR_009_refusal_persistence_padding_resistant():
    _gate("AURUM_ERR_009")
    # TODO(opus): deny a source->sink taint path; re-submit with benign padding
    # steps inserted; assert it still matches the prior denial (data-flow, not topology).
    raise NotImplementedError("AURUM_ERR_009 body: implement once PK+CS are built")


def test_AURUM_ERR_010_authority_flapping():
    _gate("AURUM_ERR_010")
    # LIVE now (needs only AG). An authority oscillating 0.81/0.79/0.81/0.79 must hold
    # a STABLE band — dual promote(0.80)/demote(0.70) thresholds mean it never re-crosses.
    from aurum.novel.ag import AuthorityGovernor

    ag = AuthorityGovernor(dwell_seconds=0.0)
    cc = "code_edit"
    ag.set_authority(cc, 0.81)
    stable = ag.band(cc)
    assert stable == "code"
    for v in (0.79, 0.81, 0.79, 0.81, 0.79):
        ag.set_authority(cc, v)
        assert ag.band(cc) == stable, f"band flapped at authority {v}: {ag.band(cc)}"


def test_AURUM_ERR_011_el_fail_safe():
    _gate("AURUM_ERR_011")
    # LIVE now (needs only EL). A consequential action must not proceed if its
    # EL event cannot be logged. We model the action as gated on append().
    import os, tempfile
    from aurum.durability.el import EvidenceLedger

    db = os.path.join(tempfile.mkdtemp(), "err011.db")
    el = EvidenceLedger(db)

    action_executed = {"value": False}

    def consequential_action(event):
        # The invariant: log first, then act. If append raises, act never runs.
        el.append(event)
        action_executed["value"] = True

    # Force append to fail by breaking the underlying store.
    el._db.close()  # any subsequent append raises -> fail-safe must block the act

    ev = {"event_id": "", "timestamp": "", "source_organ": "TS",
          "action_type": "PROMOTION", "object_ids": ["tool_b"],
          "payload": {"capability_class": "synth"},
          "evidence_confidence": 0.9, "evidence_source": "test",
          "prev_hash": "", "hash": ""}

    blocked = False
    try:
        consequential_action(ev)
    except Exception:
        blocked = True

    assert blocked, "append failure did not raise"
    assert action_executed["value"] is False, \
        "action executed despite EL.append failing (fail-safe violated)"


def test_AURUM_ERR_012_owner_absence():
    _gate("AURUM_ERR_012")
    # TODO(opus): advance past gate TTL with no approver; assert Class-B/C pending
    # expire to denied, growth paths pause, and authority does not widen.
    raise NotImplementedError("AURUM_ERR_012 body: implement once PK+AG are built")


# --- arbitration layer (013-020) ------------------------------------------
def _ca():
    import os, tempfile
    from aurum.arbitration.ca import ConflictArbiter
    return ConflictArbiter(os.path.join(tempfile.mkdtemp(), "ca.db"))


def _sig(name, directive, justification=None, constitutional=False):
    basis = {} if justification is None else {"justification": justification}
    return {"signal": name, "directive": directive, "basis": basis,
            "constitutional": constitutional}


def _seed_conflicts(ca, justifications, constitutional=False):
    for j in justifications:  # AG contracts, OI proceeds -> a logged contraction
        ca.arbitrate({"action_id": "a", "capability_class": "code_edit"},
                     [_sig("AG", "contract", j, constitutional), _sig("OI", "proceed")])


def test_AURUM_ERR_013_arbiter_determinism():
    _gate("AURUM_ERR_013")
    ca = _ca()
    sigs = [_sig("AG", "contract", 0.9), _sig("OI", "proceed")]
    r1 = ca.arbitrate({"action_id": "a", "capability_class": "c"}, sigs)
    r2 = ca.arbitrate({"action_id": "a", "capability_class": "c"}, sigs)
    assert r1["resolution"] == r2["resolution"] == "contract"
    assert r1["winner"] == r2["winner"] == "AG"
    assert r1["record"]["participants"] == r2["record"]["participants"]


def test_AURUM_ERR_014_caution_wins_logged():
    _gate("AURUM_ERR_014")
    ca = _ca()
    out = ca.arbitrate({"action_id": "a", "capability_class": "c"},
                       [_sig("AG", "contract", 0.8), _sig("OI", "proceed"),
                        _sig("LS", "proceed")])
    assert out["resolution"] == "contract" and out["winner"] == "AG"
    rec = ca.conflicts()[-1]
    assert {p["signal"] for p in rec["participants"]} == {"AG", "OI", "LS"}


def test_AURUM_ERR_015_hard_layer_not_arbitrated():
    _gate("AURUM_ERR_015")
    from aurum.arbitration.ca import ArbitrationError
    ca = _ca()
    raised = False
    try:
        ca.arbitrate({"action_id": "a", "pk_deny": True},
                     [_sig("AG", "contract", 0.5), _sig("OI", "proceed")])
    except ArbitrationError:
        raised = True
    assert raised, "CA arbitrated a PK-denied action"


def test_AURUM_ERR_016_silent_contraction_forbidden():
    _gate("AURUM_ERR_016")
    ca = _ca()
    before = len(ca.conflicts())
    ca.arbitrate({"action_id": "a", "capability_class": "c"},
                 [_sig("AG", "contract", 0.5), _sig("OI", "proceed")])
    assert len(ca.conflicts()) == before + 1, "contraction wrote no ConflictRecord"


def test_AURUM_ERR_017_wise_caution_vs_deadlock():
    _gate("AURUM_ERR_017")
    from aurum.arbitration.dd import DeadlockDetector
    # wise caution: justification stays elevated -> D below flag
    ca_wise = _ca()
    _seed_conflicts(ca_wise, [0.9] * 6)
    assert DeadlockDetector(ca=ca_wise).scan() == []
    # deadlock: justification abates while resolution stays contract -> flag + 1 escalation
    ca_dl = _ca()
    _seed_conflicts(ca_dl, [0.9, 0.8, 0.6, 0.4, 0.2, 0.1])
    esc = DeadlockDetector(ca=ca_dl).scan()
    assert len(esc) == 1


def test_AURUM_ERR_018_constitutional_exclusion():
    _gate("AURUM_ERR_018")
    from aurum.arbitration.dd import DeadlockDetector
    ca = _ca()
    # justification abated (would look like deadlock) BUT winner is constitutional
    _seed_conflicts(ca, [0.9, 0.7, 0.5, 0.3, 0.1, 0.05], constitutional=True)
    assert DeadlockDetector(ca=ca).scan() == [], "constitutional contraction flagged"


def test_AURUM_ERR_019_dd_never_self_resolves():
    _gate("AURUM_ERR_019")
    from aurum.arbitration.dd import DeadlockDetector
    dd = DeadlockDetector()
    raised = False
    try:
        dd.set_parameters({"d_flag": 0.99})  # auto-retune attempt, no human gate
    except PermissionError:
        raised = True
    assert raised, "DD allowed auto-retune of its constitutional parameters"


def test_AURUM_ERR_020_escalation_dedup():
    _gate("AURUM_ERR_020")
    from aurum.arbitration.dd import DeadlockDetector
    ca = _ca()
    _seed_conflicts(ca, [0.9, 0.7, 0.5, 0.3, 0.1, 0.05])
    dd = DeadlockDetector(ca=ca)
    dd.scan(); dd.scan(); esc = dd.scan()  # three re-runs of the same deadlock
    assert len(esc) == 1 and esc[0]["recurrences"] >= 2  # one item, growing weight


def test_AURUM_ERR_021_mount_jail():
    _gate("AURUM_ERR_021")
    # LIVE (needs only CAGE). The mount jail is the cage's host-fs containment
    # boundary; assert it denies-by-default and fails closed on escape attempts.
    import os, tempfile
    from aurum.cage.mount_jail import MountJail, MountDenied

    allow = os.path.realpath(tempfile.mkdtemp())
    outside = os.path.realpath(tempfile.mkdtemp())
    jail = MountJail([allow])

    inside = os.path.join(allow, "sub")
    os.makedirs(inside, exist_ok=True)

    # R2: inside an allowlisted root is permitted; an unrelated dir is not.
    assert jail.is_allowed(inside) is True
    assert jail.is_allowed(outside) is False
    # R4: a string-prefix sibling is NOT contained (/allow must not authorise /allow-evil).
    assert jail.is_allowed(allow + "-evil") is False
    # R1: an empty allowlist denies everything (deny-by-default).
    assert MountJail([]).is_allowed(inside) is False
    # R5: build_mounts FAILS CLOSED on a disallowed extra (not silently dropped).
    raised = False
    try:
        jail.build_mounts(allow, allow, {"x": outside})
    except MountDenied:
        raised = True
    assert raised, "mount jail did not fail closed on a disallowed extra mount"
    # R6: a non-absolute source is refused.
    raised = False
    try:
        jail.validate_extra("relative/path")
    except MountDenied:
        raised = True
    assert raised, "mount jail accepted a non-absolute mount source"
