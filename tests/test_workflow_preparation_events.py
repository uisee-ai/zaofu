"""Preparation uses canonical operation refs without hydrating unrelated events."""
import sqlite3

import pytest

from tests.test_control_actions_workflow_request import _service, _execute
from tests.test_workflow_prepare import _provider, _consume
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.web.projections import metric_facts
from zf.web.projections.preparation_events import read_preparation_events
from zf.web.workflow_preparation_worker import WorkflowPreparationWorker


def test_duplicate_startup_callbacks_share_one_preparation_scheduler(monkeypatch):
    worker = WorkflowPreparationWorker()
    monkeypatch.setattr(worker, "_run", lambda: worker._stop.wait())
    worker.start()
    first = worker._thread
    try:
        worker.start()
        assert worker._thread is first
    finally:
        worker.stop()
    assert not first.is_alive()


def test_indexed_preparation_preserves_archive_append_and_full_operation_state(tmp_path, monkeypatch):
    service, log = _service(tmp_path)
    state = service.state_dir
    for i in range(30):
        log.append(ZfEvent(type="worker.heartbeat", actor="worker", payload={"body": "x" * 1000}))
    (state / "events").mkdir()
    (state / "events.jsonl").rename(state / "events" / "2026-10-26.jsonl")
    _execute(service, "idea-to-product", {"objective": "Repair sessions", "backend": "mock"})
    expected = reduce_workflow_operations(log.read_all())
    assert expected
    assert reduce_workflow_operations(read_preparation_events(state, service.config, log)) == expected
    metric_facts.refresh_events(state, service.config)
    decoded = []
    original = EventLog.decode_line

    def decode(self, line):
        event = original(self, line)
        decoded.append(event.type if event else "invalid")
        return event

    monkeypatch.setattr(EventLog, "decode_line", decode)
    monkeypatch.setattr(EventLog, "read_all", lambda self: pytest.fail("idle preparation replayed full log"))
    assert reduce_workflow_operations(read_preparation_events(state, service.config, log)) == expected
    assert decoded and all(kind.startswith("workflow.operation.") for kind in decoded)


@pytest.mark.parametrize("failure", [sqlite3.DatabaseError("unavailable"), ValueError("bad offset")])
def test_failed_index_falls_back_to_canonical_events(tmp_path, monkeypatch, failure):
    service, log = _service(tmp_path)
    _execute(service, "idea-to-product", {"objective": "Repair sessions", "backend": "mock"})
    expected = log.read_all()
    def broken(*args, **kwargs):
        raise failure
    monkeypatch.setattr(metric_facts, "events", broken)
    assert read_preparation_events(service.state_dir, service.config, log) == expected


def test_moving_source_does_not_admit_partial_preparation(tmp_path, monkeypatch):
    service, log = _service(tmp_path)
    counter = iter(range(6))
    monkeypatch.setattr("zf.web.projections.preparation_events.fingerprint", lambda *args: next(counter))
    with pytest.raises(TimeoutError, match="source changed"):
        read_preparation_events(service.state_dir, service.config, log)


def test_idle_worker_skips_full_replay_but_new_requests_still_prepare(tmp_path, monkeypatch):
    service, log = _service(tmp_path)
    calls = _provider(monkeypatch)
    _execute(service, "idea-to-product", {"objective": "First request", "backend": "mock"})
    assert _consume(service) == 1
    metric_facts.refresh_events(service.state_dir, service.config)
    worker = WorkflowPreparationWorker()
    with monkeypatch.context() as guarded:
        guarded.setattr(EventLog, "read_all", lambda self: pytest.fail("settled request replayed full log"))
        worker._consume(tmp_path)
    assert tmp_path in worker._seen  # The worker did not swallow a reader failure.
    _execute(service, "idea-to-product", {"objective": "Next request", "backend": "mock"})
    worker._consume(tmp_path)
    assert len(calls) == 2
