from __future__ import annotations

import pytest

from zf.core.config.schema import (
    FanoutAssignmentConfig, WorkflowAffinityLaneConfig, WorkflowAffinityLaneProfileConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from tests.test_reader_fanout_runtime import _state


@pytest.mark.parametrize("mode,receipt,current,expected", [
    ("blocking", True, True, 2), ("shadow", True, True, 0),
    ("blocking", False, True, 0), ("blocking", True, False, 0),
])
def test_frozen_candidate_dispatch_uses_reader_pool_not_planning_children(
    tmp_path, mode, receipt, current, expected,
):
    state, log, transport, runtime = _state(tmp_path)
    runtime.config.workflow.flow_metadata = {"task_pipeline": {"mode": mode}}
    stage = runtime.config.workflow.stages[0]
    stage.roles = []
    stage.assignment = FanoutAssignmentConfig(
        strategy="affinity_stage_slots", lane_profile="pool", stage_slot="verify",
    )
    runtime.config.workflow.affinity_lanes = {"pool": WorkflowAffinityLaneProfileConfig(lanes=[
        WorkflowAffinityLaneConfig(id="lane-0", verify="review-a"),
        WorkflowAffinityLaneConfig(id="lane-1", verify="review-b"),
    ])}
    writer = EventWriter(log)
    writer.append(ZfEvent(type="task.pipeline.generation.admitted", correlation_id="run-1", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-1",
        "generation_id": "generation-1" if current else "generation-2",
    }))
    trigger = writer.append(ZfEvent(type="candidate.ready", correlation_id="run-1", payload={
        "schema_version": "candidate-freeze-receipt.v1" if receipt else "legacy",
        "status": "frozen", "workflow_run_id": "run-1", "generation_id": "generation-1",
        "pdd_id": "P-1", "fanout_id": "planning-fanout-without-writer-children",
        "candidate_head_commit": "a" * 40,
    }))
    runtime.run_once(events=[trigger])
    assert len(transport.sent) == expected
    # The persisted configuration and legacy affinity guard are not rewritten.
    assert stage.assignment.strategy == "affinity_stage_slots"
    if expected:
        dispatched = [e for e in log.read_all() if e.type == "fanout.child.dispatched"]
        assert {e.payload["role_instance"] for e in dispatched} == {"review-a", "review-b"}
        assert all(e.payload["payload"]["candidate_head_commit"] == "a" * 40 for e in dispatched)
        runtime.run_once(events=[trigger])
        assert len(transport.sent) == expected
