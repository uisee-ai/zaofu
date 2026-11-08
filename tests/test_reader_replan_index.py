"""Reader retry decisions retain order and scope across a shared projection."""
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


def config():
    stage = SimpleNamespace(id="issue-triage", topology="fanout_reader",
                            attempt_domain="plan", trigger="issue.requested",
                            aggregate=SimpleNamespace(failure_event="issue.triage.failed",
                                                      success_event="issue.triage.done"))
    return SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))


def event(kind, run="run", **payload):
    return ZfEvent(type=kind, correlation_id=run, payload={"workflow_run_id": run, **payload})


@pytest.mark.parametrize("ending", ["pending", "success", "replanned", "cap", "other-run"])
def test_shared_index_preserves_retry_decision(ending):
    from zf.runtime.reader_replan_index import ReaderReplanIndex

    origin = event("issue.requested", target_ref="requirements.md")
    failure = event("issue.triage.failed")
    events = [origin, failure]
    if ending == "success":
        events.append(event("issue.triage.done"))
    elif ending == "replanned":
        events.append(ZfEvent(type="issue.requested", causation_id=failure.id))
    elif ending == "cap":
        events[1:1] = [event("issue.triage.failed"), event("issue.triage.failed")]
    elif ending == "other-run":
        events[1:1] = [event("issue.triage.failed", "other"), event("issue.triage.done", "other")]
    events[1:1] = [event("codex.hook.post_tool_use", source_event_id=failure.id) for _ in range(100)]
    plain, reason = plan_reader_stage_replan(config(), events, failure)
    indexed, indexed_reason = plan_reader_stage_replan(
        config(), events, failure, event_index=ReaderReplanIndex(events))
    assert indexed_reason == reason
    assert (indexed is None) == (plain is None)
    if plain:
        assert indexed.payload == plain.payload
        assert indexed.causation_id == plain.causation_id
        assert indexed.type == plain.type


def test_changed_snapshot_cannot_reuse_index_and_new_success_takes_effect():
    from zf.runtime.reader_replan_index import ReaderReplanIndex

    failure = event("issue.triage.failed")
    events = [event("issue.requested", target_ref="requirements.md"), failure]
    index = ReaderReplanIndex(events)
    assert plan_reader_stage_replan(config(), events, failure, event_index=index)[0]
    events.append(event("issue.triage.done"))
    with pytest.raises(ValueError, match="snapshot"):
        plan_reader_stage_replan(config(), events, failure, event_index=index)
    assert plan_reader_stage_replan(config(), events, failure, event_index=ReaderReplanIndex(events))[1] == "stale_failure"


def test_workflow_resume_rebuilds_reader_checkpoints_after_new_success(tmp_path):
    from zf.core.config.schema import FanoutAggregateConfig, WorkflowConfig, WorkflowStageConfig, ZfConfig
    from zf.runtime.workflow_resume import build_workflow_resume_checkpoints

    runtime_config = ZfConfig(workflow=WorkflowConfig(stages=[WorkflowStageConfig(
        id="issue-triage", topology="fanout_reader", attempt_domain="plan",
        trigger="issue.requested", aggregate=FanoutAggregateConfig(
            failure_event="issue.triage.failed", success_event="issue.triage.done"),
    )]))
    failure = event("issue.triage.failed")
    events = [event("issue.requested", target_ref="requirements.md"), failure]
    def reader_rows():
        return [row for row in build_workflow_resume_checkpoints(
            events=events, tasks=[], config=runtime_config, state_dir=tmp_path)
                if row.safe_resume_action == "needs_stage_replan"]
    rows = reader_rows()
    assert len(rows) == 1 and rows[0].blocking_event_id == failure.id
    events.append(event("issue.triage.done"))
    assert reader_rows() == []


def test_one_projection_does_not_share_task_failure_caps():
    from zf.runtime.reader_replan_index import ReaderReplanIndex

    events = []
    failures = {}
    for task in ("B", "A"):
        source = ZfEvent(type="dev.blocked", task_id=task, payload={
            "workflow_run_id": "run", "operation_id": "impl-" + task,
            "task_pipeline_stage": "impl",
        })
        admission = event("workflow.call.result.admitted", source_event_id=source.id,
                          operation_id="impl-" + task, admission_status="admitted",
                          semantic_verdict="blocked")
        origin = event("issue.requested", task_id=task, rework_of=source.id,
                       target_ref="partial-" + task)
        events += [source, admission, origin]
        for _ in range(3 if task == "B" else 1):
            failure = event("issue.triage.failed", rework_of=source.id)
            events.append(failure)
        failures[task] = failure
    index = ReaderReplanIndex(events)
    assert plan_reader_stage_replan(config(), events, failures["B"], event_index=index)[1] == "cap_exhausted"
    retry, _ = plan_reader_stage_replan(config(), events, failures["A"], event_index=index)
    assert retry.payload["task_id"] == "A" and retry.payload["rework_attempt"] == 1
    assert plan_reader_stage_replan(config(), events, failures["B"], event_index=index)[1] == "cap_exhausted"
