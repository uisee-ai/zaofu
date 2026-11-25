import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_capability import (
    ArtifactReadCapabilityError,
    authorize_artifact_read_from_environment,
    bind_attempt_artifact_read_capability,
    provision_role_artifact_read_credential,
)


def _manifest(
    *,
    attempt_id: str = "attempt-1",
    occurrence_id: str = "occurrence-1",
) -> dict:
    return {
        "schema_version": "attempt-source-manifest.v1",
        "workflow_run_id": "run-1",
        "task_id": "T1",
        "attempt_id": attempt_id,
        "dispatch_id": attempt_id,
        "read_purpose": "implementation",
        "sources": [{
            "source_id": "contract",
            "artifact_id": "contract",
            "ref": "artifacts/contracts/T1.json",
            "sha256": "a" * 64,
            "occurrence_id": occurrence_id,
            "retention": "run",
            "access_scope": {
                "roles": ["dev"],
                "purposes": ["implementation"],
            },
        }],
    }


def _grant(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    manifest: dict | None = None,
    operation_id: str = "",
    ttl_seconds: int = 3600,
) -> tuple[dict, EventLog, Path]:
    source_manifest = manifest or _manifest()
    token_path = provision_role_artifact_read_credential(
        state_dir,
        "dev-1",
        role_name="dev",
        provider="codex",
    )
    bind_attempt_artifact_read_capability(
        state_dir,
        operation_id=operation_id,
        attempt_id=source_manifest["attempt_id"],
        role_instance="dev-1",
        manifest=source_manifest,
        ttl_seconds=ttl_seconds,
    )
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")
    monkeypatch.setenv("ZF_ROLE_NAME", "dev")
    monkeypatch.setenv("ZF_ROLE_BACKEND", "codex")
    monkeypatch.setenv("ZF_ARTIFACT_PURPOSE", "implementation")
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(token_path))
    return source_manifest, EventLog(state_dir / "events.jsonl"), token_path


def _authorize(
    state_dir: Path,
    manifest: dict,
    event_log: EventLog,
) -> dict[str, str]:
    return authorize_artifact_read_from_environment(
        state_dir,
        manifest=manifest,
        event_log=event_log,
    )


def test_capability_round_trip_returns_issued_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, event_log, _ = _grant(tmp_path / ".zf", monkeypatch)

    identity = _authorize(tmp_path / ".zf", manifest, event_log)

    assert identity == {
        "actor": "dev-1",
        "role": "dev",
        "provider": "codex",
        "purpose": "implementation",
        "operation_id": "",
        "attempt_id": "attempt-1",
    }
    assert event_log.read_all() == []


@pytest.mark.parametrize(
    ("environment", "value", "expected_code"),
    [
        ("ZF_ARTIFACT_READ_TOKEN", "forged", "capability_invalid"),
        ("ZF_ROLE_NAME", "verify", "identity_mismatch"),
        ("ZF_ROLE_BACKEND", "claude-code", "identity_mismatch"),
        ("ZF_ARTIFACT_PURPOSE", "verification", "identity_mismatch"),
    ],
)
def test_forged_identity_is_denied_and_audited_without_secret_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    value: str,
    expected_code: str,
) -> None:
    state_dir = tmp_path / ".zf"
    manifest, event_log, token_path = _grant(state_dir, monkeypatch)
    token = token_path.read_text(encoding="utf-8").strip()
    monkeypatch.setenv(environment, value)

    with pytest.raises(ArtifactReadCapabilityError) as exc:
        _authorize(state_dir, manifest, event_log)

    assert exc.value.code == expected_code
    denied = event_log.read_all()
    assert len(denied) == 1
    assert denied[0].type == "artifact.read.denied"
    assert denied[0].payload == {
        "workflow_run_id": "run-1",
        "attempt_id": "attempt-1",
        "denial_code": expected_code,
    }
    serialized = denied[0].to_json()
    assert token not in serialized
    assert "artifacts/contracts/T1.json" not in serialized
    assert "a" * 64 not in serialized


def test_manifest_occurrence_and_attempt_are_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".zf"
    manifest, event_log, _ = _grant(state_dir, monkeypatch)
    changed_occurrence = _manifest(occurrence_id="occurrence-2")

    with pytest.raises(ArtifactReadCapabilityError) as occurrence:
        _authorize(state_dir, changed_occurrence, event_log)
    assert occurrence.value.code == "capability_stale"

    next_attempt = _manifest(attempt_id="attempt-2")
    with pytest.raises(ArtifactReadCapabilityError) as attempt:
        _authorize(state_dir, next_attempt, event_log)
    assert attempt.value.code == "capability_unbound"
    assert [
        event.payload["denial_code"] for event in event_log.read_all()
    ] == ["capability_stale", "capability_unbound"]
    assert _authorize(state_dir, manifest, event_log)["attempt_id"] == "attempt-1"


def test_expired_binding_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".zf"
    manifest, event_log, _ = _grant(state_dir, monkeypatch)
    binding_path = (
        state_dir / "private/artifact-read/attempts/attempt-1.json"
    )
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    binding_path.write_text(
        json.dumps(binding, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ArtifactReadCapabilityError) as exc:
        _authorize(state_dir, manifest, event_log)

    assert exc.value.code == "capability_expired"
    assert event_log.read_all()[-1].payload["denial_code"] == "capability_expired"


def test_role_token_rotation_rejects_old_token_and_rebinds_active_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".zf"
    manifest, event_log, token_path = _grant(state_dir, monkeypatch)
    old_token = token_path.read_text(encoding="utf-8").strip()
    rotated_path = provision_role_artifact_read_credential(
        state_dir,
        "dev-1",
        role_name="dev",
        provider="codex",
        rotate=True,
    )
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN", old_token)

    with pytest.raises(ArtifactReadCapabilityError) as stale:
        _authorize(state_dir, manifest, event_log)
    assert stale.value.code == "capability_invalid"

    monkeypatch.delenv("ZF_ARTIFACT_READ_TOKEN")
    with pytest.raises(ArtifactReadCapabilityError) as stale_file:
        _authorize(state_dir, manifest, event_log)
    assert stale_file.value.code in {"capability_invalid", "capability_unreadable"}
    assert rotated_path != token_path
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(rotated_path))
    assert _authorize(state_dir, manifest, event_log)["actor"] == "dev-1"


def test_terminal_operation_revokes_attempt_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".zf"
    manifest, event_log, _ = _grant(
        state_dir,
        monkeypatch,
        operation_id="operation-1",
    )
    event_log.append(ZfEvent(
        type="workflow.operation.requested",
        actor="kernel",
        task_id="T1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": "operation-1",
            "active_attempt_id": "attempt-1",
        },
    ))
    event_log.append(ZfEvent(
        type="workflow.operation.started",
        actor="kernel",
        task_id="T1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": "operation-1",
            "active_attempt_id": "attempt-1",
        },
    ))
    assert _authorize(state_dir, manifest, event_log)["operation_id"] == (
        "operation-1"
    )
    event_log.append(ZfEvent(
        type="workflow.operation.settled",
        actor="kernel",
        task_id="T1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": "operation-1",
        },
    ))

    with pytest.raises(ArtifactReadCapabilityError) as exc:
        _authorize(state_dir, manifest, event_log)

    assert exc.value.code == "operation_terminal"
    assert event_log.read_all()[-1].type == "artifact.read.denied"
