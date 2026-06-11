"""Tier-1 review fixes — bugs caught by the full-codebase review of the spine + cage.

- EL canonical hashing: verify_chain must NOT reject a clean ledger over an int evidence_confidence
  or an omitted/extra field (the hash is now computed over the canonical stored form).
- PK gate TTL fail-closed: an expired gate cannot be approved, even if check_gate never ran.
- PK justification type-robustness: a bare-string source is ONE source (not chars); a malformed
  type fails closed (deny), not open.
- AG restore_authority reconstructs the EARNED band (no silent downgrade of gap-resting values).
- Cage broker: a turn timeout is enforced and the container is reaped; argv supports a name.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from aurum.cage import broker as cb
from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.novel.authority_governor import AuthorityGovernor
from aurum.spine.policy_kernel import PolicyKernel


def _el():
    return EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))


def _ev(**over):
    e = {"event_id": "", "timestamp": "", "source_organ": "GOV", "action_type": "VOTE",
         "object_ids": ["x"], "payload": {"k": "v"}, "evidence_confidence": 1.0,
         "evidence_source": "test", "prev_hash": "", "hash": ""}
    e.update(over)
    return e


# ── EL: verify_chain stays True on int confidence / omitted / extra fields ─────
def test_el_verify_clean_with_int_confidence_omitted_objectids_and_extra_key():
    el = _el()
    el.append(_ev(evidence_confidence=1))                  # int, not float
    e2 = _ev(evidence_confidence=0)                        # int zero
    del e2["object_ids"]                                   # omitted entirely
    el.append(e2)
    el.append(_ev(capability_class="ignored-top-level"))   # an extra top-level key
    assert el.verify_chain() is True                       # a CLEAN ledger must verify


def test_el_verify_still_detects_tampering():
    el = _el()
    el.append(_ev())
    # the append-only trigger (AURUM_ERR_001) blocks UPDATE — drop it to simulate an out-of-band
    # tamper (a direct file/DB edit) and confirm the canonical-hashing change did NOT weaken
    # verify_chain's detection.
    for (name,) in el._db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        el._db.execute(f"DROP TRIGGER {name}")
    el._db.execute("UPDATE evidence_ledger SET payload=? WHERE seq=1", ('{"k":"TAMPERED"}',))
    assert el.verify_chain() is False                      # detection preserved


# ── PK: an expired gate cannot be approved (fail-closed TTL) ───────────────────
def _gate_pk():
    pk = PolicyKernel(rules=[{"rule_id": "g", "action_type": "promote",
                              "decision": "needs_gate", "ttl_seconds": 100}])
    pk.check({"action_type": "promote", "justification_sources": ["operator"]})
    gid = pk.open_gates()[0]["gate_id"]
    return pk, gid, pk._gates[gid]["created_at"]


def test_pk_expired_gate_cannot_be_approved():
    pk, gid, created = _gate_pk()
    with pytest.raises(PermissionError):
        pk.approve_gate(gid, "dan", now=created + 101)     # past TTL → refused
    assert pk.check_gate(gid, now=created + 101) == "expired"


def test_pk_gate_approval_within_ttl_still_works():
    pk, gid, created = _gate_pk()
    pk.approve_gate(gid, "dan", now=created + 10)           # within TTL
    assert pk.check_gate(gid, now=created + 10) == "approved"


# ── PK: justification type-robustness ──────────────────────────────────────────
def test_pk_string_justification_is_one_source():
    pk = PolicyKernel()
    assert pk.trace_justification({"justification_sources": "operator"}) == ["operator"]
    assert pk.check({"action_type": "x",
                     "justification_sources": "operator"})["decision"] != "deny"


def test_pk_malformed_justification_fails_closed():
    pk = PolicyKernel()
    res = pk.check({"action_type": "x", "justification_sources": {"weird": 1}})
    assert res["decision"] == "deny" and res["rule_id"] == "pk:injection-boundary"


# ── AG: restore_authority reconstructs the EARNED band (no gap downgrade) ───────
def test_ag_restore_keeps_earned_band_in_hysteresis_gap():
    ag = AuthorityGovernor()
    ag.restore_authority("file_write", 0.75)               # code band (gap: code demote 0.70)
    assert ag.band("file_write") == "code"                 # was downgraded to readonly pre-fix
    ag.restore_authority("net", 0.55)
    assert ag.band("net") == "readonly"
    ag.restore_authority("x", 0.96)
    assert ag.band("x") == "full"


# ── Cage broker: turn timeout + named container ────────────────────────────────
def test_broker_turn_timeout_parsing(monkeypatch):
    monkeypatch.setenv("AURUM_CAGE_TURN_TIMEOUT_SEC", "not-a-number")
    assert cb._turn_timeout() == 600.0                     # bad value → default
    monkeypatch.setenv("AURUM_CAGE_TURN_TIMEOUT_SEC", "0")
    assert cb._turn_timeout() == 600.0                     # non-positive → default (never disables)
    monkeypatch.setenv("AURUM_CAGE_TURN_TIMEOUT_SEC", "30")
    assert cb._turn_timeout() == 30.0


def test_broker_argv_name_is_optional_and_present_when_given():
    argv = cb.build_docker_argv([], {}, name="aurum-turn-abc")
    assert "--name" in argv and "aurum-turn-abc" in argv
    assert "--name" not in cb.build_docker_argv([], {})    # back-compat: no name → no flag


def test_broker_docker_runner_kills_and_reaps_on_timeout(monkeypatch):
    killed = {"v": False}

    class _Hang:
        returncode = None

        async def communicate(self, data=None):
            await asyncio.sleep(10)                         # never returns within the timeout

        def kill(self):
            killed["v"] = True

        async def wait(self):
            return 0

    class _Quick:
        returncode = 0

        async def communicate(self, data=None):
            return (b"", b"")

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_exec(*argv, **_):
        if argv[:3] == ("docker", "rm", "-f"):             # the reap call
            return _Quick()
        return _Hang()                                     # the (hanging) turn

    monkeypatch.setattr(cb.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("AURUM_CAGE_TURN_TIMEOUT_SEC", "0.05")
    res = asyncio.run(cb.docker_runner({"prompt": "x"}, [], {}))
    assert res["status"] == "error" and "timeout" in res["error"]
    assert killed["v"] is True                             # the hung container process was killed
