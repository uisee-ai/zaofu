"""Legacy role-event adapters for typed call control results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.verification_result import (
    VerificationResultError,
    validate_verification_result,
)
from zf.runtime.goal_closure_result import (
    GoalClosureResultError,
    SCHEMA_VERSION as GOAL_CLOSURE_RESULT_SCHEMA,
    normalize_goal_closure_result,
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


class ControlResultAdapterRegistry:
    def __init__(self, adapters: list[ControlResultAdapter] | None = None) -> None:
        self._adapters = list(adapters or default_control_result_adapters())

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


def is_supported_call_result_event(event: ZfEvent) -> bool:
    return ControlResultAdapterRegistry().adapter_for(event) is not None


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
    return event.type in {"dev.build.done", "task.ref.updated"}


def _is_selected_fanout_aggregate(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return (
        event.type == "fanout.aggregate.completed"
        and bool(payload.get("workflow_operation_id") or payload.get("operation_id"))
        and bool(payload.get("workflow_operation_request_hash") or payload.get("request_hash"))
    )


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
    result = {
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
    "ControlResultAdapter",
    "ControlResultAdapterError",
    "ControlResultAdapterRegistry",
    "default_control_result_adapters",
    "is_supported_call_result_event",
]
