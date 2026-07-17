from pathlib import Path

from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    normalize_call_result_envelope,
    validate_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _control_result() -> dict[str, str]:
    return {
        "schema_version": "verification-result.v1",
        "ref": "artifacts/control/result.json",
        "sha256": "a" * 64,
    }


def test_call_result_envelope_is_thin_and_deterministic(tmp_path: Path) -> None:
    payload = {
        "workflow_run_id": "run-1",
        "run_id": "attempt-1",
        "task_id": "TASK-1",
        "stage_id": "verify",
        "role_instance": "verify-1",
        "target_commit": "abc123",
        "verdict": "rejected",
        "findings": [{"message": "must stay in control result"}],
        "changed_files": ["src/a.py"],
    }
    first = normalize_call_result_envelope(
        source_payload=payload,
        control_result=_control_result(),
        workflow_run_id="run-1",
        operation_id="op-1",
        request_hash="b" * 64,
        source_event_id="evt-1",
        source_event_type="verify.child.completed",
        actor="verify-1",
        task_id="TASK-1",
    )
    second = normalize_call_result_envelope(
        source_payload=payload,
        control_result=_control_result(),
        workflow_run_id="run-1",
        operation_id="op-1",
        request_hash="b" * 64,
        source_event_id="evt-1",
        source_event_type="verify.child.completed",
        actor="verify-1",
        task_id="TASK-1",
    )
    assert canonical_json_sha256(first) == canonical_json_sha256(second)
    assert "verdict" not in first
    assert "findings" not in first
    assert "changed_files" not in first
    assert validate_call_result_envelope(first) == []

    descriptor_a = write_immutable_json_sidecar(
        tmp_path,
        first,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    descriptor_b = write_immutable_json_sidecar(
        tmp_path,
        second,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    assert descriptor_a["ref"] == descriptor_b["ref"]
    assert hydrate_sidecar_ref(tmp_path, descriptor_a).payload == first


def test_call_result_envelope_requires_immutable_verify_target() -> None:
    envelope = normalize_call_result_envelope(
        source_payload={"run_id": "attempt-1", "role_instance": "verify-1"},
        control_result=_control_result(),
        workflow_run_id="run-1",
        operation_id="op-1",
        request_hash="b" * 64,
        source_event_id="evt-1",
        source_event_type="verify.child.completed",
    )
    issues = validate_call_result_envelope(
        envelope,
        require_target_snapshot=True,
    )
    fields = {item["field"] for item in issues}
    assert "identity.target_snapshot_ref" in fields
    assert "identity.target_snapshot_digest" in fields
    assert "identity.target_commit" in fields
