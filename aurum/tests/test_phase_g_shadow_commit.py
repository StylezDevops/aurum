"""Phase G(b) — SH wired into the kernel: an irreversible action is governed, then shadow-
simulated (no side effects), and only committed on a clean verdict. A gated action never
simulates/commits; a failing simulation blocks the commit even after governance allows. The
execution effect (state/apply) is kept SEPARATE from the governance action."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from aurum.action_map import to_action
from aurum.kernel import GovernanceKernel
from aurum.support.sh import ShadowMode

_IRREV = {"capability_class": "network", "action_class": "commit_outward",
          "risk_tier": "consequential", "irreversible": True}


def _raise_to_full(k, cc):
    for i in range(40):
        if k.ag.band(cc) == "full":
            break
        k.record_outcome_verdict(f"t{i}", cc, satisfied=True)


def _irrev_action():
    return to_action("http_post", {"url": "x"}, classification=_IRREV)


def test_kernel_owns_shadow(tmp_path):
    assert isinstance(GovernanceKernel(home=str(tmp_path)).sh, ShadowMode)


def test_gated_irreversible_never_simulates_or_commits(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    state = {"done": False}
    res = k.shadow_commit(_irrev_action(), state=state, apply=lambda s: s.__setitem__("done", True))
    assert res["governed"] is False and res["committed"] is False   # AG ceiling denies at baseline
    assert state == {"done": False}                                 # no side effect, no shadow run


def test_allowed_irreversible_simulates_then_commits(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    _raise_to_full(k, "network")
    state = {"done": False}
    res = k.shadow_commit(_irrev_action(), state=state, apply=lambda s: s.__setitem__("done", True))
    assert res["governed"] is True and res["committed"] is True
    assert res["shadow"]["verdict"] == "ok"
    assert state == {"done": True}                                  # real commit applied after sim


def test_failing_simulation_blocks_commit_even_when_governed(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    _raise_to_full(k, "network")
    state = {"done": False}

    def boom(_s):
        raise RuntimeError("dry-run failed")

    res = k.shadow_commit(_irrev_action(), state=state, apply=boom)
    assert res["governed"] is True and res["committed"] is False
    assert res["shadow"]["verdict"] == "error"
    assert state == {"done": False}                                 # governance allowed, shadow blocked


def test_reversible_action_has_no_shadow_gate(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    res = k.shadow_commit(to_action("write_file", {"path": "x"}))   # code band, not irreversible
    assert res["governed"] is True and res["committed"] is None
