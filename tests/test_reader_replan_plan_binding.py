from pathlib import Path
import json
from tests.test_reader_fanout_runtime import _RecordingTransport
from zf.core.config.schema import ZfConfig, ProjectConfig, RoleConfig, WorkflowConfig, WorkflowStageConfig, FanoutAggregateConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_contract_snapshot import write_task_contract_snapshot, build_task_contract_snapshot
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.plan_artifact_package import build_plan_artifact_package
from zf.runtime.run_contract import write_run_contract_snapshot

def _dispatched_replan(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="discovery", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(
            flow_metadata={"result_protocol": {"mode": "blocking"}},
            stages=[WorkflowStageConfig(
                id="post-verify-discovery",
                trigger="flow.discovery.requested",
                topology="fanout_reader",
                roles=["discovery"],
                target_ref="candidate/${pdd_id}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="flow.discovery.child.completed",
                    child_failure_event="flow.discovery.child.failed",
                    success_event="flow.discovery.completed",
                    failure_event="flow.discovery.failed",
                ),
            )],
        ),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    writer = EventWriter(log)
    writer.emit(
        "workflow.invoke.accepted",
        actor="zf-cli",
        task_id="TASK-PRD-ROOT",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "task_id": "TASK-PRD-ROOT",
        },
    )
    task = Task(id="GAP-CURRENT", status="in_progress", contract=TaskContract(
        acceptance_criteria=[{"acceptance_id": "GAP-CURRENT-AC1", "statement": "materialize the missing predecessor slice", "verification_owner": "task_verify", "verification_tier": "task_non_smoke"}],
        verification="true", evidence_contract={"workflow_run_id": "workflow-1", "source_refs": {
            "task_map_generation": "generation-1", "plan_artifact_package_id": "planpkg-old",
            "plan_artifact_package_ref": "old-package.json", "plan_artifact_package_digest": "old-digest",
        }},
    ))
    TaskStore(state_dir / "kanban.json").add(task)
    original_body = build_task_contract_snapshot(task, workflow_run_id="workflow-1", task_map_generation_id="generation-1", base_commit="base123", task_ref="task/GAP-CURRENT")
    contract_snapshot = write_task_contract_snapshot(state_dir, original_body)
    run_contract = write_run_contract_snapshot(state_dir, {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}})
    package_body = build_plan_artifact_package(workflow_run_id="workflow-1", flow_kind="prd", producer_stage_id="plan", run_contract=run_contract, plan_revision="generation-2", task_map_generation="generation-2", produced=[])
    package = write_immutable_json_sidecar(state_dir, root="artifacts/test-plan-package", kind="plan_artifact_package", schema_version="plan-artifact-package.v1", created_by="test", payload=package_body)
    writer.emit("plan.artifact_package.admitted", payload={
        "workflow_run_id": "workflow-1", "package_slot": "execution_plan", "status": "admitted",
        "task_map_generation": "generation-2", "plan_revision": "generation-2",
        "package_id": ("planpkg-" + package["sha256"]), "package_ref": package["ref"], "package_digest": package["sha256"],
    })
    current_candidate = writer.emit(
        "candidate.ready",
        actor="zf-cli",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "pdd_id": "PRD-1",
            "candidate_ref": "candidate/PRD-1",
            "candidate_head_commit": "candidate123",
            "candidate_base_commit": "base123",
            "completed_task_ids": ["GAP-CURRENT"],
            "task_map_generation": "generation-1",
        },
    )
    trigger = ZfEvent(
        type="flow.discovery.requested",
        actor="run-manager",
        task_id=None,
        correlation_id="workflow-1",
        payload={
            "schema_version": "semantic-replan-request.v1",
            "plan_artifact_package_id": ("planpkg-" + package["sha256"]),
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
            "workflow_run_id": "workflow-1",
            "pdd_id": "PRD-1",
            "task_id": "GAP-CURRENT",
            "task_map_ref": "artifacts/plan/task-map.json",
            "task_map_generation": "generation-2",
            "contract_revision": original_body["contract_revision"],
            "task_ref": "task/GAP-CURRENT",
            "contract_snapshot_ref": contract_snapshot["ref"],
            "contract_snapshot_digest": contract_snapshot["sha256"],
            "rework_of": "blocked-current",
            "recommended_action": "replan",
            "guidance": "Create a new task id and supersede only GAP-CURRENT.",
            "supersedes_task_ids": ["GAP-CURRENT"],
            "handoff_kind": "partial_task_continuation",
            "continuation_commit": "partial456",
            "continuation_ref": "worker/dev-1",
            "candidate_ref": "worker/dev-1",
            "candidate_head_commit": "partial456",
            "target_ref": "partial456",
            "candidate_event_id": current_candidate.id,
            "output_profile_id": "global-rescan",
            "output_profile_revision": "1",
        },
    )

    orch.run_once(events=[trigger])

    failures = [e.payload.get("reason") for e in log.read_all() if e.type == "fanout.child.failed"]
    assert not failures, failures
    dispatched = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    )
    child_payload = dict(dispatched.payload["payload"])
    assert dispatched.task_id == "GAP-CURRENT"
    assert child_payload["task_id"] == "GAP-CURRENT"
    assert child_payload["parent_task_id"] == "TASK-PRD-ROOT"
    assert child_payload["output_profile_id"] == "global-rescan"
    assert child_payload["output_profile_revision"] == "1"
    assert not any(
        event.type == "fanout.child.failed"
        and "preregistration failed" in str(event.payload.get("reason") or "")
        for event in log.read_all()
    )
    manifest = hydrate_sidecar_ref(
        state_dir,
        child_payload["attempt_source_manifest"],
    ).payload
    semantic_source = next(
        source for source in manifest["sources"]
        if source["source_id"] == "semantic-replan-request"
    )
    recovered = read_attempt_artifact(
        state_dir,
        manifest=manifest,
        source_id="semantic-replan-request",
        artifact_id="semantic-replan-request",
        json_path="$",
    )
    recovered_payload = json.loads(recovered["content"])
    assert recovered_payload["guidance"] == trigger.payload["guidance"]
    assert recovered_payload["continuation_commit"] == "partial456"
    assert recovered_payload["target_ref"] == "partial456"
    assert semantic_source["sha256"]
    assert any(
        item["source_id"] == "semantic-replan-request"
        for item in child_payload["required_reads"]
    )
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    started = next(
        event for event in log.read_all() if event.type == "fanout.started"
    )
    assert started.payload["target_ref"] == "partial456"
    assert dispatched.payload["target_ref"] == "partial456"
    assert "- target_ref: `partial456`" in briefing
    assert "--source semantic-replan-request" in briefing
    assert "--json-path '$'" in briefing


    assert child_payload["plan_artifact_package_id"] == ("planpkg-" + package["sha256"])
    assert child_payload["task_map_generation"] == "generation-2"
    assert manifest["plan_artifact_package_id"] == ("planpkg-" + package["sha256"])
    return state_dir, log, orch, child_payload, contract_snapshot, transport


def test_replan_reader_preserves_current_plan_over_failed_task_snapshot(tmp_path):
    _dispatched_replan(tmp_path)
