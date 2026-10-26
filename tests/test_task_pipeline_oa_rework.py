from dataclasses import replace

import pytest

from tests.test_orchestrator_agent_semantic_failure import (
    _runtime,
    _semantic_request,
    _submit_rework,
)
from tests.test_task_pipeline_reconciler import _policy, _task
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_decision_apply import (
    apply_orchestrator_agent_decision,
)
from zf.runtime.orchestrator_agent_reactor import OrchestratorAgentReactorMixin
from zf.runtime.orchestrator_agent_semantic_failure import (
    request_semantic_failure_checkpoint,
)
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
from zf.runtime.workflow_operation import reduce_workflow_operations


def _case(tmp_path):
    runtime = _runtime(tmp_path)
    package = next(
        e.payload
        for e in runtime.event_log.read_all()
        if e.type == "plan.artifact_package.admitted"
    )
    context = {
        "workflow_run_id": "run-semantic-1",
        "task_map_generation": "g1",
        "plan_artifact_package_ref": package["package_ref"],
        "plan_artifact_package_digest": package["package_digest"],
    }
    runtime.event_writer.append(
        ZfEvent(
            type="task.pipeline.generation.admitted",
            payload={
                **context,
                "schema_version": "task-pipeline-generation.v1",
                "task_ids": ["TASK-1"],
            },
        )
    )
    identity = {
        **context,
        "task_id": "TASK-1",
        "operation_id": "impl-1",
        "operation_type": "task-stage",
        "task_pipeline_stage": "impl",
        "operation_generation": 1,
        "request_hash": "request-1",
        "dispatch_id": "lease-task-1",
        "role_instance": "dev-a",
        "active_attempt_id": "attempt-1",
    }
    for kind in (
        "workflow.operation.requested",
        "workflow.operation.started",
        "workflow.call.result.admitted",
        "workflow.operation.settled",
    ):
        runtime.event_writer.append(
            ZfEvent(
                type=kind,
                task_id="TASK-1",
                payload={
                    **identity,
                    "semantic_verdict": "failed",
                    "control_result_ref": {"ref": "failed.json", "sha256": "f" * 64},
                },
            )
        )
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    event = _submit_rework(runtime, prepared)
    outcome = apply_orchestrator_agent_decision(runtime, event)
    assert outcome["applied"]
    directive = next(
        e
        for e in runtime.event_log.read_all()
        if e.type == "orchestrator.semantic.rework.requested"
    )
    return runtime, {"TASK-1": context}, directive


def _requests(runtime, contexts):
    events = runtime.event_log.read_all()
    operations = list(reduce_workflow_operations(events).values())
    return derive_impl_rework_requests(
        events=events, generation_contexts=contexts, operation_rows=operations
    ), operations


def test_oa_rework_uses_v4_generation_and_never_legacy_dispatch(tmp_path):
    runtime, contexts, directive = _case(tmp_path)
    runtime._dispatch_rework = lambda *args: pytest.fail("v4 must not legacy-dispatch")
    result = OrchestratorAgentReactorMixin._on_orchestrator_semantic_rework_requested(
        runtime, directive
    )
    assert result.action == "gate"
    requests, operations = _requests(runtime, contexts)
    policy = _policy()
    policy["max_rework_attempts"] = 2
    projection = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=[_task("TASK-1", priority=1)],
        operations=operations,
        attempts=[],
        impl_rework_requests=requests,
    )
    assignment = projection["dispatchable"]["impl"][0]
    assert assignment["operation_generation"] == "2"
    assert assignment["impl_rework_request"]["fault"] == "task_pipeline_oa_rework"
    assert _requests(runtime, contexts)[0] == requests
    policy["max_rework_attempts"] = 0
    assert (
        TaskPipelineReconciler().reconcile(
            policy=policy,
            tasks=[_task("TASK-1", priority=1)],
            operations=operations,
            attempts=[],
            impl_rework_requests=requests,
        )["dispatchable"]["impl"]
        == []
    )


@pytest.mark.parametrize(
    "mutation", ["generation", "active_successor", "unapplied", "attempt"]
)
def test_stale_or_unapplied_oa_rework_cannot_restart_task(tmp_path, mutation):
    runtime, contexts, directive = _case(tmp_path)
    events = runtime.event_log.read_all()
    if mutation == "generation":
        contexts["TASK-1"]["task_map_generation"] = "g2"
    elif mutation == "active_successor":
        events.append(
            ZfEvent(
                type="workflow.operation.requested",
                task_id="TASK-1",
                payload={
                    **contexts["TASK-1"],
                    "operation_id": "impl-2",
                    "request_hash": "next",
                    "task_pipeline_stage": "impl",
                    "operation_generation": 2,
                },
            )
        )
    elif mutation == "unapplied":
        events = [
            e for e in events if e.type != "orchestrator.semantic.decision.applied"
        ]
    else:
        events = [
            replace(e, payload={**e.payload, "target_attempt_id": "stale"})
            if e.id == directive.id
            else e
            for e in events
        ]
    assert (
        derive_impl_rework_requests(
            events=events,
            generation_contexts=contexts,
            operation_rows=reduce_workflow_operations(events).values(),
        )
        == {}
    )
