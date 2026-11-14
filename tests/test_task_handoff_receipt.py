"""Receipt must bind the actual target, complete body and durable read proof."""
import io
import json

import pytest

from tests.test_work_record import setup
from tests.test_work_record_dispatch import dispatch
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import source_manifest_from_payload, build_canonical_required_reads
from zf.runtime.artifact_output_delivery import emit_artifact_output
from zf.cli.artifact_required_read import prepare_required_read
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_handoff_receipt import accept_task_handoff, handoff_receipt_projection


@pytest.fixture
def handoff(tmp_path, monkeypatch, request):
    state = tmp_path / "state"
    saved = {}
    def inputs(state):
        body = {"schema_version": "task-handoff.v1", "target": {"workflow_run_id": "r", "task_id": "T7", "operation_id": "op"},
            "source": {"operation_id": "old"}, "objective": "finish the remaining work", "contract_revision": "c1",
            "remaining_acceptance": [], "workspace": {"path": str(tmp_path / "work"), "environment_qualification": "unverified"},
            "admitted_results": [], "work_record": None, "unknown_effects": [], "references": [],
            "next_action": "continue checks", "authorizes_execution": False, "business_completed": False}
        if getattr(request, "param", "") == "large":
            from zf.runtime.sidecar_refs import write_sidecar_text
            body["objective"] *= 500
            body["references"] = [write_sidecar_text(state, "artifacts/large-evidence.txt", "e" * 5_000_000,
                kind="evidence", schema_version="evidence.v1", created_by="kernel")]
        ref = write_immutable_json_sidecar(state, body, root="task-handoffs/bodies", kind="task_handoff", schema_version="task-handoff.v1", created_by="kernel")
        manifest, descriptor = source_manifest_from_payload(state_dir=state, project_root=tmp_path,
            payload={"input_refs": [{**ref, "source_id": "task-handoff", "artifact_id": "task-handoff", "allowed_paths": ["$"]}]},
            workflow_run_id="r", task_id="T7", attempt_id="a", dispatch_id="a", manifest_metadata={"read_purpose": "implementation"})
        required = [{"source_id": "task-handoff", "artifact_id": "task-handoff", "artifact_sha256": ref["sha256"], "json_path": "$"}]
        EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(type="task.handoff.prepared", origin="kernel", task_id="T7",
            payload={"target": body["target"], "handoff_ref": ref}))
        saved.update(ref=ref, manifest=manifest, required=required)
        return manifest, required, descriptor
    monkeypatch.setattr("tests.test_work_record.published", inputs)
    def request_with_handoff(state, body, **kwargs):
        if "request" in body:
            body["request"]["required_reads"] = saved["required"]
        return write_immutable_json_sidecar(state, body, **kwargs)
    monkeypatch.setattr("tests.test_work_record.write_immutable_json_sidecar", request_with_handoff)
    manifest, _, log, record, token = setup(state, canonical_request=True)
    dispatch(log, record)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN", token)
    return state, log, token, saved


def read_handoff(state, saved, *, first_page_only=False):
    cursor = None
    while True:
        output, receipts = prepare_required_read(state, manifest=saved["manifest"], rows=saved["required"],
            identity={"actor": "dev-1", "role": "dev", "provider": "codex", "purpose": "implementation"}, cursor=cursor, page_bytes=2048)
        emit_artifact_output(state, output, receipts, io.StringIO())
        cursor = json.loads(output)["next_cursor"]
        if not cursor or first_page_only:
            break


def test_saved_material_waits_for_authenticated_receipt_across_restart(handoff, capsys):
    from zf.cli.main import main
    state, log, token, saved = handoff
    projection = lambda: handoff_receipt_projection(state, EventLog(log.path).read_all(), "T7")
    assert projection()["status"] == "awaiting_receipt"
    read_handoff(state, saved)
    assert projection()["status"] == "awaiting_receipt"  # producer flush is not receiver acknowledgement
    assert main(["artifact", "handoff-accept", "--operation", "op", "--digest", saved["ref"]["sha256"], "--state-dir", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"
    assert projection()["status"] == "accepted"
    result = accept_task_handoff(state_dir=state, event_log=EventLog(log.path), operation_id="op", role="dev-1", credential=token, digest=saved["ref"]["sha256"])
    assert result["replayed"] and result["business_completed"] is False
    assert TaskStore(state / "kanban.json").get("T7").status == "in_progress"
    assert len([e for e in log.read_all() if e.type == "task.handoff.accepted"]) == 1


@pytest.mark.parametrize("fault", ["unread", "digest", "credential", "owner", "body"])
def test_unqualified_receipt_cannot_be_accepted(handoff, fault):
    state, log, token, saved = handoff
    if fault != "unread":
        read_handoff(state, saved)
    if fault == "owner":
        TaskStore(state / "kanban.json").update("T7", assigned_to="another-worker")
    if fault == "body":
        (state / saved["ref"]["ref"]).write_text("{}")
    with pytest.raises((ValueError, PermissionError)):
        accept_task_handoff(state_dir=state, event_log=log, operation_id="op", role="dev-1",
            credential="invalid" if fault == "credential" else token,
            digest="wrong" if fault == "digest" else saved["ref"]["sha256"])
    assert not [e for e in log.read_all() if e.type == "task.handoff.accepted"]


def test_handoff_is_a_required_source_even_without_worker_declaration(handoff):
    _, _, _, saved = handoff
    rows = build_canonical_required_reads(saved["manifest"], output_profile_id="implementation")
    assert any(row["source_id"] == "task-handoff" and row["json_path"] == "$" for row in rows)


def test_result_gate_requires_current_receipt_and_rechecks_durable_read_proof(handoff):
    from zf.runtime.task_handoff_receipt import handoff_admission_issues
    from zf.runtime.workflow_operation import load_workflow_operation
    state, log, token, saved = handoff
    operation = load_workflow_operation(log, "op")
    assert handoff_admission_issues(state, log.read_all(), operation)[0]["code"] == "handoff_receipt_required"
    read_handoff(state, saved)
    accept_task_handoff(state_dir=state, event_log=log, operation_id="op", role="dev-1", credential=token, digest=saved["ref"]["sha256"])
    assert handoff_admission_issues(state, log.read_all(), operation) == []
    receipt = next(e for e in log.read_all() if e.type == "task.handoff.accepted")
    (state / receipt.payload["read_ledger_ref"]["ref"]).write_text("corrupt")
    assert handoff_receipt_projection(state, log.read_all(), "T7")["status"] == "incomplete"
    assert handoff_admission_issues(state, log.read_all(), operation)
    with pytest.raises(ValueError):
        accept_task_handoff(state_dir=state, event_log=log, operation_id="op", role="dev-1", credential=token, digest=saved["ref"]["sha256"])


def test_recovery_exposes_pending_ack_and_new_attempt_does_not_inherit_it(handoff):
    from zf.runtime.artifact_input_recovery import build_input_recovery
    state, log, token, saved = handoff
    recovery = build_input_recovery(state_dir=state, manifest=saved["manifest"],
        identity={"actor": "dev-1", "role": "dev", "purpose": "implementation"},
        events=log.read_all(), required=saved["required"], reads=[])
    assert recovery["handoff_receipt"]["status"] == "awaiting_receipt"
    assert "handoff-accept --operation op" in recovery["commands"]["handoff_accept_after_read"]
    read_handoff(state, saved)
    accept_task_handoff(state_dir=state, event_log=log, operation_id="op", role="dev-1", credential=token, digest=saved["ref"]["sha256"])
    EventWriter(log).append(ZfEvent(type="workflow.operation.started", origin="kernel", task_id="T7",
        payload={"operation_id": "op", "workflow_run_id": "r", "active_attempt_id": "new", "dispatch_id": "new"}))
    projection = handoff_receipt_projection(state, log.read_all(), "T7")
    assert projection["status"] == "awaiting_receipt"
    assert projection["delivery_status"] == "not_dispatched"


def test_reassignment_during_read_sealing_cannot_ack_old_target(handoff, monkeypatch):
    import zf.runtime.task_handoff_receipt as module
    state, log, token, saved = handoff
    read_handoff(state, saved)
    original = module.seal_read_ledger
    def reassign(*args):
        ledger = original(*args)
        TaskStore(state / "kanban.json").update("T7", active_dispatch_id="later")
        return ledger
    monkeypatch.setattr(module, "seal_read_ledger", reassign)
    with pytest.raises(ValueError):
        accept_task_handoff(state_dir=state, event_log=log, operation_id="op", role="dev-1", credential=token, digest=saved["ref"]["sha256"])
    assert not any(e.type == "task.handoff.accepted" for e in log.read_all())


@pytest.mark.parametrize("handoff", ["large"], indirect=True)
def test_partial_body_requires_remaining_pages_and_large_reference_stays_out_of_context(handoff):
    state, log, token, saved = handoff
    read_handoff(state, saved, first_page_only=True)
    with pytest.raises(ValueError, match="completely read"):
        accept_task_handoff(state_dir=state, event_log=log, operation_id="op", role="dev-1", credential=token, digest=saved["ref"]["sha256"])
    read_handoff(state, saved)
    accept_task_handoff(state_dir=state, event_log=log, operation_id="op", role="dev-1", credential=token, digest=saved["ref"]["sha256"])
    assert handoff_receipt_projection(state, log.read_all(), "T7")["status"] == "accepted"
    (state / "artifacts/large-evidence.txt").unlink()
    assert handoff_receipt_projection(state, log.read_all(), "T7")["status"] == "incomplete"
