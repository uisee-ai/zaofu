"""A completed Owner Plan recovery retires earlier queued writer triggers."""
from dataclasses import asdict, replace

import pytest

from tests.test_writer_fanout_runtime import _state, _seed_tasks
from zf.core.config.schema import WorkflowStageConfig, FanoutAggregateConfig
from zf.core.events import ZfEvent
from zf.runtime.control_actions import ControlledActionService


def fixture(tmp_path):
    state, log, transport, runtime = _state(tmp_path)
    _seed_tasks(state)
    runtime.config.workflow.stages.append(WorkflowStageConfig(id="prd-plan", trigger="prd.scan.completed",
        topology="fanout_reader", attempt_domain="plan", roles=["supervisor"],
        aggregate=FanoutAggregateConfig(failure_event="prd.plan.failed")))
    writer = runtime.event_writer
    writer.default_origin = "kernel"
    run, pdd = "RUN-1", "F-11111111"
    writer.append(ZfEvent(type="run.goal.started", task_id=pdd, correlation_id=run,
        payload={"run_id": run, "workflow_run_id": run, "status": "active"}))
    writer.append(ZfEvent(type="prd.scan.completed", task_id=pdd, correlation_id=run,
        payload={"workflow_run_id": run, "pdd_id": pdd, "task_ids": ["TASK-1"], "resume_scope": "failed_children_only"}))
    failure = writer.append(ZfEvent(type="prd.plan.failed", task_id=pdd, correlation_id=run,
        payload={"workflow_run_id": run, "pdd_id": pdd, "stage_id": "prd-plan"}))
    escalation = writer.append(ZfEvent(type="human.escalate", task_id=pdd, correlation_id=run,
        causation_id=failure.id, payload={"failure_class": "stage_replan_cap_exhausted",
            "source_event_id": failure.id, "allowed_actions": ["start_new_generation"]}))
    old = writer.append(ZfEvent(type="task_map.ready", actor="zf-stall-redispatch", correlation_id=run,
        payload={"workflow_run_id": run, "pdd_id": pdd, "source": "workflow_resume_batch",
            "resume_scope": "all_tasks_rework", "rework_of": failure.id,
            "plan_artifact_package_ref": "missing-old-package.json", "plan_artifact_package_digest": "a" * 64}))
    service = ControlledActionService(state, writer, config=runtime.config, project_root=tmp_path)
    request = writer.append(ZfEvent(type="controlled.action.requested", actor="operator", correlation_id=run,
        payload={"action": "stage-replan-new-generation", "approval_ref": "owner:test"}))
    result = service.execute(action="stage-replan-new-generation", requested_action="stage-replan-new-generation",
        payload={"escalation_event_id": escalation.id, "approval_ref": "owner:test"}, requested=request)
    assert result["ok"], result
    return runtime, old, result, transport


def test_writer_entry_retires_old_trigger_before_package_admission(tmp_path):
    runtime, old, recovery, transport = fixture(tmp_path)
    before = len(runtime.event_log.read_all())
    runtime._maybe_start_writer_fanout(old)
    runtime._maybe_start_writer_fanout(old)
    emitted = runtime.event_log.read_all()[before:]
    suppressed = [e for e in emitted if e.type == "fanout.retrigger.suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0].payload["superseded_by"] == recovery["stage_trigger_event_id"]
    assert not any(e.type in {"prd.plan.failed", "fanout.started", "fanout.cancelled"} for e in emitted)
    assert not transport.sent


@pytest.mark.parametrize("difference", ["run", "pdd", "later", "not_plan", "external_result"])
def test_retirement_does_not_consume_other_authority(tmp_path, difference):
    from zf.runtime.writer_replan_frontier import retired_writer_trigger

    runtime, old, recovery, _ = fixture(tmp_path)
    events = runtime.event_log.read_all()
    if difference in {"run", "pdd"}:
        old = replace(old, payload={**old.payload, "workflow_run_id" if difference == "run" else "pdd_id": "OTHER"})
        if difference == "run":
            old = replace(old, correlation_id="OTHER")
        events = [old if e.id == old.id else e for e in events]
    elif difference == "later":
        events = [e for e in events if e.id != old.id] + [old]
    elif difference == "not_plan":
        runtime.config.workflow.stages[-1].attempt_domain = "task"
    else:
        events = [replace(e, origin="external") if e.type == "runtime.action.completed" else e for e in events]
    assert not retired_writer_trigger(events, old, runtime.config)


def descendant(runtime, old):
    failure = runtime.event_writer.append(ZfEvent(type="prd.plan.failed", correlation_id="RUN-1",
        causation_id=old.id, payload={"workflow_run_id": "RUN-1", "pdd_id": "F-11111111",
                                     "stage_id": "prd-plan"}))
    return runtime.event_writer.append(ZfEvent(type="prd.scan.completed", correlation_id="RUN-1",
        causation_id=failure.id, payload={"workflow_run_id": "RUN-1", "pdd_id": "F-11111111",
            "resume_scope": "all_tasks_rework", "rework_of": failure.id}))


def test_old_writer_failure_cannot_start_an_unscoped_plan(tmp_path):
    runtime, old, recovery, transport = fixture(tmp_path)
    trigger = descendant(runtime, old)
    assert not runtime._maybe_start_reader_fanout(trigger)
    assert not runtime._maybe_start_reader_fanout(trigger)
    suppressed = [e for e in runtime.event_log.read_all() if e.type == "fanout.retrigger.suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0].payload["superseded_by"] == recovery["stage_trigger_event_id"]
    assert not transport.sent


@pytest.mark.parametrize("entry", ["aggregate", "synth"])
def test_already_started_stale_plan_cannot_publish_or_replan(tmp_path, entry):
    from zf.runtime.fanout import FanoutContext

    runtime, old, recovery, transport = fixture(tmp_path)
    trigger = descendant(runtime, old)
    context = FanoutContext.create(stage_id="prd-plan", topology="fanout_reader", trace_id="RUN-1",
        trigger_event_id=trigger.id, target_ref="", role_instances=["supervisor"])
    runtime.event_writer.append(ZfEvent(type="fanout.started", correlation_id="RUN-1",
        causation_id=trigger.id, payload={**asdict(context), "pdd_id": "F-11111111",
            "trigger_payload": trigger.payload, "aggregate_config": {"failure_event": "prd.plan.failed"}}))
    before = len(runtime.event_log.read_all())
    for _ in range(2):
        if entry == "aggregate":
            runtime._evaluate_reader_fanout(context.fanout_id)
        else:
            runtime._handle_fanout_synth_completed(ZfEvent(type="fanout.synth.completed",
                payload={"fanout_id": context.fanout_id, "output_profile_id": "plan-synth"}))
    emitted = runtime.event_log.read_all()[before:]
    cancelled = [e for e in emitted if e.type == "fanout.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["superseded_by"] == recovery["stage_trigger_event_id"]
    assert not any(e.type in {"prd.plan.failed", "task_map.ready", "plan.approved"} for e in emitted)
    assert not transport.sent


@pytest.mark.parametrize("change", ["missing_cause", "external", "other_pdd", "current_recovery"])
def test_plan_branch_does_not_guess_causality(tmp_path, change):
    from zf.runtime.writer_replan_frontier import retired_plan_branch

    runtime, old, recovery, _ = fixture(tmp_path)
    trigger = descendant(runtime, old)
    if change == "missing_cause":
        trigger = replace(trigger, causation_id=None)
    elif change == "external":
        trigger = replace(trigger, origin="external")
    elif change == "other_pdd":
        trigger = replace(trigger, payload={**trigger.payload, "pdd_id": "OTHER"})
    else:
        trigger = next(e for e in runtime.event_log.read_all() if e.id == recovery["stage_trigger_event_id"])
    assert not retired_plan_branch(runtime.event_log.read_all(), trigger, runtime.config)


def test_controlled_retry_preserves_the_authorized_scope_and_generation(tmp_path):
    from zf.runtime.writer_replan_frontier import retired_plan_branch

    runtime, _, recovery, _ = fixture(tmp_path)
    service = ControlledActionService(runtime.state_dir, runtime.event_writer,
        config=runtime.config, project_root=tmp_path)
    request = runtime.event_writer.append(ZfEvent(type="controlled.action.requested", actor="operator",
        payload={"action": "stage-retrigger"}))
    result = service.execute(action="stage-retrigger", requested_action="stage-retrigger",
        payload={"source_event_id": recovery["stage_trigger_event_id"]}, requested=request)
    assert result["ok"], result
    events = runtime.event_log.read_all()
    retry = next(event for event in events if event.id == result["retriggered_event_id"])
    assert retry.payload["task_ids"] == ["TASK-1"]
    assert retry.payload["resume_scope"] == "failed_children_only"
    assert retry.payload["stage_replan_generation"] == recovery["stage_replan_generation"]
    assert not retired_plan_branch(events, retry, runtime.config)
