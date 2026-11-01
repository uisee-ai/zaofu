"""A queued stall recovery must not supersede the original fanout."""

import pytest

from tests.test_reader_fanout_runtime import _state
from tests.test_writer_fanout_runtime import _state as _writer_state, _seed_tasks
from zf.core.events.model import ZfEvent
from zf.runtime.fanout_retrigger_guard import suppress_handled_stall_trigger


@pytest.mark.parametrize("recovery_first", [False, True])
@pytest.mark.parametrize("kind", ["reader", "writer"])
def test_stall_recovery_and_original_dispatch_only_one_fanout(tmp_path, recovery_first, kind):
    state_dir, log, transport, runtime = (_state if kind == "reader" else _writer_state)(tmp_path)
    if kind == "writer":
        _seed_tasks(state_dir)
    original = ZfEvent(type="candidate.ready" if kind == "reader" else "task_map.ready",
                       actor="zf-cli", correlation_id="trace-1",
                       payload={"pdd_id": "F-11111111"})
    recovery = ZfEvent(
        type=original.type, actor="zf-stall-redispatch", correlation_id=original.correlation_id,
        payload={**original.payload, "original_trigger_event_id": original.id,
                 "redispatch_attempt": 1, "redispatch_fingerprint": "stall:original"},
    )
    log.append(original)
    log.append(recovery)
    order = [recovery, original] if recovery_first else [original, recovery]
    runtime.run_once(events=order)
    events = log.read_all()
    assert len([e for e in events if e.type == "fanout.started"]) == 1
    assert len(transport.sent) == 2  # One call per configured worker, no replacement.
    suppressed = [e for e in events if e.type == "fanout.retrigger.suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0].payload["reason"] == "stall_trigger_already_handled"
    assert suppressed[0].payload["trigger_event_id"] == order[1].id
    runtime.run_once(events=order)
    assert len([e for e in log.read_all() if e.type == "fanout.started"]) == 1
    assert len([e for e in log.read_all() if e.type == "fanout.retrigger.suppressed"]) == 1


def test_explicit_new_rework_is_not_coalesced_with_original(tmp_path):
    _state_dir, log, _transport, runtime = _state(tmp_path)
    original = ZfEvent(type="candidate.ready", actor="zf-cli", correlation_id="trace-1",
                       payload={"pdd_id": "F-11111111"})
    rework = ZfEvent(type=original.type, actor="operator", correlation_id=original.correlation_id,
                     payload={**original.payload, "rework_of": original.id})
    log.append(original)
    log.append(rework)
    runtime.run_once(events=[original, rework])
    assert len([e for e in log.read_all() if e.type == "fanout.started"]) == 2
    assert not any(e.type == "fanout.retrigger.suppressed" for e in log.read_all())


@pytest.mark.parametrize("difference", ["stage", "run", "payload", "task"])
def test_recovery_guard_does_not_suppress_other_scope(tmp_path, difference):
    _state_dir, log, _transport, runtime = _state(tmp_path)
    original = ZfEvent(type="candidate.ready", actor="zf-cli", task_id="T1",
                       correlation_id="run-1", payload={"candidate_ref": "commit-1"})
    recovery = ZfEvent(
        type=original.type, actor="zf-stall-redispatch",
        task_id="T2" if difference == "task" else original.task_id,
        correlation_id="run-2" if difference == "run" else original.correlation_id,
        payload={**original.payload, "original_trigger_event_id": original.id,
                 **({"candidate_ref": "commit-2"} if difference == "payload" else {})},
    )
    log.append(original)
    log.append(ZfEvent(type="fanout.started", payload={
        "stage_id": "other" if difference == "stage" else "review-candidate",
        "trigger_event_id": original.id, "fanout_id": "fanout-original",
    }))
    assert not suppress_handled_stall_trigger(runtime, trigger_event=recovery, stage_id="review-candidate")
