"""Phase G(b) / H2 — SH wired into the kernel via a TRUE dry-run/commit split: an irreversible
action is governed, a SIDE-EFFECT-FREE preview is run, and the real commit fires only on a clean
preview. A gated action never previews/commits; a failing preview blocks the commit and the real
outward op never fires (the H2 containment property)."""
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


def test_gated_irreversible_never_previews_or_commits(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    pv, cm = [], []
    res = k.shadow_commit(_irrev_action(), preview=lambda: pv.append(1), commit=lambda: cm.append(1))
    assert res["governed"] is False and res["committed"] is False   # AG ceiling denies at baseline
    assert pv == [] and cm == []                                    # neither ran


def test_allowed_irreversible_previews_then_commits(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    _raise_to_full(k, "network")
    log = []
    res = k.shadow_commit(
        _irrev_action(),
        preview=lambda: log.append("previewed") or {"plan": "would POST"},
        commit=lambda: log.append("committed") or "done")
    assert res["governed"] is True and res["committed"] is True
    assert res["plan"] == {"plan": "would POST"} and res["result"] == "done"
    assert log == ["previewed", "committed"]            # preview first, THEN the real commit


def test_failing_preview_blocks_commit_no_real_side_effect(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    _raise_to_full(k, "network")
    posted = []

    def preview():
        raise RuntimeError("dry-run failed")

    res = k.shadow_commit(_irrev_action(), preview=preview, commit=lambda: posted.append("POST"))
    assert res["governed"] is True and res["committed"] is False
    assert posted == []                                 # H2: the real outward op NEVER fired


def test_reversible_action_has_no_shadow_gate(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    res = k.shadow_commit(to_action("write_file", {"path": "x"}))   # code band, not irreversible
    assert res["governed"] is True and res["committed"] is None
