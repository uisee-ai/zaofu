"""Real candidate → two-reader timeout → operator batch resume → aggregation.

Reuses raw planning/worker fixtures; no candidate or fanout terminal is seeded.
Readers submit mock semantic evidence through the blocking admission service;
this proves runtime routing, not independent candidate product verification.
The timeout clock and inert worker transport do not model a physical crash.
"""

import time

from tests.conformance.test_zaofu_testkit_s3_plan_approval import planning_runtime  # noqa: F401
from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _submit_generation_stage
from tests.test_task_pipeline_generation import _git
from zf.core.config.schema import FanoutAggregateConfig, FanoutChildConfig, RoleConfig, RoleLifecycleConfig, WorkflowStageConfig
from zf.core.events import ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import hydrate_task_contract_snapshot
from zf.runtime.task_pipeline_briefing import verification_result_template
from zf.runtime.workflow_resume import build_workflow_resume_projection


def test_candidate_reader_batch_resume_preserves_freeze_and_reaches_aggregate(
    planning_runtime, monkeypatch,
):
    runtime, transport, trigger = planning_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    # Both readers share the candidate task as their authority anchor, while
    # retaining distinct child identities for fanout/retry bookkeeping.
    readers = ["review-a", "review-b"]
    config.roles.extend(RoleConfig(
        name=role, backend="mock", role_kind="reader",
        publishes=["workflow.child.completed", "workflow.child.failed"],
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ) for role in readers)
    stage = WorkflowStageConfig(
        id="candidate-review", trigger="candidate.ready", topology="fanout_reader",
        roles=[], children=[FanoutChildConfig(role_instance=role, payload={"task_id": "TASK-BLOCKING"})
                           for role in readers],
        target_ref="candidate/${pdd_id}", timeout_seconds=3600,
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all", max_retries=0,
            success_event="review.approved", failure_event="review.rejected",
        ),
    )
    config.workflow.stages.append(stage)
    config.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"]["candidate-verify"] = "blocking"
    monkeypatch.setattr(transport, "is_alive", lambda instance: instance in readers)
    runtime = Orchestrator(state, config, transport, project_root=project)

    def control(action, payload):
        requested = runtime.event_writer.append(ZfEvent(
            type="control.action.requested", actor="operator", correlation_id="run-1",
            payload={"action": action, **payload},
        ))
        return ControlledActionService(
            state, runtime.event_writer, config=config, project_root=project,
            actor="operator", source="test", surface="test",
        ).execute(action=action, requested_action=action, payload=payload, requested=requested)

    def events(kind, fanout_id=None):
        return [e for e in runtime.event_log.read_all() if e.type == kind
                and (fanout_id is None or e.payload.get("fanout_id") == fanout_id)]

    def complete(dispatch):
        body = dispatch.payload
        assert body["target_commit"] == candidate.payload["commit"]
        target = hydrate_sidecar_ref(state, {
            "ref": body["target_snapshot_ref"], "sha256": body["target_snapshot_digest"],
        }).payload
        assert target["candidate_event_id"] == candidate.id
        manifest = hydrate_sidecar_ref(state, body["attempt_source_manifest"]).payload
        for requirement in body["required_reads"]:
            read_attempt_artifact(
                state, manifest=manifest, source_id=requirement["source_id"],
                artifact_id=requirement["artifact_id"], json_path=requirement["json_path"],
            )
        contract = hydrate_task_contract_snapshot(state, {
            "ref": body["contract_snapshot_ref"], "sha256": body["contract_snapshot_digest"],
        })
        semantic = verification_result_template(contract)
        semantic.update(summary="bounded mock candidate reader", evidence_refs=[f"event:{dispatch.id}"])
        for probe in semantic["probe_receipts"]:
            probe["target_commit"] = body["target_commit"]
            probe["evidence_refs"] = [f"event:{dispatch.id}"]
        for requirement in semantic["requirement_results"]:
            requirement["evidence_refs"] = [f"event:{dispatch.id}"]
        submitted = SemanticResultSubmitService(
            state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer,
        ).submit(
            operation_id=body["operation_id"], semantic_result=semantic,
            role_instance=body["role_instance"],
            credential=tokens[body["role_instance"]].read_text().strip(),
        )
        assert submitted.admitted_event_id
        runtime.run_once()
        [report] = [e for e in events("fanout.child.completed", body["fanout_id"])
                    if e.payload["child_id"] == body["child_id"]]
        return report

    runtime.run_once()
    assert not transport.contexts
    assert control("plan-approve", {"plan_id": trigger.id})["ok"] is True
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1", *readers)}
    runtime.run_once()
    _submit_generation_stage(runtime, transport.contexts[-1], tokens["impl-1"], repair=True)
    runtime.run_once()
    _submit_generation_stage(runtime, transport.contexts[-1], tokens["verify-1"])
    runtime.run_once()
    [candidate] = events("candidate.ready")
    frozen = hydrate_sidecar_ref(state, candidate.payload["freeze_receipt_ref"]).payload
    frozen_bytes = _git(project, "show", f"{candidate.payload['commit']}:README.md")
    assert frozen["status"] == "frozen"
    runtime.run_once()
    [started] = events("fanout.started")
    first_id = started.payload["fanout_id"]
    first_dispatches = events("fanout.child.dispatched", first_id)
    assert {d.payload["child_id"] for d in first_dispatches} == set(readers)
    complete(first_dispatches[0])
    assert events("fanout.aggregate.completed", first_id) == []
    # Exercise the real timeout policy, rather than constructing failed batch facts.
    for role in readers:
        runtime._set_worker_state(role, "idle", force=True)
    with monkeypatch.context() as clock:
        future = time.time() + 7200
        clock.setattr(runtime, "_now", lambda: future)
        runtime.run_once(events=[])
    [failed] = events("fanout.aggregate.completed", first_id)
    assert failed.payload["status"] == "failed"
    assert [e.payload["child_id"] for e in events("fanout.child.completed", first_id)] == [
        first_dispatches[0].payload["child_id"],
    ]
    assert [e.payload["child_id"] for e in events("fanout.child.failed", first_id)] == [
        first_dispatches[1].payload["child_id"],
    ]
    [checkpoint] = build_workflow_resume_projection(state, config)["batch_checkpoints"]
    assert checkpoint["safe_resume_action"] == "reemit_candidate_ready"
    assert checkpoint["candidate_snapshot_event_id"] == candidate.id
    assert checkpoint["freeze_id"] == frozen["freeze_id"]
    assert checkpoint["candidate_head_commit"] == candidate.payload["commit"]

    runtime = Orchestrator(state, config, transport, project_root=project)
    action_payload = {key: checkpoint[key] for key in ("checkpoint_id", "safe_resume_action")}
    result = control("workflow-batch-resume", action_payload)
    assert result["status"] == "applied", result
    [resumed] = [e for e in events("candidate.ready") if e.id != candidate.id]
    assert resumed.payload["candidate_snapshot_event_id"] == candidate.id
    assert resumed.payload["freeze_id"] == frozen["freeze_id"]
    assert resumed.payload["candidate_head_commit"] == candidate.payload["commit"]
    assert resumed.payload["source"] == "workflow_resume_batch"
    assert control("workflow-batch-resume", action_payload)["status"] == "no_op"

    runtime = Orchestrator(state, config, transport, project_root=project)
    # The first batch's timed-out worker remains busy in persisted liveness
    # state until the operator/runtime observes it idle; model that explicit
    # recovery input before dispatching the reopened batch.
    for role in readers:
        runtime._set_worker_state(role, "idle", force=True)
    runtime.run_once()
    [second] = [e for e in events("fanout.started") if e.payload["fanout_id"] != first_id]
    second_id = second.payload["fanout_id"]
    assert second.payload["trigger_event_id"] == resumed.id
    dispatches = events("fanout.child.dispatched", second_id)
    assert {d.payload["child_id"] for d in dispatches} == set(readers)
    assert {d.payload["run_id"] for d in dispatches}.isdisjoint(
        d.payload["run_id"] for d in first_dispatches
    )
    reports = [complete(d) for d in dispatches]
    [aggregate] = events("fanout.aggregate.completed", second_id)
    assert aggregate.payload["status"] == "completed"
    assert len(events("review.approved", second_id)) == 1
    assert build_workflow_resume_projection(state, config)["summary"]["batch_pending"] == 0
    for report in reports:
        runtime = Orchestrator(state, config, transport, project_root=project)
        runtime.run_once(events=[report])
    assert control("workflow-batch-resume", action_payload)["status"] == "no_op"
    assert len(events("fanout.started")) == 2
    assert len(events("fanout.child.dispatched")) == 4
    assert len(events("review.approved", second_id)) == 1
    assert len(events("candidate.ready")) == 2  # Original freeze plus authorized resume wrapper.
    assert len(events("integration.queue.integrated")) == 1
    assert not events("workflow.call.result.repair.requested")
    assert not events("kernel.housekeeping.failed")
    assert runtime.task_store.get("TASK-BLOCKING").status == "done"
    assert _git(project, "show", f"{candidate.payload['commit']}:README.md") == frozen_bytes
