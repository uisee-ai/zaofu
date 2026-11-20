"""Shared Reader checkpoints use their signed scratch, not Task ownership."""
import json
from io import StringIO

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from zf.cli.main import main
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.work_record import current_work_record, publish_work_record


def reader(tmp_path):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path, semantic_submit=True)
    operation = next(iter(reduce_workflow_operations(log.read_all()).values()))
    request = hydrate_sidecar_ref(state, operation["request_ref"]).payload["request"]
    scratch = state / request["result_scratch_ref"]
    checkpoint = {
        "completed_actions": ["REQ-1: 已评估输入事实，依据 context $.facts 的固定摘要"],
        "pending": ["REQ-2: 核对异常分支"], "next_step": "验证 REQ-2 后完成报告",
        "evidence_files": [], "report_draft": "# 独立评估草稿\nREQ-1 已核对；REQ-2 未完成。",
    }
    token = provision_role_submit_credential(state, "verify-1", rotate=False).read_text().strip()
    manifest = load_attempt_source_manifest(state, operation["active_attempt_id"])
    return state, log, runtime, operation, manifest, scratch, token, checkpoint


def publish(values, **overrides):
    state, log, _, op, _, scratch, token, checkpoint = values
    return publish_work_record(**{
        "state_dir": state, "event_log": log, "operation_id": op["operation_id"],
        "role": "verify-1", "credential": token, "record_body": checkpoint, "request_id": "checkpoint-1",
        **overrides,
    })


def test_reader_checkpoint_cli_recover_rotation_without_task_owner(tmp_path, monkeypatch, capsys):
    values = reader(tmp_path)
    state, log, _, op, manifest, scratch, token, checkpoint = values
    template = scratch.read_bytes()
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "verify-1")
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN", token)
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(checkpoint)))
    assert main(["artifact", "work-record", "publish", "--operation", op["operation_id"],
                 "--stdin", "--request-id", "checkpoint-1", "--state-dir", str(state)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert publish(values)["event_id"] == first["event_id"]
    record = hydrate_sidecar_ref(state, first["work_record_ref"]).payload
    assert "REQ-2" in record["report_draft"]
    assert record["claims_status"] == "worker_report_unverified"
    assert not record["authorizes_execution"]
    assert json.loads((state / "kanban.json").read_text()) == []
    new_token = provision_role_submit_credential(state, "verify-1").read_text().strip()
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN", new_token)
    assert current_work_record(state, log.read_all(), manifest, "verify-1")["status"] == "bound"
    assert main(["artifact", "work-record", "show", "--operation", op["operation_id"],
                 "--state-dir", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == first["work_record_ref"]["sha256"]
    assert not any(e.type == "workflow.call.result.admitted" for e in log.read_all())
    assert scratch.read_bytes() == template
    from zf.runtime.artifact_read_capability import (
        bind_attempt_artifact_read_capability, provision_role_artifact_read_credential,
    )
    read_token = provision_role_artifact_read_credential(state, "verify-1", role_name="verify-1", provider="mock")
    bind_attempt_artifact_read_capability(state, operation_id=op["operation_id"],
        attempt_id=manifest["attempt_id"], role_instance="verify-1", manifest=manifest)
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(read_token))
    assert main(["artifact", "recover", "--attempt", manifest["attempt_id"], "--state-dir", str(state)]) == 0
    pack = json.loads(capsys.readouterr().out)
    assert pack["work_record"]["status"] == "bound"
    assert "--stdin" in pack["work_record_publish"]["command"]
    assert "Keep result scratch unchanged" in pack["work_record_publish"]["instructions"]
    assert "work-record show" in pack["commands"]["work_record_read"]
    assert pack["historical_missing"] == 1
    assert pack["current_memory"] == "unknown"
    assert len(json.dumps(pack).encode()) <= 8192


@pytest.mark.parametrize("fault", [
    "wrong_actor", "wrong_token", "expired", "operation_cancelled", "child_completed",
    "fanout_cancelled", "fanout_superseded", "old_dispatch", "request_tampered",
    "manifest_changed", "unknown_ledger", "foreign_owner", "old_credential",
])
def test_reader_checkpoint_rejects_stale_or_foreign_authority(tmp_path, fault):
    values = reader(tmp_path)
    state, log, runtime, op, manifest, _, token, _ = values
    overrides = {}
    dispatch = next(e for e in log.read_all() if e.type == "fanout.child.dispatched")
    if fault == "wrong_actor":
        overrides["role"] = "verify-2"
    elif fault == "wrong_token":
        overrides["credential"] = "invalid"
    elif fault == "expired":
        from zf.runtime.task_attempt_runtime import task_attempt_store
        store = task_attempt_store(runtime)
        row = store.current_rows()[0]
        store.renew_lease(row["attempt_id"], lease_expires_at="2000-01-01T00:00:00+00:00", updated_at=row["updated_at"])
    elif fault == "operation_cancelled":
        runtime.event_writer.emit("workflow.operation.cancelled", payload={"operation_id": op["operation_id"]})
    elif fault == "child_completed":
        runtime.event_writer.emit("fanout.child.completed", payload=dispatch.payload)
    elif fault == "fanout_cancelled":
        runtime.event_writer.emit("fanout.cancelled", payload=dispatch.payload)
    elif fault == "fanout_superseded":
        started = next(e for e in log.read_all() if e.type == "fanout.started")
        runtime.event_writer.emit("fanout.started", payload={**started.payload, "fanout_id": "new-fanout"})
    elif fault == "old_dispatch":
        runtime.event_writer.emit("workflow.operation.started", payload={
            "operation_id": op["operation_id"], "dispatch_id": "other-dispatch"})
    elif fault == "request_tampered":
        (state / op["request_ref"]["ref"]).write_text("{}")
    elif fault == "manifest_changed":
        (state / dispatch.payload["attempt_source_manifest_ref"]).write_text("{}")
    elif fault == "unknown_ledger":
        runtime.event_writer.emit("event.malformed", payload={"ledger_integrity": "unknown"})
    elif fault == "foreign_owner":
        runtime.event_writer.emit("workflow.operation.started", payload={
            **op, "operation_id": "another-running-operation"})
    elif fault == "old_credential":
        provision_role_submit_credential(state, "verify-1")
    with pytest.raises((ValueError, OSError)):
        publish(values, **overrides)
    assert not any(e.type == "worker.work_record.published" for e in log.read_all())
    assert current_work_record(state, log.read_all(), manifest, "verify-2")["status"] == "unavailable"


@pytest.mark.parametrize("fault", ["outside", "symlink", "secret", "oversize", "files"])
def test_reader_checkpoint_never_expands_file_or_secret_access(tmp_path, fault):
    values = reader(tmp_path)
    state, log, _, _, _, scratch, _, body = values
    overrides = {}
    if fault in {"outside", "symlink"}:
        path = tmp_path / "checkpoint.json"
        path.write_text(json.dumps(body))
        if fault == "symlink":
            scratch.unlink()
            scratch.symlink_to(path)
            path = scratch
        overrides = {"record_file": path, "record_body": None}
    elif fault == "secret":
        body["report_draft"] = "API_KEY=secret-credential-value"
    elif fault == "oversize":
        body["report_draft"] = "x" * 17000
    else:
        body["evidence_files"] = ["private/secret.json"]
    with pytest.raises((ValueError, OSError)):
        publish(values, **overrides)
    assert not any(e.type == "worker.work_record.published" for e in log.read_all())


def test_corrupt_latest_checkpoint_does_not_resurrect_previous(tmp_path):
    values = reader(tmp_path)
    state, log, _, _, manifest, _, _, body = values
    first = publish(values)
    body["pending"] = ["新的未决"]
    second = publish(values, request_id="checkpoint-2")
    (state / second["work_record_ref"]["ref"]).write_text("{}")
    assert current_work_record(state, log.read_all(), manifest, "verify-1")["status"] == "unavailable"
    with pytest.raises(ValueError):
        publish(values, request_id="checkpoint-2")
    assert hydrate_sidecar_ref(state, first["work_record_ref"]).payload["pending"] != body["pending"]


def test_checkpoint_survives_controller_restart_without_new_turn(tmp_path):
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.task_attempt_runtime import task_attempt_store
    values = reader(tmp_path)
    state, log, runtime, op, manifest, _, _, _ = values
    first = publish(values)
    before = task_attempt_store(runtime).current_rows()
    restarted = Orchestrator(state, runtime.config, runtime.transport)
    restarted.run_once(events=[])
    assert current_work_record(state, log.read_all(), manifest, "verify-1")["work_record_ref"] == first["work_record_ref"]
    assert len(runtime.transport.sent) == 1
    assert task_attempt_store(restarted).current_rows() == before
    assert reduce_workflow_operations(log.read_all())[op["operation_id"]]["status"] == "running"
    briefing = runtime.transport.sent[0][1].read_text()
    assert "Optional semantic checkpoint: `artifact recover`" in briefing


def candidate_reader(tmp_path):
    from tests.test_reader_undelivered_retry import _waiting
    from tests.test_run_manager_reader_rebuild import recommendation, consume
    runtime, context, old, failed = _waiting(tmp_path)
    state, log = runtime.state_dir, runtime.event_log
    runtime.config.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"] = {"candidate-verify": "blocking"}
    token = provision_role_submit_credential(state, "verify").read_text().strip()
    recommendation(runtime, context, old, failed)
    consume(runtime)
    intents = [e for e in log.read_all() if e.type == "repair.action.requested"]
    runtime.run_once(events=intents)
    op = next(o for o in reduce_workflow_operations(log.read_all()).values() if o["status"] == "running")
    manifest = load_attempt_source_manifest(state, op["active_attempt_id"])
    runtime.task_store.update("FLOW-ANCHOR", status="done")
    return state, log, runtime, op, manifest, token


def test_checkpoint_does_not_consume_formal_result_capability(tmp_path):
    from zf.runtime.result_submit import SemanticResultSubmitService
    state, log, runtime, op, manifest, token = candidate_reader(tmp_path)
    first = publish_work_record(state_dir=state, event_log=log, operation_id=op["operation_id"],
        role="verify", credential=token, request_id="candidate-checkpoint", record_body={
            "completed_actions": ["核对冻结候选；待继续"], "pending": ["环境验证未完成"],
            "next_step": "报告具体环境故障", "evidence_files": [],
        })
    service = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=runtime.event_writer)
    result = service.submit(
        operation_id=op["operation_id"], role_instance="verify", credential=token,
        semantic_result={
            "schema_version": "verification-result.v1", "execution_status": "failed",
            "verdict": "abstained", "failure_class": "reader_execution_failure",
            "summary": "mock obstruction after checkpoint; no product verdict",
            "evidence_refs": [first["work_record_ref"]["ref"]],
            "verification_owner": "candidate_verify", "verification_tier": "runtime",
            "requirement_results": [], "probe_receipts": [], "reused_command_receipt_ids": [],
            "rework_items": [],
        },
    )
    event = next(e for e in log.read_all() if e.id == result.canonical_event_id)
    assert result.canonical_event_type.endswith("failed")
    runtime.run_once(events=[event])
    assert current_work_record(state, log.read_all(), manifest, "verify")["status"] == "unavailable"
    assert any(e.type == "workflow.call.result.admitted" for e in log.read_all())
    assert not any(e.type == "task.done" for e in log.read_all())
    assert runtime.task_store.get("FLOW-ANCHOR").status == "done"


@pytest.mark.parametrize("fault", ["target", "contract", "generation"])
def test_candidate_checkpoint_rechecks_freeze_without_plan_package(tmp_path, fault):
    state, log, runtime, op, manifest, token = candidate_reader(tmp_path)
    assert not manifest.get("plan_artifact_package_ref")
    request = hydrate_sidecar_ref(state, op["request_ref"]).payload["request"]
    if fault == "contract":
        (state / request["result_identity"]["contract_snapshot_ref"]).write_text("{}")
    else:
        freeze = next(e for e in log.read_all() if e.id == "evt-candidate-ready")
        field = "candidate_head_commit" if fault == "target" else "task_map_generation"
        runtime.event_writer.emit("candidate.ready", payload={**freeze.payload, field: "new-value"})
    with pytest.raises(ValueError, match="frozen candidate"):
        publish_work_record(state_dir=state, event_log=log, operation_id=op["operation_id"],
            role="verify", credential=token, request_id="stale", record_body={
                "completed_actions": [], "pending": ["须重核"], "next_step": "受控恢复", "evidence_files": [],
            })
    assert not any(e.type == "worker.work_record.published" for e in log.read_all())
    assert current_work_record(state, log.read_all(), manifest, "verify")["status"] == "unavailable"


@pytest.mark.parametrize("status", ["run.paused", "run.cancelled", "run.completed"])
def test_reader_checkpoint_cannot_hide_run_lifecycle(tmp_path, status):
    values = reader(tmp_path)
    state, log, runtime, op, manifest, _, _, _ = values
    runtime.event_writer.emit("run.started", payload={"run_id": op["workflow_run_id"]})
    publish(values)
    runtime.event_writer.emit(status, payload={"run_id": op["workflow_run_id"]})
    with pytest.raises(ValueError, match="Run is not running"):
        publish(values, request_id="after-stop")
    assert current_work_record(state, log.read_all(), manifest, "verify-1")["status"] == "unavailable"
