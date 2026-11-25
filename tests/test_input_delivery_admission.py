"""Actual result submission must distinguish host production from delivery."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.test_result_submit import _runtime, _semantic
from zf.cli.artifact_required_read import prepare_required_read
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_output_delivery import emit_artifact_output
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.result_submit import (
    ResultSubmitError, SemanticResultSubmitService, provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _operation(tmp_path, *, observed=True, pipeline=False, submit_blocking=True, runtime=None):
    runtime = runtime or _runtime(tmp_path)
    if observed:
        runtime.config.workflow.flow_metadata["result_protocol"]["input_delivery_mode"] = "observed"
    if not submit_blocking:
        runtime.config.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"] = {}
    source = runtime.state_dir / "artifacts" / "contract.json"
    source.parent.mkdir()
    source.write_text(json.dumps({"acceptance": ["Preserve every required assertion."]}))
    token = provision_role_submit_credential(runtime.state_dir, "dev-1").read_text().strip()
    payload = {
        "workflow_run_id": "run-1", "role_instance": "dev-1", "task_id": "T1",
        "fanout_id": "fanout-1", "stage_id": "impl", "child_id": "dev-1-T1",
        "run_id": "attempt-1", "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
        "input_refs": [{"source_id": "contract", "ref": "artifacts/contract.json",
                        "allowed_paths": ["$"]}],
    }
    if pipeline:
        payload["task_pipeline_stage"] = "impl"
    prepared = prepare_call_operation(runtime, payload=payload,
        operation_type="task-stage" if pipeline else "fanout_writer_child",
        operation_key="dev-1-T1", stage_id="impl", task_id="T1", dispatch_id="attempt-1")
    mark_call_operation_started(runtime, prepared, task_id="T1", dispatch_id="attempt-1")
    if pipeline:
        from zf.core.events.model import ZfEvent

        runtime.event_writer.append(ZfEvent(type="task.pipeline.stage.dispatched", origin="kernel",
            task_id="T1", payload={**payload, "attempt_id": "attempt-1", "dispatch_id": "attempt-1"}))
    service = SemanticResultSubmitService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    return runtime, payload, prepared, service, token


def _deliver(runtime, payload, prepared, *, output="full", text_format=False):
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    body, rows = prepare_required_read(runtime.state_dir, manifest=manifest,
        rows=payload["required_reads"], identity={"actor": "dev-1", "role": "dev",
        "provider": "codex", "purpose": "implementation", "operation_id": prepared.operation_id})
    if text_format:
        from zf.runtime.artifact_text_page import render_text_page

        body = render_text_page(json.loads(body))
    emit_artifact_output(runtime.state_dir, body, rows, io.StringIO())
    sid = str(uuid4())
    transcript = runtime.state_dir / f"provider-{sid}.jsonl"
    shown = body if output == "full" else (body[:80] if output == "truncated" else '{"pages":1}')
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": sid}},
        {"type": "response_item", "timestamp": datetime.now(timezone.utc).isoformat(),
         "payload": {"type": "function_call_output", "call_id": "call-read", "output": shown}},
    ]) + "\n")
    RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", runtime.project_root).bind_codex_session(
        "dev-1", sid, session_path=transcript)
    return transcript


@pytest.mark.parametrize("output", ["summary", "truncated"])
def test_submit_rejects_host_only_output_and_accepts_complete_redelivery(tmp_path, output):
    runtime, payload, prepared, service, token = _operation(tmp_path)
    _deliver(runtime, payload, prepared, output=output)
    with pytest.raises(ResultSubmitError, match="delivery"):
        service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                       role_instance="dev-1", credential=token)
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())
    _deliver(runtime, payload, prepared)
    result = service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                            role_instance="dev-1", credential=token)
    assert result.admitted_event_id


def test_legacy_input_contract_does_not_gain_a_new_gate(tmp_path):
    runtime, payload, prepared, service, token = _operation(tmp_path, observed=False)
    _deliver(runtime, payload, prepared, output="summary")
    result = service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                            role_instance="dev-1", credential=token)
    assert result.admitted_event_id


def test_readonly_preflight_reports_missing_without_consuming_binding(tmp_path):
    runtime, payload, prepared, service, token = _operation(tmp_path)
    _deliver(runtime, payload, prepared, output="summary")
    snapshot = lambda: {str(p): p.read_bytes() for p in runtime.state_dir.rglob("*") if p.is_file()}
    before = snapshot()
    validation = service.validate_plan_candidate(operation_id=prepared.operation_id,
        semantic_result=_semantic(), role_instance="dev-1", credential=token)
    assert validation["status"] == "failed"
    assert validation["errors"][0]["code"] == "required_input_delivery_missing"
    assert snapshot() == before
    with pytest.raises(ResultSubmitError) as caught:
        service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                       role_instance="dev-1", credential=token)
    assert caught.value.details["semantic_rework_cost"] == 0
    assert snapshot() == before
    _deliver(runtime, payload, prepared)
    assert service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                          role_instance="dev-1", credential=token).admitted_event_id


def test_canonical_admission_rejects_missing_delivery_when_cli_preflight_is_bypassed(tmp_path, monkeypatch):
    runtime, payload, prepared, service, token = _operation(tmp_path)
    _deliver(runtime, payload, prepared, output="summary")
    monkeypatch.setattr("zf.runtime.input_delivery_preflight.input_delivery_preflight", lambda *args: [])
    with pytest.raises(ResultSubmitError):
        service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                       role_instance="dev-1", credential=token)
    invalid = [e for e in runtime.event_log.read_all() if e.type == "workflow.call.result.repair.requested"]
    assert invalid
    assert "required_input_delivery_missing" in json.dumps(invalid[-1].payload)
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("observed", [True, False])
def test_retry_retains_first_frozen_requirement_after_config_change(tmp_path, observed):
    runtime, payload, prepared, _, _ = _operation(tmp_path, observed=observed)
    runtime.config.workflow.flow_metadata["result_protocol"]["input_delivery_mode"] = (
        "produced" if observed else "observed")
    repeated = prepare_call_operation(runtime, payload=payload, operation_type="fanout_writer_child",
        operation_key="dev-1-T1", stage_id="impl", task_id="T1", dispatch_id="attempt-1")
    assert repeated.operation_id == prepared.operation_id
    assert repeated.request_hash == prepared.request_hash
    assert payload.get("input_delivery_mode", "produced") == ("observed" if observed else "produced")


def test_sealed_historical_delivery_survives_session_rotation_and_compaction(tmp_path):
    from zf.runtime.artifact_read_ledger import seal_read_ledger

    runtime, payload, prepared, service, token = _operation(tmp_path)
    transcript = _deliver(runtime, payload, prepared)
    transcript.write_text(transcript.read_text() + json.dumps({"type": "compacted", "payload": {}}) + "\n")
    seal_read_ledger(runtime.state_dir, attempt_id="attempt-1")
    RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", runtime.project_root).clear("dev-1")
    transcript.unlink()
    assert service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                          role_instance="dev-1", credential=token).admitted_event_id


def test_complete_text_frame_is_admitted(tmp_path):
    runtime, payload, prepared, service, token = _operation(tmp_path)
    _deliver(runtime, payload, prepared, text_format=True)
    assert service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                          role_instance="dev-1", credential=token).admitted_event_id


def test_task_stage_uses_kernel_delivery_policy_and_rejects_worker_override(tmp_path, monkeypatch):
    runtime, payload, prepared, service, token = _operation(tmp_path, pipeline=True)
    _deliver(runtime, payload, prepared, output="summary")
    monkeypatch.setattr("zf.runtime.input_delivery_preflight.input_delivery_preflight", lambda *args: [])
    monkeypatch.setattr(service, "_input_policy", lambda *args, **kwargs: {})
    with pytest.raises(ResultSubmitError):
        service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                       role_instance="dev-1", credential=token)
    repair = [e for e in runtime.event_log.read_all() if e.type == "workflow.call.result.repair.requested"]
    assert "required_input_delivery_missing" in json.dumps(repair[-1].payload)


@pytest.mark.parametrize("observed", [False, True])
def test_truthful_reader_failure_remains_reportable_without_reads(tmp_path, observed):
    runtime, _, prepared, service, token = _operation(tmp_path, observed=observed)
    kwargs = dict(operation_id=prepared.operation_id, role_instance="dev-1", credential=token,
        semantic_result={"verdict": "blocked", "execution_status": "failed",
            "target_commit": "abc123",
            "failure_class": "reader_execution_failure", "summary": "Input delivery unavailable",
            "evidence_refs": ["receipt:reader-error"]})
    assert service.validate_plan_candidate(**kwargs)["status"] == "passed"
    result = service.submit(**kwargs)
    assert result.admitted_event_id
    admitted = next(e for e in runtime.event_log.read_all() if e.id == result.admitted_event_id)
    assert admitted.payload["semantic_verdict"] == "blocked"


def test_success_cannot_bypass_delivery_with_failed_execution_flag(tmp_path):
    runtime, _, prepared, service, token = _operation(tmp_path)
    with pytest.raises(ResultSubmitError):
        service.submit(operation_id=prepared.operation_id,
            semantic_result={**_semantic(), "execution_status": "failed"},
            role_instance="dev-1", credential=token)
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())


def test_pinned_policy_corruption_fails_closed(tmp_path):
    runtime, payload, prepared, service, token = _operation(tmp_path)
    _deliver(runtime, payload, prepared)
    (runtime.state_dir / payload["input_consumption_policy_ref"]["ref"]).write_text('{}')
    with pytest.raises(ResultSubmitError):
        service.submit(operation_id=prepared.operation_id, semantic_result=_semantic(),
                       role_instance="dev-1", credential=token)
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())


def test_runtime_terminal_cannot_supply_weaker_policy_than_frozen_request(tmp_path):
    from zf.core.events.model import ZfEvent
    from zf.runtime.call_result_runtime import admit_runtime_call_result

    runtime, payload, prepared, _, _ = _operation(tmp_path, submit_blocking=False)
    _deliver(runtime, payload, prepared, output="summary")
    event = ZfEvent(type="dev.build.done", actor="dev-1", task_id="T1",
        payload={**payload, "implementation_result": _semantic(),
                 "input_consumption_policy": {"attempt_id": "attempt-1", "required_reads": []}})
    result = admit_runtime_call_result(runtime, event, dispatch_correction=False)
    assert not result.admitted
    assert "required_input_delivery_missing" in {i["code"] for i in result.issues}


@pytest.mark.parametrize("pipeline", [False, True], ids=["request", "current-kernel-dispatch"])
def test_produced_reads_preflight_and_final_admission_agree(tmp_path, pipeline):
    from zf.cli.result import _validation_summary
    from zf.runtime.artifact_read_ledger import read_attempt_artifact

    runtime, payload, prepared, service, token = _operation(tmp_path, observed=False, pipeline=pipeline)
    kwargs = dict(operation_id=prepared.operation_id, semantic_result=_semantic(),
                  role_instance="dev-1", credential=token)
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    source = manifest["sources"][0]
    # Even a complete read of the same SHA under another attempt cannot fulfill this one.
    read_attempt_artifact(runtime.state_dir, manifest={**manifest, "attempt_id": "previous-attempt"},
                          source_id=source["source_id"], artifact_id=source["artifact_id"])
    events = runtime.event_log.read_all()
    proof_paths = list((runtime.state_dir / "artifacts/attempts/attempt-1").glob("read-ledger*"))
    summary = _validation_summary(service.validate_plan_candidate(**kwargs))
    assert summary["status"] == "failed" and summary["error_count"] == 1
    assert summary["errors"][0]["code"] == "required_read_missing"
    assert "contract/contract.json $" in summary["errors"][0]["message"]
    assert "artifact read --attempt attempt-1 --required" in summary["errors"][0]["message"]
    assert runtime.event_log.read_all() == events
    assert list((runtime.state_dir / "artifacts/attempts/attempt-1").glob("read-ledger*")) == proof_paths
    with pytest.raises(ResultSubmitError, match="required_read_missing"):
        service.submit(**kwargs)

    _deliver(runtime, payload, prepared)
    validated = service.validate_plan_candidate(**kwargs)
    if pipeline:
        # This fixture lacks a task verification contract. Satisfying the reads must
        # leave that independent gate intact.
        assert {issue["code"] for issue in validated["errors"]} == {"schema_invalid"}
        with pytest.raises(ResultSubmitError, match="schema_invalid") as error:
            service.submit(**kwargs)
        assert "required_read_missing" not in {issue["code"] for issue in error.value.details["issues"]}
    else:
        assert validated["status"] == "passed"
        assert service.submit(**kwargs).admitted_event_id
