"""Falsification correctness cluster: C2 (governance-breach floor fires from a STRUCTURAL tag, not
a proxy result), H1 (in-memory authority can't diverge from EL on a failed append), M1 (a denied
action doesn't pollute the within-turn taint chain), H2 (the chain log is bounded).
"""
from __future__ import annotations

import pytest

from aurum.action_map import to_action
from aurum.build_state import is_built
from aurum.kernel import GovernanceKernel

pytestmark = pytest.mark.skipif(
    not is_built("PK", "EL", "AG", "CA", "BB"),
    reason="governance organs not all built",
)

_GOV = {"capability_class": "file_write", "action_class": "commit_outward",
        "risk_tier": "consequential", "governance_class": "destructive_data_loss"}


# ── C2: a structural must-never tag floors authority — even on a proxy-"satisfied" outcome ─────
def test_structural_governance_breach_floors_authority(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    cc = "file_write"
    # the most dangerous case: a governance breach that "succeeds" by the proxy measure.
    k.observe_outcome(to_action("danger", {}, classification=_GOV),
                      {"completed": True, "proxy_satisfied": True, "quality": 1.0})
    assert k.ag.authority(cc) == k.ag.kinetics()["floor"]   # floored, not one-band nudged


def test_governance_floors_lower_than_a_plain_failure(tmp_path):
    cc = "file_write"
    k1 = GovernanceKernel(home=str(tmp_path / "a"))
    k2 = GovernanceKernel(home=str(tmp_path / "b"))
    k1.observe_outcome(to_action("danger", {}, classification=_GOV),
                       {"completed": True, "proxy_satisfied": True, "quality": 1.0})
    k2.observe_outcome(to_action("write_file", {"path": "/x"}),      # no governance_class
                       {"completed": False, "proxy_satisfied": False, "quality": 0.3})
    assert k1.ag.authority(cc) == k1.ag.kinetics()["floor"]          # governance → floor
    assert k2.ag.authority(cc) > k1.ag.authority(cc)                 # plain bad → demoted, not floored


def test_proxy_result_text_cannot_forge_a_breach(tmp_path):
    # The masquerade guard: governance severity comes from the ACTION's structural tag, never from
    # the runtime result. A result that merely mentions a breach must NOT floor.
    k = GovernanceKernel(home=str(tmp_path))
    cc = "file_write"
    base = k.ag.authority(cc)
    k.observe_outcome(to_action("write_file", {"path": "/x"}),       # untagged action
                      {"completed": True, "proxy_satisfied": True, "quality": 1.0,
                       "error": '{"error":"credential_exfil tenant_boundary constitutional"}'})
    assert k.ag.authority(cc) == base    # proxy-good + scary text → unchanged (no forged floor)


# ── H1: a failed EL append cannot leave in-memory authority ahead of the ledger ───────────────
def test_authority_does_not_diverge_from_el_on_append_failure(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    cc = "file_write"
    before = k.ag.authority(cc)

    def boom(*_a, **_k):
        raise RuntimeError("disk gone")

    k.el.append = boom  # type: ignore[method-assign]   # the TRUST_CHANGE audit append will fail
    with pytest.raises(Exception):
        k.ag.set_authority(cc, 0.99)                     # write-then-act: audit FIRST → raises
    assert k.ag.authority(cc) == before                  # in-memory did NOT move (no divergence)


# ── M1: a denied action does not persist in the within-turn chain log ─────────────────────────
def test_denied_action_does_not_pollute_the_chain_log(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    # an injection-sourced action is denied by PK; it must not enter _action_log
    denied = to_action("write_file", {"path": "/x"}, justification_sources=["aa-fetched-spec"])
    assert k.govern(denied).allow is False
    assert len(k._action_log) == 0                       # blocked attempt left no taint residue
    # an allowed consequential action DOES persist (it executed)
    assert k.govern(to_action("write_file", {"path": "/y"})).allow is True
    assert len(k._action_log) == 1


# ── H2: the chain log is bounded (a long-lived host kernel can't grow it unbounded) ────────────
def test_action_log_is_bounded(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    assert k._action_log.maxlen == 256
    for i in range(400):                                 # more than the bound, no new_turn
        k.govern(to_action("write_file", {"path": f"/f{i}"}))
    assert len(k._action_log) <= 256                     # capped, not 400
