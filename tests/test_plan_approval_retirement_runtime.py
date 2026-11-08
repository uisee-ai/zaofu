"""Writer recovery uses the same approval retirement as CLI projections."""

import pytest

from tests.test_writer_fanout_runtime import _approval_orch, _approval_start
from zf.core.events import EventWriter, ZfEvent
from zf.runtime.plan_approval_replay import pending_plan_requests


def _cancel(writer, trigger, *, origin="kernel", scope="plan_admission", cause=None):
    writer.append(ZfEvent(
        type="fanout.cancelled", origin=origin,
        causation_id=trigger.id if cause is None else cause,
        correlation_id="trace-1",
        payload={"trigger_event_id": trigger.id, "failure_scope": scope},
    ))


def _retry(writer, trigger):
    return writer.append(ZfEvent(
        type="task_map.ready", correlation_id="trace-1",
        payload={"pdd_id": "F-11111111", "rework_of": trigger.id},
    ))


@pytest.mark.parametrize("already_suppressed", [False, True])
def test_retired_approval_allows_writer_recovery_without_auto_approval(
    tmp_path, already_suppressed,
):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    original = _approval_start(orch, log)
    writer = EventWriter(log)
    if already_suppressed:
        retry = _retry(writer, original)
        orch.run_once(events=[retry])
        assert len(pending_plan_requests(log.read_all())) == 1
    _cancel(writer, original)
    assert not pending_plan_requests(log.read_all())
    if not already_suppressed:
        retry = _retry(writer, original)
        orch.run_once(events=[retry])
    for _ in range(2):
        orch._resume_unstarted_rework_task_maps(log.read_all())
    pending = pending_plan_requests(log.read_all())
    assert [e.payload["plan_id"] for e in pending] == [retry.id]
    assert len([e for e in log.read_all() if e.type == "plan.approval.requested"]) == 2
    assert not any(e.type in {"plan.approved", "fanout.started"} for e in log.read_all())


@pytest.mark.parametrize("invalid", [
    {"origin": "external"}, {"scope": "worker_failure"}, {"cause": "other-trigger"},
])
def test_untrusted_or_unrelated_cancel_does_not_retire_pending(tmp_path, invalid):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    original = _approval_start(orch, log)
    writer = EventWriter(log)
    _cancel(writer, original, **invalid)
    retry = _retry(writer, original)
    orch.run_once(events=[retry])
    assert [e.payload["plan_id"] for e in pending_plan_requests(log.read_all())] == [original.id]
    assert len([e for e in log.read_all() if e.type == "plan.approval.requested"]) == 1
