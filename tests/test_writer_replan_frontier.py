"""A completed Owner Plan recovery retires earlier queued writer triggers."""
from dataclasses import asdict, replace

import pytest

from tests.test_writer_fanout_runtime import _state, _seed_tasks
from zf.core.config.schema import WorkflowStageConfig, FanoutAggregateConfig
from zf.core.events import ZfEvent
from zf.runtime.control_actions import ControlledActionService


def fixture(tmp_path, *, writer_actor="zf-stall-redispatch", escalation_actor=""):
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
    escalation = writer.append(ZfEvent(type="human.escalate", task_id=pdd, correlation_id=run, actor=escalation_actor,
        causation_id=failure.id, payload={"failure_class": "stage_replan_cap_exhausted",
            "source_event_id": failure.id, "allowed_actions": ["start_new_generation"]}))
    old = writer.append(ZfEvent(type="task_map.ready", actor=writer_actor, correlation_id=run,
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


def test_later_stall_copy_cannot_revive_a_retired_writer_intent(tmp_path):
    from zf.runtime.stall_detector import StallFinding, stall_redispatch_event, emit_stall_recoveries

    runtime, old, recovery, transport = fixture(tmp_path, writer_actor="zf-cli")
    events = runtime.event_log.read_all()
    finding = StallFinding(trigger="task_map.ready", stage_id="prd-lanes-impl",
        success_event="candidate.ready", feature_id="F-11111111", fingerprint="stall:old-writer",
        trigger_event_id=old.id)
    retry = stall_redispatch_event(finding, events)
    assert retry is not None
    runtime.event_writer.append(retry)
    before = len(runtime.event_log.read_all())
    runtime._maybe_start_writer_fanout(retry)
    emitted = runtime.event_log.read_all()[before:]
    assert any(e.type == "fanout.retrigger.suppressed"
               and e.payload.get("superseded_by") == recovery["stage_trigger_event_id"] for e in emitted)
    assert not any(e.type == "prd.plan.failed" for e in emitted)
    assert not transport.sent
    stages = [(stage.id, stage.trigger, stage.aggregate.success_event)
              for stage in runtime.config.workflow.stages if stage.topology == "fanout_writer_scoped"]
    assert emit_stall_recoveries(runtime.event_log.read_all(), runtime.event_writer,
        stages=stages, min_events_after=0) == 0
    runtime._maybe_start_writer_fanout(retry)
    assert len([e for e in runtime.event_log.read_all()[before:]
                if e.type == "fanout.retrigger.suppressed"]) == 2


@pytest.mark.parametrize("changed", ["payload", "run", "task"])
def test_late_changed_work_is_not_mistaken_for_a_stall_copy(tmp_path, changed):
    from zf.runtime.stall_detector import StallFinding, stall_redispatch_event
    from zf.runtime.writer_replan_frontier import retired_writer_trigger

    runtime, old, _, _ = fixture(tmp_path, writer_actor="zf-cli")
    finding = StallFinding(trigger="task_map.ready", stage_id="prd-lanes-impl",
        success_event="candidate.ready", feature_id="F-11111111", fingerprint="stall:old-writer",
        trigger_event_id=old.id)
    retry = stall_redispatch_event(finding, runtime.event_log.read_all())
    assert retry is not None
    if changed == "payload":
        retry = replace(retry, payload={**retry.payload, "task_ids": ["TASK-2"]})
    elif changed == "run":
        retry = replace(retry, correlation_id="OTHER")
    else:
        retry = replace(retry, task_id="OTHER")
    retry = runtime.event_writer.append(retry)
    assert not retired_writer_trigger(runtime.event_log.read_all(), retry, runtime.config)


def test_late_automatic_plan_from_exhausted_failure_cannot_dispatch_or_publish(tmp_path):
    from zf.runtime.fanout import FanoutContext
    from zf.runtime.writer_replan_frontier import retired_plan_branch

    runtime, _, recovery, transport = fixture(tmp_path, escalation_actor="orchestrator")
    log, writer = runtime.event_log, runtime.event_writer
    owner = next(e for e in log.read_all() if e.id == recovery["stage_trigger_event_id"])
    cap = log.query(type="human.escalate")[0]
    failure = log.query(type="prd.plan.failed")[0]
    ordinary = writer.append(ZfEvent(type=owner.type, actor="zf-cli", origin="kernel",
        causation_id=failure.id, correlation_id=owner.correlation_id,
        payload={**owner.payload, "stage_replan_generation": "old", "rework_of": failure.id}))
    events = log.read_all()
    assert retired_plan_branch(events, ordinary, runtime.config) == cap
    assert not retired_plan_branch(events, owner, runtime.config)
    for _ in range(2):
        assert not runtime._maybe_start_reader_fanout(ordinary)
    assert not transport.sent
    assert [e.payload["reason"] for e in log.query(type="fanout.retrigger.suppressed")] == [
        "automatic_plan_retry_after_owner_handoff"]

    # Simulate the persisted manifest dispatched by the previous binary.
    context = FanoutContext.create(stage_id="prd-plan", topology="fanout_reader", trace_id=ordinary.correlation_id,
        trigger_event_id=ordinary.id, target_ref="", role_instances=["supervisor"])
    writer.append(ZfEvent(type="fanout.started", correlation_id=ordinary.correlation_id,
        causation_id=ordinary.id, payload={**asdict(context), "pdd_id": ordinary.payload["pdd_id"],
            "trigger_payload": ordinary.payload, "aggregate_config": {"failure_event": "prd.plan.failed"}}))
    before = len(log.read_all())
    for _ in range(2):
        runtime._evaluate_reader_fanout(context.fanout_id)
    assert [(e.payload["reason"], e.payload["superseded_by"]) for e in log.query(type="fanout.cancelled")] == [
        ("automatic_plan_retry_after_owner_handoff", cap.id)]
    assert not any(e.type in {"prd.plan.failed", "task_map.ready", "plan.approved"} for e in log.read_all()[before:])
