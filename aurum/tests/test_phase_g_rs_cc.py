"""Phase G(e) — RS + CC wired into the kernel: background organ work is scheduled through RS
(foreground preempts; aging guards starvation), and CC surfaces the concentration systemic-risk
signal (the MGC do-not-retire / TCM harden-or-split feed) over the live EL."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from aurum.kernel import GovernanceKernel
from aurum.observability.concentration_check import ConcentrationCheck
from aurum.support.resource_scheduler import ResourceScheduler


def _append(el, object_ids):
    el.append({"event_id": "", "timestamp": "", "source_organ": "T", "action_type": "PROMOTION",
               "object_ids": object_ids, "payload": {}, "evidence_confidence": 0.9,
               "evidence_source": "t", "prev_hash": "", "hash": ""})


def test_kernel_owns_rs_and_cc(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    assert isinstance(k.rs, ResourceScheduler) and isinstance(k.cc, ConcentrationCheck)


def test_background_scheduling_foreground_preempts_then_goal_relevance(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.submit_background("aa-discovery", {"priority": 1, "goal_relevance": 0.9})
    k.submit_background("mgc-cleanup", {"priority": 1, "goal_relevance": 0.1})
    k.submit_background("user-task", {"priority": 1, "foreground": True})
    assert k.next_background() == "user-task"        # foreground preempts all background
    assert k.next_background() == "aa-discovery"     # then higher goal-relevance
    assert k.next_background() == "mgc-cleanup"


def test_preempt_background_requeues(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.submit_background("bg", {"priority": 5})
    assert k.next_background() == "bg"
    assert k.preempt_background("user request arrived") == "bg"


def test_concentration_risks_feed_from_live_el(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    for _ in range(20):
        _append(k.el, ["hot"])                       # an artifact servicing a huge share
    _append(k.el, ["rare"])
    risks = k.concentration_risks()
    assert "hot" in risks and "rare" not in risks    # the MGC/TCM systemic-risk feed
