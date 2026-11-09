"""Idle checks retain authority without rebuilding irrelevant event projections."""
from itertools import product

import pytest

from tests.test_scheduler_task_attempt import _runtime, _send
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.pause_lifecycle import (
    PAUSE_EVENT_TYPES, RESUME_EVENT_TYPES, is_dispatch_paused, project_pause_lifecycle,
)
from zf.runtime.task_attempt_operation_settlement import settle_admitted_operation_attempt


class CountingIndex(dict):
    scans = 0

    def values(self):
        self.scans += 1
        return super().values()


@pytest.mark.parametrize("status,mode,failure_class,settles", [
    ("succeeded", "enforce", "", False),
    ("failed", "enforce", "semantic_result_failed", False),
    ("expired", "enforce", "lease_expired", False),
    ("failed", "shadow", "semantic_result_failed", False),
    ("expired", "shadow", "lease_expired", True),
    ("failed", "shadow", "lease_expired", True),
    ("sent", "enforce", "", True),
])
def test_replayed_terminal_result_skips_full_scan_but_repair_still_settles(
    tmp_path, status, mode, failure_class, settles,
):
    runtime, _, state = _runtime(tmp_path)
    context = _send(runtime, state, dispatch_id="dispatch-1")
    runtime.config.workflow.task_attempt.mode = mode
    store = TaskAttemptStore(state / "task_attempts.json")
    store.update(context.attempt_id, status=status, failure_class=failure_class,
                 updated_at="2026-11-09T00:00:00+00:00")
    source = runtime.event_writer.append(ZfEvent(type="workflow.child.completed", actor="dev",
        payload={"workflow_run_id": "RUN-1", "operation_id": context.operation_id,
                 "attempt_id": context.dispatch_id, "status": "completed"}))
    admitted = ZfEvent(type="workflow.operation.settled", origin="kernel", task_id="TASK-1",
        payload={"workflow_run_id": "RUN-1", "operation_id": context.operation_id,
                 "admitted_call_result_ref": {"source_event_id": source.id}})
    index = CountingIndex((e.id, e) for e in runtime.event_log.read_all())
    assert settle_admitted_operation_attempt(runtime, admitted, events_by_id=index,
                                             attempt_rows=store.rows(), store=store) is settles
    assert index.scans == int(settles)
    assert store.get(context.attempt_id)["status"] == ("succeeded" if settles else status)


def test_pause_predicate_matches_projection_for_all_short_control_sequences(tmp_path):
    controls = sorted(PAUSE_EVENT_TYPES | RESUME_EVENT_TYPES)
    for kinds in product(controls, repeat=3):
        # Occurrence order is authoritative even when timestamps run backwards.
        events = [ZfEvent(type=kind, ts=f"2026-11-09T00:00:0{3-i}+00:00")
                  for i, kind in enumerate(kinds)]
        assert is_dispatch_paused(tmp_path, events=events) == project_pause_lifecycle(
            tmp_path, events=events)["paused"]
    assert not is_dispatch_paused(tmp_path, events=[])


def test_pause_predicate_does_not_materialize_older_history(tmp_path):
    class History(list):
        visited = 0
        def __iter__(self):
            raise AssertionError("Pause boolean must not build the full display projection")
        def __reversed__(self):
            for event in super().__reversed__():
                self.visited += 1
                yield event
    events = History([ZfEvent(type="worker.heartbeat") for _ in range(10000)] + [
        ZfEvent(type="dispatch.paused"), ZfEvent(type="worker.heartbeat")])
    assert is_dispatch_paused(tmp_path, events=events)
    assert events.visited == 2


def test_terminal_admitted_dispatch_does_not_settle_an_older_live_attempt(tmp_path):
    runtime, _, state = _runtime(tmp_path)
    context = _send(runtime, state, dispatch_id="older")
    store = TaskAttemptStore(state / "task_attempts.json")
    rows = store.rows()
    terminal = {**rows[0], "attempt_id": "newer", "dispatch_id": "newer", "status": "succeeded",
                "created_at": "2099-01-01T00:00:00+00:00"}
    source = ZfEvent(type="workflow.child.completed", payload={"attempt_id": "newer"})
    admitted = ZfEvent(type="workflow.operation.settled", origin="kernel", task_id="TASK-1",
        payload={"workflow_run_id": "RUN-1", "operation_id": context.operation_id,
                 "admitted_call_result_ref": {"source_event_id": source.id}})
    index = CountingIndex({source.id: source})
    assert not settle_admitted_operation_attempt(runtime, admitted, events_by_id=index,
                                                  attempt_rows=rows + [terminal], store=store)
    assert store.get(context.attempt_id)["status"] == "sent"
    assert index.scans == 1
