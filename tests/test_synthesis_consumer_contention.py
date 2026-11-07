"""A shared consumer lease defers a tick without hiding processing failures."""

from unittest.mock import patch

import pytest

from tests.test_tick_services import _FakeOrchestrator, _state
from tests.test_web_workflow_proposals import _write_config
from zf.core.config.schema import ZfConfig
from zf.core.state.locks import locked_path
from zf.runtime.tick_services import (
    TickServiceIntervals, TickServiceState, run_standard_tick_services,
)
from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations
from zf.web.workflow_preparation_worker import WorkflowPreparationWorker


def _tick(orch):
    return run_standard_tick_services(
        orch, state=TickServiceState(), now=1.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=999, bug_scan_s=999, supervisor_inspection_s=999,
            spine_projection_s=999, cost_blackout_check_s=999, stillness_audit_s=999,
        ),
    )


def _failures(orch):
    return [e for e in orch.event_log.read_all()
            if e.type == "orchestrator.tick.failed"
            and e.payload.get("component") == "workflow_synthesis_consumer"]


def test_busy_consumer_does_not_report_failed_runtime_tick(tmp_path):
    orch = _FakeOrchestrator(_state(tmp_path), ZfConfig())
    with locked_path(orch.state_dir / "projections/workflow-synthesis-consumer.guard"):
        _tick(orch)
    assert _failures(orch) == []
    assert consume_workflow_synthesis_operations(
        state_dir=orch.state_dir, project_root=tmp_path, config=orch.config,
        writer=orch.event_writer,
    ) == 0


def test_lease_contention_is_typed_without_reading_or_writing_events(tmp_path):
    orch = _FakeOrchestrator(_state(tmp_path), ZfConfig())
    before = orch.event_log.read_all()
    def forbidden_read():
        pytest.fail("busy consumer must not enter the leased body")
    with locked_path(orch.state_dir / "projections/workflow-synthesis-consumer.guard"):
        with pytest.raises(TimeoutError) as caught:
            consume_workflow_synthesis_operations(
                state_dir=orch.state_dir, project_root=tmp_path, config=orch.config,
                writer=orch.event_writer, read_operation_events=forbidden_read,
            )
    assert type(caught.value).__name__ == "WorkflowSynthesisConsumerBusy"
    assert orch.event_log.read_all() == before


def test_processing_timeout_still_reports_failed_runtime_tick(tmp_path):
    orch = _FakeOrchestrator(_state(tmp_path), ZfConfig())
    def read_timeout():
        raise TimeoutError("operation event read timed out")
    def consume(**kwargs):
        return consume_workflow_synthesis_operations(
            **kwargs, read_operation_events=read_timeout,
        )
    with patch("zf.runtime.workflow_synthesis.consume_workflow_synthesis_operations", consume):
        _tick(orch)
    failure, = _failures(orch)
    assert failure.payload["error_type"] == "TimeoutError"
    assert failure.payload["error"] == "operation event read timed out"


def test_web_busy_consumer_does_not_mark_fingerprint_seen(tmp_path):
    _write_config(tmp_path / "zf.yaml")
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "events.jsonl").touch()
    worker = WorkflowPreparationWorker()
    with locked_path(state / "projections/workflow-synthesis-consumer.guard"):
        worker._consume(tmp_path)
    assert tmp_path not in worker._seen
    worker._consume(tmp_path)
    assert tmp_path in worker._seen
