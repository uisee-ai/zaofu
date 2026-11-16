import pytest

from zf.core.events import ZfEvent
from zf.runtime.recovery_tick_wake import RECOVERY_INPUTS, has_new_recovery_input


@pytest.mark.parametrize("kind", sorted(RECOVERY_INPUTS))
def test_recovery_fact_wakes_only_after_consumed_cursor(kind):
    before = ZfEvent(type="run.manager.tick.completed")
    fact = ZfEvent(type=kind)
    after = ZfEvent(type="agent.usage")
    assert has_new_recovery_input([before, fact, after], before.id)
    assert not has_new_recovery_input([before, fact, after], fact.id)
    assert not has_new_recovery_input([before, fact, after], after.id)


def test_bookkeeping_and_unknown_events_do_not_create_fast_feedback():
    rows = [ZfEvent(type=kind) for kind in (
        "run.manager.tick.started", "run.manager.tick.completed", "agent.usage",
        "worker.heartbeat", "orchestrator.round.complete", "future.unclassified.fact",
    )]
    assert not has_new_recovery_input(rows, "")
