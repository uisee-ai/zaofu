from tests.test_quiescent import _ev, _cfg, _NOW
from zf.runtime.quiescent import quiescent_now


def test_approved_plan_wakes_quiescence():
    events = [
        _ev("human.escalate", minutes_ago=30),
        _ev("plan.approved", minutes_ago=1),
    ]
    assert not quiescent_now(
        events, config=_cfg(), now_epoch=_NOW.timestamp()
    ).quiescent


def test_controlled_retry_wakes_but_worker_noise_does_not():
    for origin, wakes in [("external", False), ("kernel", True)]:
        event = _ev("task.rework.requested", minutes_ago=1)
        event.origin = origin
        event.payload = {
            "source": "task_pipeline_operator_retry",
            "operation_id": "op-1",
        }
        status = quiescent_now(
            [_ev("human.escalate", minutes_ago=30), event],
            config=_cfg(),
            now_epoch=_NOW.timestamp(),
        )
        assert status.quiescent != wakes
