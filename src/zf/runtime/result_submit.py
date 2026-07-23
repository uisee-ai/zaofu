"""Authorized semantic result submission for durable workflow operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.call_result_adapters import (
    ControlResultAdapterError,
    ControlResultAdapterRegistry,
)
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
)


MAX_RESULT_BYTES = 1024 * 1024


class ResultSubmitError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SubmittedSemanticResult:
    operation_id: str
    canonical_event_id: str
    canonical_event_type: str
    admitted_event_id: str
    envelope_ref: dict[str, Any]
    control_result_ref: dict[str, Any]


def provision_role_submit_credential(
    state_dir: Path,
    role_instance: str,
    *,
    rotate: bool = True,
) -> Path:
    """Issue a role-scoped transport credential without logging its secret."""

    role = _safe_component(role_instance)
    root = Path(state_dir) / "private" / "result-submit" / "roles"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    metadata_path = root / f"{role}.json"
    token_path = root / f"{role}.token"
    current = _read_json(metadata_path)
    if not rotate and token_path.is_file() and current:
        return token_path
    generation = int(current.get("generation") or 0) + 1
    token = secrets.token_urlsafe(32)
    atomic_write_text(token_path, token + "\n")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    atomic_write_text(
        metadata_path,
        json.dumps({
            "schema_version": "result-submit-role-credential.v1",
            "role_instance": role_instance,
            "generation": generation,
            "token_sha256": _digest(token),
            "token_ref": str(token_path),
        }, sort_keys=True, indent=2) + "\n",
    )
    try:
        metadata_path.chmod(0o600)
    except OSError:
        pass
    _reissue_role_bindings(Path(state_dir), role_instance, generation)
    return token_path


def bind_operation_submit_capability(
    state_dir: Path,
    *,
    operation_id: str,
    role_instance: str,
    attempt_id: str,
    lease_id: str,
) -> None:
    metadata = _credential_metadata(Path(state_dir), role_instance)
    if not metadata:
        return
    path = _binding_path(Path(state_dir), operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps({
            "schema_version": "result-submit-capability-binding.v1",
            "operation_id": operation_id,
            "role_instance": role_instance,
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "credential_generation": int(metadata.get("generation") or 0),
            "used": False,
        }, sort_keys=True, indent=2) + "\n",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


class SemanticResultSubmitService:
    def __init__(
        self,
        *,
        state_dir: Path,
        event_log: EventLog,
        event_writer: EventWriter,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.event_log = event_log
        self.event_writer = event_writer
        self.operations = WorkflowOperationService(
            state_dir=self.state_dir,
            event_log=event_log,
            event_writer=event_writer,
        )
        self.registry = ControlResultAdapterRegistry()
        self.admission = CallResultAdmissionService(
            state_dir=self.state_dir,
            event_log=event_log,
            event_writer=event_writer,
            operation_service=self.operations,
            adapters=self.registry,
        )

    def submit(
        self,
        *,
        operation_id: str,
        semantic_result: Mapping[str, Any] | None = None,
        result_file: Path | None = None,
        role_instance: str,
        credential: str,
    ) -> SubmittedSemanticResult:
        operation = load_workflow_operation(self.event_log, operation_id)
        if operation is None:
            raise ResultSubmitError("operation_missing", f"unknown operation {operation_id!r}")
        existing_binding = _read_json(_binding_path(self.state_dir, operation_id))
        if bool(existing_binding.get("used")):
            raise ResultSubmitError("duplicate_submit", "submit capability was already consumed")
        if str(operation.get("status") or "") != "running":
            raise ResultSubmitError(
                "operation_not_running",
                f"operation {operation_id!r} is {operation.get('status')!r}",
            )
        request_body = self._request_body(operation)
        request = request_body.get("request")
        if not isinstance(request, Mapping):
            raise ResultSubmitError("request_invalid", "operation request body is missing")
        self._authorize(
            operation=operation,
            request=request,
            role_instance=role_instance,
            credential=credential,
        )
        if (semantic_result is None) == (result_file is None):
            raise ResultSubmitError(
                "input_mode_invalid",
                "provide exactly one semantic result input",
            )
        if result_file is not None:
            semantic_result = self._read_result_file(request, result_file)
        assert semantic_result is not None
        semantic = dict(semantic_result)
        profile_id = str(request.get("output_profile_id") or "")
        revision = str(request.get("output_profile_revision") or "")
        profile = self.registry.profile(profile_id, revision)
        wrapped = semantic.get(profile.semantic_field)
        if isinstance(wrapped, Mapping):
            semantic = dict(wrapped)
        event_type = self._canonical_event_type(request, semantic)
        identity = dict(request.get("result_identity") or {})
        identity.update({
            "workflow_run_id": str(operation.get("workflow_run_id") or ""),
            "operation_id": operation_id,
            "request_hash": str(operation.get("request_hash") or ""),
            "attempt_id": str(operation.get("active_attempt_id") or ""),
            "dispatch_id": str(operation.get("dispatch_id") or ""),
            "lease_id": str(operation.get("lease_id") or ""),
            "role_instance": role_instance,
            "output_profile_id": profile_id,
            "output_profile_revision": revision,
        })
        source_event_id = ZfEvent(type=event_type).id
        try:
            event, _adapted = self.registry.adapt_semantic_result(
                self.state_dir,
                profile_id=profile_id,
                revision=revision,
                event_type=event_type,
                semantic_result=semantic,
                identity=identity,
                source_event_id=source_event_id,
                actor=role_instance,
                task_id=str(operation.get("task_id") or ""),
                correlation_id=str(operation.get("workflow_run_id") or ""),
            )
        except ControlResultAdapterError as exc:
            raise ResultSubmitError("profile_adapter_invalid", str(exc)) from exc
        event.payload.update(_compatibility_projection(profile.semantic_field, event.payload))
        policy = self._input_policy(request)
        outcome = self.admission.report_legacy_result(
            event,
            mode="blocking",
            operation={
                "workflow_run_id": str(operation.get("workflow_run_id") or ""),
                "parent_operation_id": str(operation.get("parent_operation_id") or ""),
                "operation_id": operation_id,
                "request_hash": str(operation.get("request_hash") or ""),
            },
            input_policy=policy,
        )
        if not outcome.admitted:
            codes = ", ".join(str(item.get("code") or "invalid") for item in outcome.issues)
            raise ResultSubmitError(
                "result_not_admitted",
                f"semantic result was not admitted: {codes or outcome.status}",
            )
        event.payload.pop(profile.semantic_field, None)
        event.payload.update({
            "control_result_ref": dict(outcome.control_result_ref or {}),
            "call_result_envelope_ref": dict(outcome.envelope_ref or {}),
            "semantic_result_profile": {
                "profile_id": profile_id,
                "revision": revision,
            },
        })
        canonical = self.event_writer.append(event)
        self._mark_used(operation_id)
        return SubmittedSemanticResult(
            operation_id=operation_id,
            canonical_event_id=canonical.id,
            canonical_event_type=canonical.type,
            admitted_event_id=outcome.admitted_event_id,
            envelope_ref=dict(outcome.envelope_ref or {}),
            control_result_ref=dict(outcome.control_result_ref or {}),
        )

    def _request_body(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        descriptor = operation.get("request_ref")
        if not isinstance(descriptor, Mapping):
            raise ResultSubmitError("request_ref_missing", "operation request ref is missing")
        hydrated = hydrate_sidecar_ref(self.state_dir, dict(descriptor)).payload
        if not isinstance(hydrated, dict):
            raise ResultSubmitError("request_invalid", "operation request must be an object")
        return dict(hydrated)

    def _authorize(
        self,
        *,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
        role_instance: str,
        credential: str,
    ) -> None:
        expected_role = str(operation.get("role_instance") or request.get("role_instance") or "")
        if not role_instance or role_instance != expected_role:
            raise ResultSubmitError("role_mismatch", "submitter does not own this operation")
        metadata = _credential_metadata(self.state_dir, role_instance)
        if not metadata or not hmac.compare_digest(
            str(metadata.get("token_sha256") or ""),
            _digest(credential),
        ):
            raise ResultSubmitError("capability_invalid", "submit credential is invalid or stale")
        binding = _read_json(_binding_path(self.state_dir, str(operation.get("operation_id") or "")))
        if not binding:
            raise ResultSubmitError("capability_unbound", "operation has no submit capability binding")
        expected = {
            "role_instance": expected_role,
            "attempt_id": str(operation.get("active_attempt_id") or ""),
            "lease_id": str(operation.get("lease_id") or ""),
            "credential_generation": int(metadata.get("generation") or 0),
        }
        for key, value in expected.items():
            if binding.get(key) != value:
                raise ResultSubmitError("capability_stale", f"submit binding mismatch: {key}")
        if bool(binding.get("used")):
            raise ResultSubmitError("duplicate_submit", "submit capability was already consumed")

    def _read_result_file(
        self,
        request: Mapping[str, Any],
        path: Path,
    ) -> dict[str, Any]:
        expected = Path(os.path.abspath(
            self.state_dir / str(request.get("result_scratch_ref") or "")
        ))
        actual = Path(os.path.abspath(Path(path).expanduser()))
        if actual != expected or self.state_dir.resolve() not in actual.parents:
            raise ResultSubmitError("result_file_outside_scratch", "result file is not signed scratch")
        try:
            mode = actual.lstat().st_mode
        except OSError as exc:
            raise ResultSubmitError("result_file_unreadable", str(exc)) from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ResultSubmitError("result_file_unsafe", "result file must be a regular non-symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(actual, flags)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ResultSubmitError(
                        "result_file_unsafe",
                        "opened result file is not regular",
                    )
                chunks: list[bytes] = []
                remaining = MAX_RESULT_BYTES + 1
                while remaining > 0:
                    chunk = os.read(fd, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
            finally:
                os.close(fd)
        except ResultSubmitError:
            raise
        except OSError as exc:
            raise ResultSubmitError("result_file_unreadable", str(exc)) from exc
        return _parse_result(raw)

    def _canonical_event_type(
        self,
        request: Mapping[str, Any],
        semantic: Mapping[str, Any],
    ) -> str:
        execution_status = str(semantic.get("execution_status") or "completed").lower()
        key = "canonical_failure_event" if execution_status == "failed" else "canonical_success_event"
        event_type = str(request.get(key) or "")
        if not event_type:
            raise ResultSubmitError("canonical_event_missing", f"operation has no {key}")
        return event_type

    def _input_policy(self, request: Mapping[str, Any]) -> dict[str, Any]:
        descriptor = request.get("input_consumption_policy_ref")
        if not isinstance(descriptor, Mapping) or not str(descriptor.get("ref") or ""):
            return {}
        hydrated = hydrate_sidecar_ref(self.state_dir, dict(descriptor)).payload
        return dict(hydrated) if isinstance(hydrated, Mapping) else {}

    def _mark_used(self, operation_id: str) -> None:
        path = _binding_path(self.state_dir, operation_id)
        binding = _read_json(path)
        binding["used"] = True
        atomic_write_text(path, json.dumps(binding, sort_keys=True, indent=2) + "\n")


def credential_from_environment() -> tuple[str, str]:
    role = str(os.environ.get("ZF_ROLE_INSTANCE") or "").strip()
    token = str(os.environ.get("ZF_RESULT_SUBMIT_TOKEN") or "").strip()
    if not token:
        token_file = str(os.environ.get("ZF_RESULT_SUBMIT_TOKEN_FILE") or "").strip()
        if token_file:
            try:
                token = Path(token_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ResultSubmitError("capability_unreadable", str(exc)) from exc
    if not role or not token:
        raise ResultSubmitError(
            "capability_missing",
            "result submit requires transport-scoped role credentials",
        )
    return role, token


def parse_semantic_result_bytes(raw: bytes) -> dict[str, Any]:
    return _parse_result(raw)


def _parse_result(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESULT_BYTES:
        raise ResultSubmitError("result_too_large", "semantic result exceeds 1 MiB")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResultSubmitError("result_json_invalid", str(exc)) from exc
    if not isinstance(value, dict):
        raise ResultSubmitError("result_shape_invalid", "semantic result must be a JSON object")
    return value


def _compatibility_projection(field: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    result = payload.get(field)
    result = result if isinstance(result, Mapping) else {}
    verdict = str(result.get("verdict") or "passed").lower()
    status = "passed" if verdict == "passed" else "failed"
    return {
        "status": "completed",
        "summary": str(result.get("summary") or ""),
        "report": {
            "status": status,
            "summary": str(result.get("summary") or ""),
            "findings": list(result.get("findings") or []),
            "recommendation": "approve" if verdict == "passed" else "reject",
        },
        **(
            {
                "source_commit": str(result.get("target_commit") or ""),
                "files_touched": list(result.get("changed_files") or []),
                "evidence_refs": list(result.get("evidence_refs") or []),
                "impl_self_check": dict(result.get("self_check") or {}),
                "known_gaps": list(result.get("known_gaps") or []),
            }
            if field == "implementation_result" else {}
        ),
    }


def _credential_metadata(state_dir: Path, role_instance: str) -> dict[str, Any]:
    path = state_dir / "private" / "result-submit" / "roles" / f"{_safe_component(role_instance)}.json"
    return _read_json(path)


def _binding_path(state_dir: Path, operation_id: str) -> Path:
    return state_dir / "private" / "result-submit" / "operations" / f"{_safe_component(operation_id)}.json"


def _reissue_role_bindings(state_dir: Path, role_instance: str, generation: int) -> None:
    root = state_dir / "private" / "result-submit" / "operations"
    for path in root.glob("*.json") if root.exists() else ():
        binding = _read_json(path)
        if binding.get("role_instance") != role_instance or bool(binding.get("used")):
            continue
        binding["credential_generation"] = generation
        atomic_write_text(path, json.dumps(binding, sort_keys=True, indent=2) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value)).strip("-.") or "item"


__all__ = [
    "MAX_RESULT_BYTES",
    "ResultSubmitError",
    "SemanticResultSubmitService",
    "SubmittedSemanticResult",
    "bind_operation_submit_capability",
    "credential_from_environment",
    "parse_semantic_result_bytes",
    "provision_role_submit_credential",
]
