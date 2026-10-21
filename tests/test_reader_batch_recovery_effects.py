from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.run_manager import run_manager_tick, _settle_pending_action_effects
from zf.runtime.workflow_resume import build_workflow_resume_projection
from tests.test_run_manager import _state, _config, _append_repair_failed_children_fixture


def test_post_verify_reader_cannot_reopen_business_task_batch(tmp_path: Path) -> None:
    state, log, writer = _state(tmp_path)
    source = writer.append(ZfEvent(type="flow.discovery.requested", payload={
        "workflow_run_id": "run-reader", "pdd_id": "PRD-1",
        "task_map_ref": "artifacts/task_map.json", "candidate_ref": "worker/dev-3",
    }))
    writer.append(ZfEvent(type="fanout.started", payload={
        "fanout_id": "fanout-reader", "stage_id": "prd-post-verify-discovery",
        "topology": "fanout_reader", "trigger_event_id": source.id,
    }))
    writer.append(ZfEvent(type="fanout.aggregate.completed", payload={
        "workflow_run_id": "run-reader", "fanout_id": "fanout-reader",
        "stage_id": "prd-post-verify-discovery", "status": "failed",
        "failure_event": "flow.discovery.failed", "failed_children": ["flow-discovery"],
        "pdd_id": "PRD-1", "task_map_ref": "artifacts/task_map.json",
        "candidate_ref": "worker/dev-3", "candidate_head_commit": "candidate-head",
    }))
    writer.append(ZfEvent(type="flow.discovery.failed", payload={
        "workflow_run_id": "run-reader", "fanout_id": "fanout-reader",
        "failure_class": "verifier_execution_failure", "recommendation": "abstain",
    }))
    projection = build_workflow_resume_projection(state, _config(), events=log.read_all(), tasks=[])
    assert not projection["batch_checkpoints"]
    run_manager_tick(state_dir=state, writer=writer, config=_config(),
                     spawn_repairs=False, action_filter={"workflow-batch-resume"})
    assert not any(e.type == "task_map.ready" for e in log.read_all())


@pytest.mark.parametrize("effect_type, binding_key, case_status", [
    ("fanout.started", "trigger_event_id", "resolved"),
    ("task.pipeline.generation.admitted", "trigger_event_id", "resolved"),
    ("plan.rejected", "plan_id", "open"),
])
def test_batch_resume_waits_for_its_new_writer_admission(
    tmp_path: Path, effect_type: str, binding_key: str, case_status: str,
) -> None:
    state, log, writer = _state(tmp_path)
    _append_repair_failed_children_fixture(log)
    run_manager_tick(state_dir=state, writer=writer, config=_config(),
                     spawn_repairs=False, action_filter={"workflow-batch-resume"})
    pending = [e for e in log.read_all() if e.type == "run.manager.action.effect.pending"]
    assert len(pending) == 1
    ready = [e for e in log.read_all() if e.type == "task_map.ready"][-1]
    assert pending[0].payload["effect_request_event_id"] == ready.id
    case_id = pending[0].payload["recovery_case_id"]
    def recovery_status() -> str:
        return next(case["status"] for case in build_recovery_case_projection(log.read_all())["cases"]
                    if case["recovery_case_id"] == case_id)

    assert recovery_status() == "verifying"
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    writer.append(ZfEvent(type="fanout.started", payload={
        "fanout_id": "unrelated", "trigger_event_id": "another-request",
    }))
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    writer.append(ZfEvent(type=effect_type, payload={
        "fanout_id": "replacement-writer", binding_key: ready.id,
    }))
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert recovery_status() == case_status
