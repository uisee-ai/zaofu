"""Kernel-issued credentials for attempt-scoped artifact reads."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text


ARTIFACT_READ_CREDENTIAL_SCHEMA = "artifact-read-role-credential.v1"
ARTIFACT_READ_BINDING_SCHEMA = "artifact-read-capability-binding.v1"
DEFAULT_CAPABILITY_TTL_SECONDS = 48 * 60 * 60
_ACTIVE_OPERATION_STATUSES = frozenset({"requested", "reserved", "running"})


class ArtifactReadCapabilityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def provision_role_artifact_read_credential(
    state_dir: Path,
    role_instance: str,
    *,
    role_name: str = "",
    provider: str = "",
    rotate: bool = True,
) -> Path:
    """Issue one role-scoped bearer without publishing its plaintext."""

    role = _safe_component(role_instance)
    root = Path(state_dir) / "private" / "artifact-read" / "roles"
    root.mkdir(parents=True, exist_ok=True)
    _chmod(root, 0o700)
    metadata_path = root / f"{role}.json"
    token_path = root / f"{role}.token"
    current = _read_json(metadata_path)
    if not rotate and token_path.is_file() and current:
        return token_path
    generation = int(current.get("generation") or 0) + 1
    token = secrets.token_urlsafe(32)
    atomic_write_text(token_path, token + "\n")
    _chmod(token_path, 0o600)
    atomic_write_text(
        metadata_path,
        json.dumps(
            {
                "schema_version": ARTIFACT_READ_CREDENTIAL_SCHEMA,
                "role_instance": str(role_instance),
                "role_name": str(role_name),
                "provider": str(provider),
                "generation": generation,
                "token_sha256": _digest(token),
                "token_ref": str(token_path),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )
    _chmod(metadata_path, 0o600)
    _reissue_attempt_bindings(Path(state_dir), str(role_instance), generation)
    return token_path


def bind_attempt_artifact_read_capability(
    state_dir: Path,
    *,
    operation_id: str,
    attempt_id: str,
    role_instance: str,
    manifest: Mapping[str, Any],
    ttl_seconds: int = DEFAULT_CAPABILITY_TTL_SECONDS,
) -> None:
    """Bind a role credential to one immutable source manifest."""

    metadata = _credential_metadata(Path(state_dir), role_instance)
    if not metadata:
        return
    manifest_attempt = str(manifest.get("attempt_id") or "")
    if not attempt_id or manifest_attempt != attempt_id:
        raise ArtifactReadCapabilityError(
            "attempt_mismatch",
            "artifact read binding does not match the source manifest attempt",
        )
    ttl = max(1, int(ttl_seconds))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    path = _binding_path(Path(state_dir), attempt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod(path.parent, 0o700)
    binding = {
        "schema_version": ARTIFACT_READ_BINDING_SCHEMA,
        "workflow_run_id": str(manifest.get("workflow_run_id") or ""),
        "task_id": str(manifest.get("task_id") or ""),
        "operation_id": str(operation_id),
        "attempt_id": str(attempt_id),
        "role_instance": str(role_instance),
        "role_name": str(metadata.get("role_name") or ""),
        "provider": str(metadata.get("provider") or ""),
        "purpose": str(manifest.get("read_purpose") or ""),
        "manifest_digest": canonical_source_manifest_digest(manifest),
        "credential_generation": int(metadata.get("generation") or 0),
        "expires_at": expires_at.isoformat(),
    }
    existing = _read_json(path)
    if existing:
        stable_keys = (
            "workflow_run_id",
            "task_id",
            "operation_id",
            "attempt_id",
            "role_instance",
            "purpose",
            "manifest_digest",
        )
        if any(existing.get(key) != binding.get(key) for key in stable_keys):
            raise ArtifactReadCapabilityError(
                "binding_divergent",
                f"artifact read binding diverged for attempt {attempt_id}",
            )
    atomic_write_text(path, json.dumps(binding, sort_keys=True, indent=2) + "\n")
    _chmod(path, 0o600)


def authorize_artifact_read_from_environment(
    state_dir: Path,
    *,
    manifest: Mapping[str, Any],
    event_log: EventLog,
    use_artifact_context: bool = False,
) -> dict[str, str]:
    """Authenticate the worker and return Kernel-bound consumer identity."""

    try:
        return _authorize_artifact_read_from_environment(
            state_dir,
            manifest=manifest,
            event_log=event_log,
            use_artifact_context=use_artifact_context,
        )
    except ArtifactReadCapabilityError as exc:
        _record_denial(event_log, manifest=manifest, code=exc.code)
        raise


def _authorize_artifact_read_from_environment(
    state_dir: Path,
    *,
    manifest: Mapping[str, Any],
    event_log: EventLog,
    use_artifact_context: bool = False,
) -> dict[str, str]:
    role_instance = str(os.environ.get("ZF_ROLE_INSTANCE") or "").strip()
    token = str(os.environ.get("ZF_ARTIFACT_READ_TOKEN") or "").strip()
    if not token:
        token_file = str(
            os.environ.get("ZF_ARTIFACT_READ_TOKEN_FILE") or ""
        ).strip()
        if token_file:
            try:
                token = Path(token_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ArtifactReadCapabilityError(
                    "capability_unreadable",
                    "artifact read credential is unavailable",
                ) from exc
    if not role_instance or not token:
        raise ArtifactReadCapabilityError(
            "capability_missing",
            "artifact read requires an attempt-scoped worker credential",
        )
    metadata = _credential_metadata(Path(state_dir), role_instance)
    if not metadata or not hmac.compare_digest(
        str(metadata.get("token_sha256") or ""),
        _digest(token),
    ):
        raise ArtifactReadCapabilityError(
            "capability_invalid",
            "artifact read credential is invalid or stale",
        )
    attempt_id = str(manifest.get("attempt_id") or "")
    binding = _read_json(_binding_path(Path(state_dir), attempt_id))
    if not binding:
        raise ArtifactReadCapabilityError(
            "capability_unbound",
            "attempt has no artifact read capability binding",
        )
    expected = {
        "attempt_id": attempt_id,
        "role_instance": role_instance,
        "workflow_run_id": str(manifest.get("workflow_run_id") or ""),
        "task_id": str(manifest.get("task_id") or ""),
        "manifest_digest": canonical_source_manifest_digest(manifest),
        "credential_generation": int(metadata.get("generation") or 0),
    }
    for key, value in expected.items():
        if binding.get(key) != value:
            raise ArtifactReadCapabilityError(
                "capability_stale",
                f"artifact read binding mismatch: {key}",
            )
    _require_not_expired(binding)
    role_name = str(metadata.get("role_name") or "")
    provider = str(metadata.get("provider") or "")
    purpose = str(binding.get("purpose") or "")
    claimed_role = str(os.environ.get("ZF_ROLE_NAME") or "").strip()
    claimed_provider = str(os.environ.get("ZF_ROLE_BACKEND") or "").strip()
    claimed_purpose = str(os.environ.get("ZF_ARTIFACT_PURPOSE") or "").strip()
    for label, claimed, actual in (
        ("role", claimed_role, role_name),
        ("provider", claimed_provider, provider),
        ("purpose", claimed_purpose, purpose),
    ):
        if claimed and claimed != actual:
            raise ArtifactReadCapabilityError(
                "identity_mismatch",
                f"artifact read {label} does not match the issued capability",
            )
    _require_current_operation(binding, event_log, use_artifact_context=use_artifact_context)
    return {
        "actor": role_instance,
        "role": role_name,
        "provider": provider,
        "purpose": purpose,
        "operation_id": str(binding.get("operation_id") or ""),
        "attempt_id": attempt_id,
    }


def canonical_source_manifest_digest(manifest: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_current_operation(
    binding: Mapping[str, Any],
    event_log: EventLog,
    *,
    use_artifact_context: bool = False,
) -> None:
    operation_id = str(binding.get("operation_id") or "")
    if not operation_id:
        return
    from zf.runtime.workflow_operation import load_workflow_operation

    if not use_artifact_context:
        operation = load_workflow_operation(event_log, operation_id)
    else:
        from zf.runtime.artifact_context_events import artifact_context_events
        from zf.runtime.workflow_operation import reduce_workflow_operations

        operation = reduce_workflow_operations(artifact_context_events(event_log)).get(operation_id)
    if not operation:
        raise ArtifactReadCapabilityError(
            "operation_missing",
            "artifact read operation is not current",
        )
    if str(operation.get("active_attempt_id") or "") != str(
        binding.get("attempt_id") or ""
    ):
        raise ArtifactReadCapabilityError(
            "attempt_stale",
            "artifact read attempt is no longer active",
        )
    if str(operation.get("status") or "") not in _ACTIVE_OPERATION_STATUSES:
        raise ArtifactReadCapabilityError(
            "operation_terminal",
            "artifact read operation is no longer active",
        )


def _record_denial(
    event_log: EventLog,
    *,
    manifest: Mapping[str, Any],
    code: str,
) -> None:
    workflow_run_id = str(manifest.get("workflow_run_id") or "")[:160]
    event_log.append(ZfEvent(
        type="artifact.read.denied",
        actor="kernel",
        task_id=str(manifest.get("task_id") or "")[:160] or None,
        correlation_id=workflow_run_id or None,
        payload={
            "workflow_run_id": workflow_run_id,
            "attempt_id": str(manifest.get("attempt_id") or "")[:160],
            "denial_code": str(code or "capability_denied")[:160],
        },
    ))


def _require_not_expired(binding: Mapping[str, Any]) -> None:
    raw = str(binding.get("expires_at") or "")
    try:
        expires_at = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ArtifactReadCapabilityError(
            "capability_invalid",
            "artifact read capability expiry is invalid",
        ) from exc
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        raise ArtifactReadCapabilityError(
            "capability_expired",
            "artifact read capability has expired",
        )


def _credential_metadata(state_dir: Path, role_instance: str) -> dict[str, Any]:
    return _read_json(
        state_dir
        / "private"
        / "artifact-read"
        / "roles"
        / f"{_safe_component(role_instance)}.json"
    )


def _binding_path(state_dir: Path, attempt_id: str) -> Path:
    return (
        state_dir
        / "private"
        / "artifact-read"
        / "attempts"
        / f"{_safe_component(attempt_id)}.json"
    )


def _reissue_attempt_bindings(
    state_dir: Path,
    role_instance: str,
    generation: int,
) -> None:
    root = state_dir / "private" / "artifact-read" / "attempts"
    for path in root.glob("*.json") if root.exists() else ():
        binding = _read_json(path)
        if binding.get("role_instance") != role_instance:
            continue
        binding["credential_generation"] = generation
        atomic_write_text(path, json.dumps(binding, sort_keys=True, indent=2) + "\n")
        _chmod(path, 0o600)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_component(value: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value)
    ).strip("-.") or "item"


def _chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


__all__ = [
    "ARTIFACT_READ_BINDING_SCHEMA",
    "ARTIFACT_READ_CREDENTIAL_SCHEMA",
    "ArtifactReadCapabilityError",
    "authorize_artifact_read_from_environment",
    "bind_attempt_artifact_read_capability",
    "canonical_source_manifest_digest",
    "provision_role_artifact_read_credential",
]
