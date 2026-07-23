"""Legacy role-event adapters for typed call control results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.verification_result import (
    VerificationResultError,
    validate_verification_result,
)
from zf.runtime.goal_closure_result import (
    GoalClosureResultError,
    SCHEMA_VERSION as GOAL_CLOSURE_RESULT_SCHEMA,
    normalize_goal_closure_result,
)
from zf.runtime.plan_synth_handoff import (
    PLAN_SYNTH_PROFILE_ID,
    PLAN_SYNTH_PROFILE_REVISION,
    PLAN_SYNTH_RESULT_SCHEMA,
)


IMPLEMENTATION_RESULT_SCHEMA = "implementation-result.v1"
FANOUT_AGGREGATE_RESULT_SCHEMA = "fanout-aggregate-result.v1"


class ControlResultAdapterError(ValueError):
    """No adapter can produce a trustworthy typed control result."""


@dataclass(frozen=True)
class AdaptedControlResult:
    adapter_id: str
    schema_version: str
    payload: dict[str, Any]
    descriptor: dict[str, Any]
    issues: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class ControlResultAdapter:
    adapter_id: str
    schema_version: str
    accepts: Callable[[ZfEvent], bool]
    normalize: Callable[[ZfEvent], tuple[dict[str, Any], list[dict[str, str]]]]


@dataclass(frozen=True)
class CallResultProfile:
    profile_id: str
    revision: str
    schema_version: str
    adapter_id: str
    semantic_field: str
    allowed_event_types: tuple[str, ...]


class ControlResultAdapterRegistry:
    def __init__(
        self,
        adapters: list[ControlResultAdapter] | None = None,
        profiles: list[CallResultProfile] | None = None,
    ) -> None:
        self._adapters = list(adapters or default_control_result_adapters())
        self._profiles = {
            (item.profile_id, item.revision): item
            for item in (profiles or default_call_result_profiles())
        }

    def adapter_for(self, event: ZfEvent) -> ControlResultAdapter | None:
        return next((item for item in self._adapters if item.accepts(event)), None)

    def adapt(self, state_dir: Path, event: ZfEvent) -> AdaptedControlResult:
        adapter = self.adapter_for(event)
        if adapter is None:
            raise ControlResultAdapterError(
                f"no call-result adapter for {event.type!r}"
            )
        payload, issues = adapter.normalize(event)
        descriptor = write_immutable_json_sidecar(
            state_dir,
            payload,
            root=f"call-results/control/{adapter.schema_version}",
            kind="call_control_result",
            schema_version=adapter.schema_version,
            created_by=f"call-result-adapter:{adapter.adapter_id}",
            source_event_id=event.id,
        )
        return AdaptedControlResult(
            adapter_id=adapter.adapter_id,
            schema_version=adapter.schema_version,
            payload=payload,
            descriptor=descriptor,
            issues=tuple(issues),
        )

    def profile(self, profile_id: str, revision: str) -> CallResultProfile:
        profile = self._profiles.get((str(profile_id), str(revision)))
        if profile is None:
            raise ControlResultAdapterError(
                f"unknown call-result profile {profile_id!r} revision {revision!r}"
            )
        if not any(item.adapter_id == profile.adapter_id for item in self._adapters):
            raise ControlResultAdapterError(
                f"call-result profile {profile_id!r} references missing adapter "
                f"{profile.adapter_id!r}"
            )
        return profile

    def adapt_semantic_result(
        self,
        state_dir: Path,
        *,
        profile_id: str,
        revision: str,
        event_type: str,
        semantic_result: Mapping[str, Any],
        identity: Mapping[str, Any],
        source_event_id: str,
        actor: str,
        task_id: str,
        correlation_id: str,
    ) -> tuple[ZfEvent, AdaptedControlResult]:
        """Build a legacy-compatible event through one pinned result profile."""

        profile = self.profile(profile_id, revision)
        if profile.allowed_event_types and event_type not in profile.allowed_event_types:
            raise ControlResultAdapterError(
                f"event {event_type!r} is not allowed by profile {profile_id!r}"
            )
        protected = {
            key: value for key, value in identity.items()
            if value not in (None, "")
        }
        result = {**dict(semantic_result), **protected}
        result.setdefault("schema_version", profile.schema_version)
        event = ZfEvent(
            id=source_event_id,
            type=event_type,
            actor=actor,
            task_id=task_id or None,
            payload={**protected, profile.semantic_field: result},
            correlation_id=correlation_id or None,
        )
        adapted = self.adapt(state_dir, event)
        if adapted.adapter_id != profile.adapter_id:
            raise ControlResultAdapterError(
                f"profile {profile_id!r} selected adapter {adapted.adapter_id!r}; "
                f"expected {profile.adapter_id!r}"
            )
        return event, adapted


def default_control_result_adapters() -> list[ControlResultAdapter]:
    return [
        ControlResultAdapter(
            adapter_id="goal-closure-result-v1",
            schema_version=GOAL_CLOSURE_RESULT_SCHEMA,
            accepts=_is_goal_closure_event,
            normalize=_normalize_goal_closure,
        ),
        ControlResultAdapter(
            adapter_id="fanout-aggregate-result-v1",
            schema_version=FANOUT_AGGREGATE_RESULT_SCHEMA,
            accepts=_is_selected_fanout_aggregate,
            normalize=_normalize_fanout_aggregate,
        ),
        ControlResultAdapter(
            adapter_id="plan-synthesis-result-v1",
            schema_version=PLAN_SYNTH_RESULT_SCHEMA,
            accepts=_is_plan_synth_event,
            normalize=_normalize_plan_synth,
        ),
        ControlResultAdapter(
            adapter_id="verification-result-v1-explicit",
            schema_version="verification-result.v1",
            accepts=_is_verification_event,
            normalize=_normalize_verification,
        ),
        ControlResultAdapter(
            adapter_id="implementation-result-v1-legacy",
            schema_version=IMPLEMENTATION_RESULT_SCHEMA,
            accepts=_is_implementation_event,
            normalize=_normalize_implementation,
        ),
    ]


def default_call_result_profiles() -> list[CallResultProfile]:
    return [
        CallResultProfile(
            profile_id="thin-judge-goal-closure",
            revision="1",
            schema_version=GOAL_CLOSURE_RESULT_SCHEMA,
            adapter_id="goal-closure-result-v1",
            semantic_field="goal_closure_result",
            allowed_event_types=(),
        ),
        *[
            CallResultProfile(
                profile_id=profile_id,
                revision="1",
                schema_version="verification-result.v1",
                adapter_id="verification-result-v1-explicit",
                semantic_field="verification_result",
                allowed_event_types=(),
            )
            for profile_id in ("task-verify", "candidate-verify", "global-rescan")
        ],
        CallResultProfile(
            profile_id="implementation",
            revision="1",
            schema_version=IMPLEMENTATION_RESULT_SCHEMA,
            adapter_id="implementation-result-v1-legacy",
            semantic_field="implementation_result",
            allowed_event_types=("dev.build.done", "dev.blocked", "task.ref.updated"),
        ),
        CallResultProfile(
            profile_id=PLAN_SYNTH_PROFILE_ID,
            revision=PLAN_SYNTH_PROFILE_REVISION,
            schema_version=PLAN_SYNTH_RESULT_SCHEMA,
            adapter_id="plan-synthesis-result-v1",
            semantic_field="plan_synthesis_result",
            allowed_event_types=("fanout.synth.completed",),
        ),
    ]


def call_result_profile_identity(
    *,
    operation_type: str,
    stage_id: str,
    payload: Mapping[str, Any],
) -> tuple[str, str]:
    explicit_id = str(payload.get("output_profile_id") or "").strip()
    explicit_revision = str(payload.get("output_profile_revision") or "1").strip()
    if explicit_id:
        ControlResultAdapterRegistry().profile(explicit_id, explicit_revision)
        return explicit_id, explicit_revision
    identity = " ".join((stage_id, str(payload.get("verification_owner") or ""))).lower()
    if str(payload.get("closure_identity") or "").strip() or str(
        payload.get("goal_claim_set_ref") or ""
    ).strip():
        return "thin-judge-goal-closure", "1"
    if operation_type == "fanout_synth" or "plan-synth" in identity:
        return PLAN_SYNTH_PROFILE_ID, PLAN_SYNTH_PROFILE_REVISION
    if "writer" in operation_type:
        return "implementation", "1"
    if "global" in identity or "rescan" in identity:
        return "global-rescan", "1"
    if "candidate" in identity:
        return "candidate-verify", "1"
    return "task-verify", "1"


def is_supported_call_result_event(event: ZfEvent) -> bool:
    return ControlResultAdapterRegistry().adapter_for(event) is not None


def hydrate_profiled_control_result_event(
    state_dir: Path,
    event: ZfEvent,
    *,
    registry: ControlResultAdapterRegistry | None = None,
) -> ZfEvent:
    """Hydrate one ref-backed result through its pinned profile revision."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    profile_ref = payload.get("semantic_result_profile")
    if not isinstance(profile_ref, Mapping):
        return event
    profile_id = str(profile_ref.get("profile_id") or payload.get("output_profile_id") or "")
    revision = str(profile_ref.get("revision") or payload.get("output_profile_revision") or "")
    profile = (registry or ControlResultAdapterRegistry()).profile(profile_id, revision)
    if isinstance(payload.get(profile.semantic_field), Mapping):
        return event
    descriptor = payload.get("control_result_ref")
    if not isinstance(descriptor, Mapping):
        raise ControlResultAdapterError("ref-backed call result has no control_result_ref")
    try:
        hydrated = hydrate_sidecar_ref(Path(state_dir), dict(descriptor)).payload
    except Exception as exc:
        raise ControlResultAdapterError(f"control-result hydration failed: {exc}") from exc
    if not isinstance(hydrated, Mapping):
        raise ControlResultAdapterError("hydrated control result must be an object")
    return replace(event, payload={**payload, profile.semantic_field: dict(hydrated)})


def _is_verification_event(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if isinstance(payload.get("verification_result"), Mapping):
        return True
    return event.type in {
        "verify.child.completed",
        "verify.child.failed",
        "review.child.completed",
        "review.child.failed",
        "judge.child.completed",
        "judge.child.failed",
    }


def _is_goal_closure_event(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw = payload.get("goal_closure_result")
    if not isinstance(raw, Mapping) and isinstance(payload.get("report"), Mapping):
        raw = payload["report"].get("goal_closure_result")
    return (
        isinstance(raw, Mapping)
        and str(raw.get("schema_version") or GOAL_CLOSURE_RESULT_SCHEMA)
        == GOAL_CLOSURE_RESULT_SCHEMA
    )


def _normalize_goal_closure(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    try:
        return normalize_goal_closure_result(payload), []
    except GoalClosureResultError as exc:
        raw = payload.get("goal_closure_result")
        if not isinstance(raw, Mapping) and isinstance(payload.get("report"), Mapping):
            raw = payload["report"].get("goal_closure_result")
        result = dict(raw) if isinstance(raw, Mapping) else {
            "schema_version": GOAL_CLOSURE_RESULT_SCHEMA,
        }
        return result, [{
            "field": "control_result",
            "code": "schema_invalid",
            "message": str(exc),
        }]


def _is_implementation_event(event: ZfEvent) -> bool:
    return event.type in {"dev.build.done", "dev.blocked", "task.ref.updated"}


def _is_selected_fanout_aggregate(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return (
        event.type == "fanout.aggregate.completed"
        and bool(payload.get("workflow_operation_id") or payload.get("operation_id"))
        and bool(payload.get("workflow_operation_request_hash") or payload.get("request_hash"))
    )


def _is_plan_synth_event(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return (
        event.type == "fanout.synth.completed"
        and str(payload.get("output_profile_id") or "") == PLAN_SYNTH_PROFILE_ID
    )


def _normalize_plan_synth(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    report = payload.get("report") if isinstance(payload.get("report"), Mapping) else {}
    recommendation = str(
        payload.get("recommendation")
        or report.get("recommendation")
        or "abstain"
    ).lower()
    status = str(payload.get("status") or report.get("status") or "completed").lower()
    failed = status in {"failed", "failure"} or recommendation in {
        "reject",
        "rejected",
        "block",
        "blocked",
        "needs_rework",
    }
    result = {
        "schema_version": PLAN_SYNTH_RESULT_SCHEMA,
        "execution_status": "failed" if status in {"failed", "failure"} else "completed",
        "verdict": "rejected" if failed else "passed",
        "workflow_run_id": _text(payload, "workflow_run_id", "trace_id"),
        "fanout_id": _text(payload, "fanout_id"),
        "stage_id": _text(payload, "stage_id"),
        "plan_revision": _text(payload, "plan_revision"),
        "plan_synth_contract_ref": _text(payload, "plan_synth_contract_ref"),
        "plan_synth_contract_digest": _text(payload, "plan_synth_contract_digest"),
        "summary": str(payload.get("summary") or report.get("summary") or ""),
        "artifact_refs": _strings(payload.get("artifact_refs") or report.get("artifact_refs")),
        "evidence_refs": _strings(payload.get("evidence_refs") or report.get("evidence_refs")),
        "findings": _objects(payload.get("findings") or report.get("findings")),
    }
    issues = [
        {"field": f"control_result.{field}", "code": "missing_required"}
        for field in (
            "workflow_run_id",
            "fanout_id",
            "plan_revision",
            "plan_synth_contract_ref",
            "plan_synth_contract_digest",
        )
        if not str(result.get(field) or "").strip()
    ]
    return result, issues


def _normalize_verification(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = event.payload if isinstance(event.payload, dict) else {}
    raw = source.get("verification_result")
    result = dict(raw) if isinstance(raw, Mapping) else _legacy_verification_result(event)
    result.setdefault("schema_version", "verification-result.v1")
    issues: list[dict[str, str]] = []
    try:
        validate_verification_result(result, strict=False)
    except VerificationResultError as exc:
        issues.append({"field": "control_result", "code": "schema_invalid", "message": str(exc)})
    return result, issues


def _legacy_verification_result(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    report = payload.get("report") if isinstance(payload.get("report"), Mapping) else {}
    status = str(payload.get("status") or report.get("status") or "").lower()
    recommendation = str(
        payload.get("recommendation")
        or report.get("recommendation")
        or report.get("verdict")
        or ""
    ).lower()
    failed_execution = event.type.endswith(".failed") and not report
    if failed_execution:
        execution_status = "failed"
        verdict = "abstained"
        failure_class = "verifier_execution_failure"
    else:
        execution_status = "completed"
        if recommendation in {"reject", "rejected", "needs_rework"} or status == "rejected":
            verdict = "rejected"
            failure_class = "product_rejection"
        elif recommendation in {"block", "blocked"} or status == "blocked":
            verdict = "blocked"
            failure_class = "dependency_blocked"
        else:
            verdict = "passed"
            failure_class = "none"
    matrix = report.get("requirement_coverage_matrix")
    if not isinstance(matrix, list):
        matrix = payload.get("requirement_coverage_matrix")
    requirements: list[dict[str, Any]] = []
    for index, item in enumerate(matrix if isinstance(matrix, list) else []):
        if not isinstance(item, Mapping):
            continue
        item_status = str(item.get("status") or "passed").lower()
        if item_status in {"covered", "complete", "approved"}:
            item_status = "passed"
        elif item_status in {"rejected", "failure"}:
            item_status = "failed"
        requirements.append({
            "acceptance_id": str(
                item.get("acceptance_id")
                or item.get("requirement_id")
                or f"legacy-{index + 1}"
            ),
            "status": item_status,
            "verification_owner": str(item.get("verification_owner") or "task_verify"),
            "verification_tier": str(item.get("verification_tier") or "runtime"),
            "evidence_refs": _strings(item.get("evidence_refs")),
            "findings": _objects(item.get("findings")),
            "reproduction_commands": _strings(item.get("reproduction_commands")),
        })
    result = {
        "schema_version": "verification-result.v1",
        "execution_status": execution_status,
        "verdict": verdict,
        "failure_class": failure_class,
        "workflow_run_id": _text(payload, "workflow_run_id", "trace_id"),
        "task_id": str(event.task_id or _text(payload, "task_id", "upstream_task_id")),
        "contract_revision": _text(payload, "contract_revision"),
        "task_map_generation": _text(payload, "task_map_generation"),
        "base_commit": _text(payload, "base_commit"),
        "task_ref": _text(payload, "task_ref"),
        "contract_snapshot_ref": _text(payload, "contract_snapshot_ref"),
        "contract_snapshot_digest": _text(payload, "contract_snapshot_digest"),
        "target_snapshot_ref": _text(payload, "target_snapshot_ref"),
        "target_commit": _text(payload, "target_commit", "candidate_head_commit"),
        "target_snapshot_digest": _text(payload, "target_snapshot_digest"),
        "verification_owner": _text(payload, "verification_owner") or "task_verify",
        "verification_tier": _text(payload, "verification_tier") or "runtime",
        "summary": str(report.get("summary") or payload.get("summary") or payload.get("reason") or ""),
        "findings": _objects(report.get("findings") or payload.get("findings")),
        "evidence_refs": _strings(report.get("evidence_refs") or payload.get("evidence_refs")),
        "reproduction_commands": _strings(
            report.get("reproduction_commands") or payload.get("reproduction_commands")
        ),
        "requirement_results": requirements,
    }
    return result


def _normalize_implementation(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw = payload.get("implementation_result")
    result = dict(raw) if isinstance(raw, Mapping) else {
        "schema_version": IMPLEMENTATION_RESULT_SCHEMA,
        "workflow_run_id": _text(payload, "workflow_run_id", "trace_id"),
        "task_id": str(event.task_id or _text(payload, "task_id")),
        "task_ref": _text(payload, "task_ref"),
        "target_commit": _text(
            payload,
            "target_commit",
            "source_commit",
            "candidate_head_commit",
        ),
        "changed_files": _strings(payload.get("changed_files") or payload.get("files_touched")),
        "evidence_refs": _strings(payload.get("evidence_refs") or payload.get("artifact_refs")),
        "self_check": payload.get("self_check") if isinstance(payload.get("self_check"), Mapping) else {},
        "known_gaps": _strings(payload.get("known_gaps") or payload.get("residual_risks")),
        "summary": str(payload.get("summary") or ""),
        "source_event_id": event.id,
    }
    result.setdefault("schema_version", IMPLEMENTATION_RESULT_SCHEMA)
    result.setdefault("workflow_run_id", _text(payload, "workflow_run_id", "trace_id"))
    result.setdefault("task_id", str(event.task_id or _text(payload, "task_id")))
    result.setdefault("target_commit", _text(payload, "target_commit", "source_commit"))
    result.setdefault("source_event_id", event.id)
    issues = [
        {"field": f"control_result.{field}", "code": "missing_required"}
        for field in ("task_id", "target_commit")
        if not str(result.get(field) or "").strip()
    ]
    return result, issues


def _normalize_fanout_aggregate(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Reference the admitted child results without re-synthesizing them.

    The fanout reducer has already made the aggregate decision. This adapter
    only turns that settled mechanical decision into a typed call return for a
    selected nested workflow operation; it does not reinterpret child
    findings, acceptance criteria, or product evidence.
    """

    payload = event.payload if isinstance(event.payload, dict) else {}
    status = str(payload.get("status") or "").strip().lower()
    if status == "completed":
        verdict = "passed"
    elif status in {"blocked", "timed_out", "cancelled"}:
        verdict = "blocked"
    else:
        verdict = "rejected"
    child_refs = [
        dict(item)
        for item in payload.get("child_call_result_refs", [])
        if isinstance(item, Mapping)
        and str(item.get("ref") or "").strip()
        and str(item.get("sha256") or "").strip()
    ]
    result = {
        "schema_version": FANOUT_AGGREGATE_RESULT_SCHEMA,
        "execution_status": "completed",
        "verdict": verdict,
        "failure_class": str(payload.get("failure_kind") or "none"),
        "workflow_run_id": _text(payload, "workflow_run_id", "trace_id"),
        "operation_id": _text(payload, "workflow_operation_id", "operation_id"),
        "fanout_id": _text(payload, "fanout_id"),
        "stage_id": _text(payload, "stage_id"),
        "success_event": _text(payload, "success_event"),
        "failure_event": _text(payload, "failure_event"),
        "child_call_result_refs": child_refs,
        "source_event_id": event.id,
    }
    issues = [
        {"field": f"control_result.{field}", "code": "missing_required"}
        for field in ("workflow_run_id", "operation_id", "fanout_id", "stage_id")
        if not str(result.get(field) or "").strip()
    ]
    if status not in {"completed", "failed", "blocked", "timed_out", "cancelled"}:
        issues.append({
            "field": "control_result.status",
            "code": "enum_mismatch",
            "message": f"unsupported fanout aggregate status {status!r}",
        })
    if not child_refs:
        issues.append({
            "field": "control_result.child_call_result_refs",
            "code": "missing_required",
            "message": "durable fanout aggregate has no admitted child result refs",
        })
    return result, issues


def _text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _strings(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _objects(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


__all__ = [
    "FANOUT_AGGREGATE_RESULT_SCHEMA",
    "IMPLEMENTATION_RESULT_SCHEMA",
    "AdaptedControlResult",
    "CallResultProfile",
    "ControlResultAdapter",
    "ControlResultAdapterError",
    "ControlResultAdapterRegistry",
    "call_result_profile_identity",
    "default_call_result_profiles",
    "default_control_result_adapters",
    "hydrate_profiled_control_result_event",
    "is_supported_call_result_event",
]
