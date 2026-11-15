"""The real stage dispatcher pins successor material before freezing its request."""
import subprocess
import pytest

from tests.test_task_pipeline_capsule import dispatch_fixture
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_stage_dispatch_pins_and_reuses_predecessor_handoff(dispatch_fixture):
    runtime, _, _, prepared, dispatch = dispatch_fixture
    root = runtime.project_root
    for arguments in (["init", "-q"], ["config", "user.email", "test@example.com"], ["config", "user.name", "Test"]):
        subprocess.run(["git", *arguments], cwd=root, check=True)
    (root / "README.md").write_text("preserved candidate\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "test: seed"], cwd=root, check=True)
    request = write_immutable_json_sidecar(runtime.state_dir, {"request": {"task_id": "TASK-1"}},
        root="requests", kind="request", schema_version="request.v1", created_by="kernel")
    identity = {"operation_id": "predecessor", "workflow_run_id": "run-1", "task_id": "TASK-1",
                "task_pipeline_stage": "impl", "request_ref": request, "active_attempt_id": "old"}
    for kind in ("workflow.operation.requested", "workflow.operation.started", "workflow.operation.settled"):
        runtime.event_writer.append(ZfEvent(type=kind, origin="kernel", task_id="TASK-1", payload=identity))
    dispatch()
    payload = prepared[-1]["payload"]
    descriptor = payload["task_handoff_ref"]
    body = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    assert body["source"]["operation_id"] == "predecessor"
    assert body["workspace"]["head"]
    assert body["business_completed"] is False
    assert [row for row in payload["input_refs"] if row["source_id"] == "task-handoff"]
    (root / "new-candidate.txt").write_text("later change must not rewrite delivered facts")
    dispatch()
    assert prepared[-1]["payload"]["task_handoff_ref"] == descriptor
    assert len([e for e in runtime.event_log.read_all() if e.type == "task.handoff.prepared"]) == 1
    (runtime.state_dir / descriptor["ref"]).write_text("{}")
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting
    with pytest.raises(TaskPipelineWaiting):
        dispatch()
    assert prepared[-1]["payload"]["task_handoff_ref"] == descriptor


def test_failed_delivery_does_not_clear_unknown_continuation():
    from zf.runtime.task_handoff import _unconfirmed_continuations
    facts = {"attempt_id": "a", "dispatch_id": "d", "input_read_progress": 1,
             "recovery_action": "completion_nudge_requested"}
    claim = ZfEvent(type="worker.continuation.claimed", origin="kernel", task_id="T", actor="dev", payload=facts)
    failed = ZfEvent(type="worker.stuck.recovered", origin="kernel", task_id="T", actor="dev", payload={**facts, "prompt_injected": False})
    assert _unconfirmed_continuations([claim, failed], "T", "a") == [claim.id]
    delivered = ZfEvent(type="worker.stuck.recovered", origin="kernel", task_id="T", actor="dev", payload={**facts, "prompt_injected": True})
    assert _unconfirmed_continuations([claim, failed, delivered], "T", "a") == []


def test_real_operation_preparation_pins_handoff_as_required_input(tmp_path):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_runtime import prepare_call_operation
    runtime = _runtime(tmp_path)
    ref = write_immutable_json_sidecar(runtime.state_dir, {"objective": "Keep the candidate and verify it"},
        root="task-handoffs/bodies", kind="task_handoff", schema_version="task-handoff.v1", created_by="kernel")
    payload = {"workflow_run_id": "run-1", "role_instance": "dev-1", "task_id": "T1",
        "task_pipeline_stage": "impl", "stage_id": "impl", "output_profile_id": "implementation",
        "canonical_success_event": "dev.build.done", "canonical_failure_event": "dev.blocked",
        "input_refs": [{**ref, "source_id": "task-handoff", "artifact_id": "task-handoff", "allowed_paths": ["$"]}]}
    prepare_call_operation(runtime, payload=payload, operation_type="task-stage", operation_key="T1:impl",
        stage_id="impl", task_id="T1", dispatch_id="attempt-1")
    event = next(e for e in runtime.event_log.read_all() if e.type == "workflow.operation.requested")
    request = hydrate_sidecar_ref(runtime.state_dir, event.payload["request_ref"]).payload["request"]
    assert any(row["source_id"] == "task-handoff" and row["artifact_sha256"] == ref["sha256"]
        for row in request["required_reads"])


@pytest.mark.parametrize("corrupt", [False, True])
def test_successor_dispatch_exposes_pinned_contract_history(dispatch_fixture, corrupt):
    from zf.core.task.schema import TaskContract
    from zf.runtime.task_contract_authority import TaskContractAuthorityService
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting

    runtime, task, snapshot, prepared, dispatch = dispatch_fixture
    root = runtime.project_root
    for args in (["init", "-q"], ["config", "user.email", "test@example.com"],
                 ["config", "user.name", "Test"], ["commit", "--allow-empty", "-qm", "test: seed"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    candidate = root / "candidate.txt"
    candidate.write_text("unfinished work stays untouched")
    service = TaskContractAuthorityService(task_store=runtime.task_store,
        event_writer=runtime.event_writer, state_dir=runtime.state_dir)
    change = service.replace(task, contract=TaskContract(behavior="new scope"), source="approved_revision")
    task.contract = change.task.contract
    task.contract_authority_revision = change.authority_revision
    from zf.runtime.task_doc import compute_task_capsule_revisions
    snapshot.update(compute_task_capsule_revisions(change.task))
    snapshot["contract_authority_revision"] = change.authority_revision
    request = write_immutable_json_sidecar(runtime.state_dir, {"request": {"task_id": task.id}},
        root="requests", kind="request", schema_version="request.v1", created_by="kernel")
    identity = {"operation_id": "predecessor", "workflow_run_id": "run-1", "task_id": task.id,
                "task_pipeline_stage": "impl", "request_ref": request, "active_attempt_id": "old"}
    for kind in ("workflow.operation.requested", "workflow.operation.started", "workflow.operation.settled"):
        runtime.event_writer.append(ZfEvent(type=kind, origin="kernel", task_id=task.id, payload=identity))
    # A later worker assertion must not replace the authority service's receipt.
    runtime.event_writer.append(ZfEvent(type="task.contract.revision.applied", origin="worker", task_id=task.id,
        payload={"contract_authority_revision": change.authority_revision, "mutation_ref": {"ref": "not-real"}}))
    if corrupt:
        (runtime.state_dir / change.receipt_ref).write_text("{}")
        with pytest.raises(TaskPipelineWaiting):
            dispatch()
        assert not prepared
    else:
        dispatch()
        body = hydrate_sidecar_ref(runtime.state_dir, prepared[-1]["payload"]["task_handoff_ref"]).payload
        history = body["contract_change"]
        assert history["previous_snapshot_available"] and not history["authorizes_execution"]
        old_new = hydrate_sidecar_ref(runtime.state_dir, history["ref"]).payload
        assert old_new["previous_contract"]["behavior"] == "current"
        assert old_new["contract"]["behavior"] == "new scope"
        assert history["ref"] in body["references"]
        assert body["workspace"]["dirty"]
    assert candidate.read_text() == "unfinished work stays untouched"


def test_handoff_history_cannot_mix_current_store_with_stale_snapshot(dispatch_fixture):
    from zf.runtime.task_handoff import _contract_change

    runtime, task, _, _, _ = dispatch_fixture
    with pytest.raises(ValueError, match="authority changed"):
        _contract_change(runtime, task.id, [], expected_revision="stale-revision")
