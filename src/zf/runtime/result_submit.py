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
from zf.runtime.plan_candidate_submit_validation import (
    plan_synth_verdict_errors,
    validate_plan_candidate_semantic,
    validate_plan_synth_result,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
    reduce_workflow_operations,
)


MAX_RESULT_BYTES = 1024 * 1024
_PLAN_SYNTH_PROFILE_ID = "plan-synth"


class ResultSubmitError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


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


def is_authorized_result_scratch_write(
    state_dir: Path,
    event_log: EventLog,
    *,
    role_instance: str,
    target: Path,
) -> bool:
    """Allow one provider write only to its current signed result scratch."""

    state_root = Path(state_dir).resolve()
    actual = Path(os.path.abspath(Path(target).expanduser()))
    try:
        actual.relative_to(state_root)
        mode = actual.lstat().st_mode
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        return False

    current_request = _current_running_operation_request(
        state_root,
        event_log,
        role_instance=role_instance,
    )
    if current_request is None:
        return False
    current, request = current_request
    expected = Path(os.path.abspath(
        state_root / str(request.get("result_scratch_ref") or "")
    ))
    if actual != expected:
        return False

    binding = _read_json(
        _binding_path(state_root, str(current.get("operation_id") or ""))
    )
    expected_binding = {
        "role_instance": role_instance,
        "attempt_id": str(current.get("active_attempt_id") or ""),
        "lease_id": str(current.get("lease_id") or ""),
    }
    return (
        bool(binding)
        and not bool(binding.get("used"))
        and all(binding.get(key) == value for key, value in expected_binding.items())
    )


def authorized_operation_workdir_write_scopes(
    state_dir: Path,
    event_log: EventLog,
    *,
    role_instance: str,
    task_id: str = "",
) -> list[str]:
    """Return kernel-pinned write scopes from the actor's running operation."""

    current_request = _current_running_operation_request(
        Path(state_dir).resolve(),
        event_log,
        role_instance=role_instance,
        task_id=task_id,
    )
    if current_request is None:
        return []
    _, request = current_request
    raw_scopes = request.get("workdir_write_scopes")
    if not isinstance(raw_scopes, list):
        return []
    scopes: list[str] = []
    for raw_scope in raw_scopes:
        scope = str(raw_scope or "").strip()
        scope_path = Path(scope)
        if (
            not scope
            or scope_path.is_absolute()
            or ".." in scope_path.parts
            or scope.startswith(".zf/")
        ):
            continue
        if scope not in scopes:
            scopes.append(scope)
    return scopes


def _current_running_operation_request(
    state_root: Path,
    event_log: EventLog,
    *,
    role_instance: str,
    task_id: str = "",
) -> tuple[dict[str, Any], Mapping[str, Any]] | None:
    operations = reduce_workflow_operations(event_log.read_all())
    candidates = [
        operation
        for operation in operations.values()
        if operation.get("status") == "running"
        and str(operation.get("role_instance") or "") == role_instance
        and (
            not task_id
            or str(operation.get("task_id") or "") == task_id
        )
    ]
    candidates.sort(
        key=lambda operation: (
            str(operation.get("last_event_at") or ""),
            str(operation.get("last_event_id") or ""),
        ),
        reverse=True,
    )
    for current in candidates:
        descriptor = current.get("request_ref")
        if not isinstance(descriptor, Mapping):
            continue
        try:
            request_body = hydrate_sidecar_ref(
                state_root,
                dict(descriptor),
            ).payload
        except Exception:
            continue
        if not isinstance(request_body, Mapping):
            continue
        request = request_body.get("request")
        if not isinstance(request, Mapping):
            continue
        if (
            not str(request.get("task_pipeline_stage") or "").strip()
            and str(request.get("role_instance") or "") != role_instance
        ):
            continue
        if task_id and str(request.get("task_id") or "") != task_id:
            continue
        return current, request
    return None


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

    def validate_plan_candidate(
        self,
        *,
        operation_id: str,
        semantic_result: Mapping[str, Any] | None = None,
        result_file: Path | None = None,
        use_operation_scratch: bool = False,
        role_instance: str,
        credential: str,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        """Preflight a supported result without consuming its submit binding.

        Plan producers carry the full candidate-validation context. Plan Synth
        operations carry only the critic verdict delta, so they use their
        pinned typed-result profile instead of pretending to be a second Plan
        candidate producer.
        """

        _operation, request, semantic, profile_id, _revision = (
            self._authorized_semantic_result(
                operation_id=operation_id,
                semantic_result=semantic_result,
                result_file=result_file,
                use_operation_scratch=use_operation_scratch,
                role_instance=role_instance,
                credential=credential,
            )
        )
        validation = validate_plan_candidate_semantic(
            state_dir=self.state_dir,
            request=request,
            semantic=semantic,
            profile_id=profile_id,
            project_root=Path(project_root or Path.cwd()),
        )
        if validation is not None:
            return validation
        if profile_id == _PLAN_SYNTH_PROFILE_ID:
            return validate_plan_synth_result(
                registry=self.registry,
                event_type=self._canonical_event_type(request, semantic),
                operation=_operation,
                request=request,
                semantic=semantic,
                operation_id=operation_id,
                role_instance=role_instance,
                profile_id=profile_id,
                revision=_revision,
            )
        if profile_id in {"task-verify", "candidate-verify"}:
            from zf.runtime.verification_submit_validation import validate_verification_submission

            return validate_verification_submission(
                state_dir=self.state_dir,
                registry=self.registry,
                event_type=self._canonical_event_type(request, semantic),
                operation=_operation,
                request=request,
                semantic=semantic,
                role_instance=role_instance,
                profile_id=profile_id,
                revision=_revision,
                delivery=self._current_task_pipeline_delivery(_operation, request),
            )
        raise ResultSubmitError(
            "plan_candidate_validation_unavailable",
            "operation has no supported pre-submit validation profile",
        )

    def submit(
        self,
        *,
        operation_id: str,
        semantic_result: Mapping[str, Any] | None = None,
        result_file: Path | None = None,
        use_operation_scratch: bool = False,
        role_instance: str,
        credential: str,
    ) -> SubmittedSemanticResult:
        operation, request, semantic, profile_id, revision = (
            self._authorized_semantic_result(
                operation_id=operation_id,
                semantic_result=semantic_result,
                result_file=result_file,
                use_operation_scratch=use_operation_scratch,
                role_instance=role_instance,
                credential=credential,
            )
        )
        profile = self.registry.profile(profile_id, revision)
        if profile_id == _PLAN_SYNTH_PROFILE_ID:
            verdict_errors = plan_synth_verdict_errors(semantic)
            if verdict_errors:
                raise ResultSubmitError(
                    "plan_synth_verdict_required",
                    "Plan Synth requires an authored review verdict",
                    details={"issues": verdict_errors},
                )
        candidate_validation = validate_plan_candidate_semantic(
            state_dir=self.state_dir,
            request=request,
            semantic=semantic,
            profile_id=profile_id,
            project_root=Path.cwd(),
        )
        if (
            candidate_validation is not None
            and candidate_validation.get("status") != "passed"
        ):
            errors = [
                {
                    **dict(item),
                    "required_change": (
                        "Patch the same result scratch/artifact set and rerun "
                        "result validate before submitting."
                    ),
                }
                for item in candidate_validation.get("errors", [])
                if isinstance(item, Mapping)
            ]
            raise ResultSubmitError(
                "plan_candidate_not_admitted",
                "Plan candidate failed the pinned pre-submit evaluator",
                details={
                    "status": "repair_required",
                    "attempt_domain": "protocol_repair",
                    "semantic_rework_cost": 0,
                    "issues": errors,
                },
            )
        event_type = self._canonical_event_type(request, semantic)
        task_pipeline_delivery = self._current_task_pipeline_delivery(
            operation,
            request,
        )
        identity = dict(request.get("result_identity") or {})
        for key in ("placement_epoch", "task_stage_session_binding"):
            value = task_pipeline_delivery.get(key)
            if value not in (None, ""):
                identity[key] = value
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
            event, adapted = self.registry.adapt_semantic_result(
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
        event.payload.update(_compatibility_projection(
            profile.semantic_field,
            {profile.semantic_field: adapted.payload},
        ))
        policy = self._input_policy(
            request,
            task_pipeline_delivery=task_pipeline_delivery,
        )
        outcome = self.admission.report_legacy_result(
            event,
            mode="blocking",
            operation={
                **dict(operation),
                "result_identity": dict(identity),
                "output_profile_id": profile_id,
                "output_profile_revision": revision,
                "canonical_success_event": str(request.get("canonical_success_event") or ""),
                "handoff_authority_contract": dict(request.get("handoff_authority_contract") or {}),
            },
            input_policy=policy,
            require_semantic_submit=True,
            semantic_submit=True,
        )
        if not outcome.admitted:
            codes = ", ".join(str(item.get("code") or "invalid") for item in outcome.issues)
            from zf.runtime.call_result_correction import repair_instruction

            issues = [
                {
                    **dict(item),
                    "required_change": repair_instruction(item),
                }
                for item in outcome.issues
            ]
            raise ResultSubmitError(
                "result_not_admitted",
                f"semantic result was not admitted: {codes or outcome.status}",
                details={
                    "status": outcome.status,
                    "repair_round": outcome.repair_round,
                    "correction_ref": dict(outcome.correction_ref or {}),
                    "issues": issues,
                },
            )
        event.payload.pop(profile.semantic_field, None)
        event.payload.update({
            "control_result_ref": dict(outcome.control_result_ref or {}),
            "call_result_envelope_ref": dict(outcome.envelope_ref or {}),
            "semantic_submit_admission_event_id": outcome.admitted_event_id,
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

    def _authorized_semantic_result(
        self,
        *,
        operation_id: str,
        semantic_result: Mapping[str, Any] | None,
        result_file: Path | None,
        use_operation_scratch: bool,
        role_instance: str,
        credential: str,
    ) -> tuple[
        dict[str, Any],
        Mapping[str, Any],
        dict[str, Any],
        str,
        str,
    ]:
        operation = load_workflow_operation(self.event_log, operation_id)
        if operation is None:
            raise ResultSubmitError(
                "operation_missing",
                f"unknown operation {operation_id!r}",
            )
        existing_binding = _read_json(_binding_path(self.state_dir, operation_id))
        if bool(existing_binding.get("used")):
            raise ResultSubmitError(
                "duplicate_submit",
                "submit capability was already consumed",
            )
        if str(operation.get("status") or "") != "running":
            raise ResultSubmitError(
                "operation_not_running",
                f"operation {operation_id!r} is {operation.get('status')!r}",
            )
        request_body = self._request_body(operation)
        request = request_body.get("request")
        if not isinstance(request, Mapping):
            raise ResultSubmitError(
                "request_invalid",
                "operation request body is missing",
            )
        self._authorize(
            operation=operation,
            request=request,
            role_instance=role_instance,
            credential=credential,
        )
        input_count = sum((
            semantic_result is not None,
            result_file is not None,
            use_operation_scratch,
        ))
        if input_count != 1:
            raise ResultSubmitError(
                "input_mode_invalid",
                "provide exactly one semantic result input",
            )
        if use_operation_scratch:
            scratch_ref = str(request.get("result_scratch_ref") or "").strip()
            if not scratch_ref:
                raise ResultSubmitError(
                    "result_scratch_missing",
                    "operation has no signed result scratch",
                )
            result_file = self.state_dir / scratch_ref
        if result_file is not None:
            semantic_result = self._read_result_file(request, result_file)
        assert semantic_result is not None
        semantic = dict(semantic_result)
        profile_id = str(request.get("output_profile_id") or "")
        revision = str(request.get("output_profile_revision") or "")
        profile = self.registry.profile(profile_id, revision)
        wrapped = semantic.get(profile.semantic_field)
        if isinstance(wrapped, Mapping):
            sibling_fields = sorted(
                str(key)
                for key in semantic
                if key != profile.semantic_field
            )
            if sibling_fields:
                raise ResultSubmitError(
                    "ambiguous_semantic_result",
                    "semantic result must be either the profile body or an exact "
                    f"{profile.semantic_field!r} wrapper; sibling fields would be "
                    "discarded: "
                    + ", ".join(sibling_fields),
                )
            semantic = dict(wrapped)
        return dict(operation), request, semantic, profile_id, revision

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

    def _current_task_pipeline_delivery(
        self,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not str(request.get("task_pipeline_stage") or "").strip():
            return {}
        operation_id = str(operation.get("operation_id") or "").strip()
        attempt_id = str(operation.get("active_attempt_id") or "").strip()
        dispatch_id = str(operation.get("dispatch_id") or "").strip()
        if not operation_id or not attempt_id:
            return {}
        for event in reversed(self.event_log.read_all()):
            if (
                event.type != "task.pipeline.stage.dispatched"
                or str(event.origin or "") != "kernel"
            ):
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("operation_id") or "") == operation_id
                and str(payload.get("attempt_id") or "") == attempt_id
                and (
                    not dispatch_id
                    or str(payload.get("dispatch_id") or "") == dispatch_id
                )
            ):
                return dict(payload)
        return {}

    def _input_policy(
        self,
        request: Mapping[str, Any],
        *,
        task_pipeline_delivery: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        delivery = task_pipeline_delivery or {}
        if str(request.get("task_pipeline_stage") or "").strip():
            descriptor = delivery.get("input_consumption_policy_ref")
        else:
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
    report = (
        dict(result)
        if field == "report"
        else {
            "status": status,
            "summary": str(result.get("summary") or ""),
            "findings": list(result.get("findings") or []),
            "fix_items": list(result.get("fix_items") or []),
            "evidence_refs": list(
                result.get("verification_evidence_refs")
                or result.get("evidence_refs")
                or []
            ),
            "recommendation": "approve" if verdict == "passed" else "reject",
        }
    )
    artifact_evidence = (
        [
            str(item.get("ref") or "")
            for item in result.get("artifacts") or []
            if isinstance(item, Mapping)
            and str(item.get("ref") or "").strip()
        ]
        if field == "artifact_delivery_result"
        else []
    )
    evidence_refs = list(
        result.get("verification_evidence_refs")
        or result.get("evidence_refs")
        or []
    )
    unique_evidence: list[Any] = []
    for ref in [*evidence_refs, *artifact_evidence]:
        if ref not in unique_evidence:
            unique_evidence.append(ref)
    evidence_refs = unique_evidence
    return {
        "status": "completed",
        "summary": str(result.get("summary") or ""),
        "report": {**report, "evidence_refs": evidence_refs},
        **(
            {
                "source_commit": str(result.get("target_commit") or ""),
                "changed_files": list(result.get("changed_files") or []),
                "files_touched": list(result.get("changed_files") or []),
                "evidence_refs": list(result.get("evidence_refs") or []),
                "impl_self_check": dict(result.get("self_check") or {}),
                "known_gaps": list(result.get("known_gaps") or []),
                "failure_class": str(result.get("failure_class") or ""),
                "blocker_kind": str(result.get("blocker_kind") or ""),
                "reason": str(result.get("summary") or ""),
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
