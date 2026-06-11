"""SH (Shadow Mode) — simulate runs the real action against a copy (no side effects); commit is
gated by an 'ok' verdict and consumes it (no replay)."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from aurum.build_state import is_built
from aurum.support.shadow_mode import ShadowMode


def _deploy_action():
    return {"id": "deploy-1", "state": {"deployed": False},
            "apply": lambda s: s.__setitem__("deployed", True)}


def test_simulate_has_no_side_effects_and_returns_diff():
    sh = ShadowMode()
    action = _deploy_action()
    diff = sh.simulate(action)
    assert diff["verdict"] == "ok" and diff["changed"] is True
    assert diff["before"] == {"deployed": False} and diff["after"] == {"deployed": True}
    assert action["state"] == {"deployed": False}, "simulate must NOT touch the real state"


def test_commit_refused_without_simulate():
    sh = ShadowMode()
    res = sh.commit(_deploy_action())
    assert res["committed"] is False and "not simulated" in res["reason"]


def test_simulate_then_commit_applies_for_real():
    sh = ShadowMode()
    action = _deploy_action()
    sh.simulate(action)
    res = sh.commit(action)
    assert res["committed"] is True
    assert action["state"] == {"deployed": True}, "commit must apply the real side effect"


def test_verdict_is_consumed_no_replay():
    sh = ShadowMode()
    action = _deploy_action()
    sh.simulate(action)
    assert sh.commit(action)["committed"] is True
    again = sh.commit(action)                       # second commit off one simulate
    assert again["committed"] is False and "not simulated" in again["reason"]


def test_failing_simulation_blocks_commit():
    def boom(_s):
        raise RuntimeError("sim blew up")

    sh = ShadowMode()
    action = {"id": "x", "state": {"v": 1}, "apply": boom}
    diff = sh.simulate(action)
    assert diff["verdict"] == "error" and "RuntimeError" in diff["error"]
    assert action["state"] == {"v": 1}              # no side effect even on a failing sim
    res = sh.commit(action)
    assert res["committed"] is False and "error" in res["reason"]


def test_sh_is_built():
    assert is_built("SH")
