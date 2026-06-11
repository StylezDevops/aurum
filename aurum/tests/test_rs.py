"""RS (Resource Scheduler) — foreground preempts background; goal-relevance orders; the aging
guard means a perpetually-deferred low-priority job eventually runs rather than starving."""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from aurum.build_state import is_built
from aurum.support.resource_scheduler import ResourceScheduler


def test_submit_and_next_basic():
    rs = ResourceScheduler()
    assert rs.next() is None                         # empty
    rs.submit("job-a", {"priority": 1})
    assert rs.next() == "job-a"
    assert rs.next() is None


def test_foreground_preempts_background():
    rs = ResourceScheduler()
    rs.submit("bg", {"priority": 9})                 # high-priority background
    rs.submit("fg", {"priority": 1, "foreground": True})
    assert rs.next() == "fg"                         # foreground wins despite lower priority


def test_goal_relevance_orders_background():
    rs = ResourceScheduler()
    rs.submit("mgc-cleanup", {"priority": 1, "goal_relevance": 0.1})
    rs.submit("aa-discovery", {"priority": 1, "goal_relevance": 0.9})
    assert rs.next() == "aa-discovery"               # higher goal-relevance runs first


def test_low_priority_job_eventually_escalates_not_starves():
    rs = ResourceScheduler()
    rs.submit("low", {"priority": 1})
    dispatched = []
    for i in range(12):
        rs.submit(f"high-{i}", {"priority": 5})
        dispatched.append(rs.next())
    assert "low" in dispatched, "the low-priority job starved — aging guard failed"


def test_preempt_requeues_running_background_job():
    rs = ResourceScheduler()
    rs.submit("bg", {"priority": 5})
    assert rs.next() == "bg"                         # now running
    assert rs.preempt("user request arrived") == "bg"
    assert "bg" in rs.backlog()                      # yielded back to the queue


def test_preempt_noop_when_idle_or_foreground():
    rs = ResourceScheduler()
    assert rs.preempt("x") is None                   # nothing running
    rs.submit("fg", {"priority": 1, "foreground": True})
    rs.next()
    assert rs.preempt("x") is None                   # foreground isn't preempted


def test_rs_is_built():
    assert is_built("RS")
