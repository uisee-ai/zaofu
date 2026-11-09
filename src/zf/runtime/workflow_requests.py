"""Durable workflow request state built from versioned requirement artifacts.

EventLog records transitions. ``workflow-requests/*.json`` is a rebuildable
read projection used by CLI/Web before a Run exists.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.runtime.workflow_synthesis_recovery import bind_workflow_synthesis_operation  # noqa: F401 — public API
from zf.core.events import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.safety.path_guard import PathGuard, PathGuardError
from zf.core.state.atomic_io import atomic_write_text
from zf.core.workflow.request_policy import missing_fields_for_kind, normalize_workflow_kind
from zf.runtime.workflow_origin import (
    WorkflowOriginError,
    assert_same_workflow_origin,
    workflow_origin_from_manifest,
    workflow_origin_from_request,
)
from zf.runtime.workflow_request_concurrency import (
    WorkflowRequestConflict,
    WorkflowRequestError,
    check_workflow_request_preconditions,
)
from zf.runtime.workflow_request_io import (
    now_iso as _now_iso,
    read_json as _read_json,
    safe_id as _safe_id,
    strings as _strings,
)
from zf.runtime.workflow_requirement_specs import (
    build_requirement_spec,
    merge_clarification_answers,
    normalize_clarification_answers,
    normalize_requirement_spec,
)

_REQUEST_STATUSES = {
    "draft",
    "clarifying",
    "ready",
    "proposed",
    "approved",
    "submitted",
    "running",
    "rejected",
}
_REQUEST_TRANSITIONS = {
    "draft": {"proposed"},
    "ready": {"proposed"},
    "proposed": {"approved", "rejected"},
    "approved": {"submitted"},
    "submitted": {"running"},
    "running": set(),
    "clarifying": set(),
    "rejected": set(),
}


def workflow_request_path(state_dir: Path, request_id: str) -> Path:
    return Path(state_dir) / "workflow-requests" / f"{_safe_id(request_id)}.json"


def load_workflow_request(state_dir: Path, request_id: str) -> dict[str, Any]:
    path = workflow_request_path(state_dir, request_id)
    if not path.exists():
        return {}
    return _read_json(path)


def require_current_workflow_request(
    state_dir: Path,
    request_id: str,
    request_revision: int,
) -> dict[str, Any]:
    projection = load_workflow_request(state_dir, request_id)
    if not projection:
        raise WorkflowRequestError(f"workflow request not found: {request_id}")
    current_revision = int(projection.get("revision") or 0)
    if int(request_revision or 0) != current_revision:
        raise WorkflowRequestError(
            "stale workflow request revision: "
            f"expected {request_revision}, current {current_revision}"
        )
    try:
        projection = dict(projection)
        projection["origin_binding"] = workflow_origin_from_request(
            projection
        )
    except WorkflowOriginError as exc:
        raise WorkflowRequestError(str(exc)) from exc
    return projection


def hydrate_workflow_requirement(
    state_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Read one current immutable Requirement and verify its identity."""

    ref = str(request.get("requirement_spec_ref") or "").strip()
    expected_digest = str(
        request.get("requirement_spec_digest") or ""
    ).strip()
    if not ref or not expected_digest:
        raise WorkflowRequestError(
            "workflow request has no immutable requirement identity"
        )
    state_root = Path(state_dir).expanduser().resolve()
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = state_root / path
    if path.is_symlink():
        raise WorkflowRequestError("workflow requirement ref is a symlink")
    try:
        path = PathGuard.assert_under(
            path,
            state_root / "workflow-requests",
        ).resolve(strict=True)
    except (OSError, PathGuardError) as exc:
        raise WorkflowRequestError(
            "workflow requirement ref is outside canonical request state"
        ) from exc
    try:
        raw = path.read_bytes()
        requirement = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowRequestError(
            "workflow requirement body is unreadable"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise WorkflowRequestError("workflow requirement digest mismatch")
    if not isinstance(requirement, dict):
        raise WorkflowRequestError("workflow requirement body must be an object")
    if (
        str(requirement.get("request_id") or "")
        != str(request.get("request_id") or "")
        or int(requirement.get("revision") or 0)
        != int(request.get("revision") or 0)
    ):
        raise WorkflowRequestError(
            "workflow requirement does not match the request revision"
        )
    return requirement


def adopt_workflow_research_result(
    state_dir: Path,
    request_id: str,
    *,
    expected_revision: int,
    artifact_ref: str,
    artifact_digest: str,
    summary: str,
    actor: str,
    source_event_id: str,
    result_event_id: str,
    task_id: str,
    workflow_run_id: str,
    terminal_event_id: str,
    writer: EventWriter | None = None,
) -> tuple[dict[str, Any], bool]:
    """Bind verified research to the current request revision.

    Requirement revision is not changed: adoption enriches the request context
    projection and fails if the caller observed an older requirement revision.
    """
    projection = require_current_workflow_request(
        state_dir,
        request_id,
        expected_revision,
    )
    current_revision = int(projection.get("revision") or 0)
    digest = str(artifact_digest or "").removeprefix("sha256:").strip().lower()
    if not artifact_ref or len(digest) != 64 or not summary:
        raise WorkflowRequestError(
            "research adoption requires artifact_ref, 64-char artifact_digest, and summary"
        )
    adoptions = [
        dict(item)
        for item in projection.get("research_artifacts") or []
        if isinstance(item, dict)
    ]
    existing = next(
        (
            item
            for item in adoptions
            if str(item.get("sha256") or "").lower() == digest
            and int(item.get("request_revision") or 0) == current_revision
        ),
        None,
    )
    if existing is not None:
        return projection, False
    origin_binding = workflow_origin_from_request(projection)
    channel_id = (
        str(origin_binding.get("channel_id") or "")
        if origin_binding.get("surface") == "channel"
        else ""
    )
    thread_id = (
        str(origin_binding.get("thread_id") or "main")
        if channel_id
        else ""
    )
    adoption = {
        "artifact_ref": artifact_ref,
        "sha256": digest,
        "summary": summary,
        "request_revision": current_revision,
        "source_event_id": source_event_id,
        "result_event_id": result_event_id,
        "task_id": task_id,
        "workflow_run_id": workflow_run_id,
        "terminal_event_id": terminal_event_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "origin_binding": origin_binding,
        "adopted_by": actor,
        "adopted_at": _now_iso(),
    }
    projection = dict(projection)
    projection["research_artifacts"] = [*adoptions, adoption]
    projection["updated_at"] = _now_iso()
    _write_projection(state_dir, projection)
    if writer is not None:
        writer.emit(
            "workflow.research.adopted",
            actor=actor,
            task_id=task_id or None,
            causation_id=source_event_id or None,
            correlation_id=(
                adoption["channel_id"]
                or str(origin_binding.get("conversation_id") or "")
                or request_id
            ),
            payload={
                "request_id": request_id,
                "request_revision": current_revision,
                "artifact_ref": artifact_ref,
                "artifact_digest": digest,
                "summary": summary,
                "channel_id": adoption["channel_id"],
                "thread_id": adoption["thread_id"],
                "project_id": str(origin_binding.get("project_id") or ""),
                "origin_surface": str(origin_binding.get("surface") or ""),
                "conversation_id": str(
                    origin_binding.get("conversation_id") or ""
                ),
                "thread_key": str(origin_binding.get("thread_key") or ""),
                "origin_binding": origin_binding,
                "result_event_id": result_event_id,
                "task_id": task_id,
                "workflow_run_id": workflow_run_id,
                "terminal_event_id": terminal_event_id,
                "source_event_id": source_event_id,
            },
        )
    return projection, True


def register_workflow_intake(
    state_dir: Path,
    manifest_path: Path,
    *,
    actor: str,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path).expanduser().resolve()
    manifest = _read_json(manifest_path)
    request_id = str(manifest.get("request_id") or "").strip()
    if not request_id:
        raise WorkflowRequestError("workflow input manifest requires request_id")
    existing = load_workflow_request(state_dir, request_id)
    if existing:
        incoming_origin = _manifest_origin_binding(manifest)
        try:
            existing_origin = workflow_origin_from_request(existing)
            assert_same_workflow_origin(existing_origin, incoming_origin)
        except WorkflowOriginError as exc:
            raise WorkflowRequestError(str(exc)) from exc
        current = _read_json(Path(str(existing.get("requirement_spec_ref") or "")))
        if not current:
            raise WorkflowRequestError("current requirement spec is missing")
        spec_ref, digest = _write_requirement_spec(state_dir, current)
        projection = dict(existing)
        projection["origin_binding"] = existing_origin
        projection["requirement_spec_ref"] = spec_ref
        projection["requirement_spec_digest"] = digest
        _bind_effective_manifest(
            state_dir,
            manifest_path=manifest_path,
            source_manifest=manifest,
            projection=projection,
            spec=current,
        )
        _write_projection(state_dir, projection)
        return projection

    intake = _read_json(Path(str(manifest.get("intake_json_ref") or "")))
    spec = build_requirement_spec(
        manifest,
        intake,
        revision=1,
        confirmed=False,
    )
    spec_ref, digest = _write_requirement_spec(state_dir, spec)
    projection = _projection(
        manifest,
        spec,
        spec_ref=spec_ref,
        digest=digest,
        prior={},
    )
    _bind_effective_manifest(
        state_dir,
        manifest_path=manifest_path,
        source_manifest=manifest,
        projection=projection,
        spec=spec,
    )
    _write_projection(state_dir, projection)
    _emit(
        writer,
        "workflow.intake.created",
        projection,
        actor=actor,
        extra={
            "workflow_input_manifest_ref": str(
                projection.get("workflow_input_manifest_ref") or ""
            ),
            "source_workflow_input_manifest_ref": str(manifest_path),
        },
    )
    if projection["status"] == "clarifying":
        _emit(
            writer,
            "workflow.intake.clarification.required",
            projection,
            actor=actor,
        )
    return projection


def revise_workflow_request(
    state_dir: Path,
    manifest_path: Path,
    *,
    actor: str,
    kind: str | None = None,
    objective: str | None = None,
    source_root: str | None = None,
    target_root: str | None = None,
    acceptance: list[str] | None = None,
    constraints: list[str] | None = None,
    open_questions: list[str] | None = None,
    clarification_answers: list[dict[str, str]] | None = None,
    confirm: bool = False,
    expected_revision: int | None = None,
    expected_requirement_digest: str = "",
    revision_reason: str = "requirement_update",
    source_event_id: str = "",
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    revision_reason = str(revision_reason or "requirement_update").strip().lower()
    if revision_reason not in {
        "clarification",
        "requirement_update",
        "semantic_replan",
    }:
        raise WorkflowRequestError(
            f"unsupported workflow request revision reason: {revision_reason}"
        )
    manifest_path = Path(manifest_path).expanduser().resolve()
    manifest = _read_json(manifest_path)
    request_id = str(manifest.get("request_id") or "").strip()
    if not request_id:
        raise WorkflowRequestError("workflow input manifest requires request_id")
    prior = load_workflow_request(state_dir, request_id)
    if not prior:
        prior = register_workflow_intake(
            state_dir,
            manifest_path,
            actor=actor,
            writer=writer,
        )
    check_workflow_request_preconditions(
        prior,
        expected_revision=expected_revision,
        expected_requirement_digest=expected_requirement_digest,
    )
    current = _read_json(Path(str(prior.get("requirement_spec_ref") or "")))
    if not current:
        raise WorkflowRequestError("current requirement spec is missing")
    revision = int(prior.get("revision") or 0) + 1
    updates = {
        "kind": normalize_workflow_kind(kind) if kind is not None else None,
        "objective": objective,
        "source_root": source_root,
        "target_root": target_root,
        "acceptance": acceptance,
        "constraints": constraints,
        "open_questions": open_questions,
    }
    spec = dict(current, revision=revision, updated_at=_now_iso())
    for key, value in updates.items():
        if value is not None:
            spec[key] = value
    if clarification_answers is not None:
        spec["clarification_answers"] = merge_clarification_answers(
            spec.get("clarification_answers"),
            clarification_answers,
        )
    if confirm:
        spec["confirmed"] = True
        spec["confirmed_at"] = _now_iso()
        spec["confirmed_by"] = actor
    spec = normalize_requirement_spec(spec)
    spec_ref, digest = _write_requirement_spec(state_dir, spec)
    projection = _projection(
        manifest,
        spec,
        spec_ref=spec_ref,
        digest=digest,
        prior=prior,
    )
    _bind_effective_manifest(
        state_dir,
        manifest_path=manifest_path,
        source_manifest=manifest,
        projection=projection,
        spec=spec,
    )
    _write_projection(state_dir, projection)
    _emit(
        writer,
        "workflow.request.updated",
        projection,
        actor=actor,
        extra={
            "previous_revision": int(prior.get("revision") or 0),
            "revision_reason": revision_reason,
            "source_event_id": str(source_event_id or ""),
            "attempt_domain": (
                "gap" if revision_reason == "semantic_replan" else "plan"
            ),
            "semantic_attempt_incremented": (
                revision_reason == "semantic_replan"
            ),
        },
        causation_id=source_event_id,
    )
    if projection["status"] == "ready" and prior.get("status") != "ready":
        _emit(writer, "workflow.intake.ready", projection, actor=actor)
    elif projection["status"] == "clarifying":
        _emit(
            writer,
            "workflow.intake.clarification.required",
            projection,
            actor=actor,
        )
    return projection


def mark_workflow_request(
    state_dir: Path,
    request_id: str,
    *,
    status: str,
    actor: str,
    writer: EventWriter | None = None,
    run_id: str = "",
    event_type: str = "",
) -> dict[str, Any]:
    projection = load_workflow_request(state_dir, request_id)
    if not projection:
        raise WorkflowRequestError(f"workflow request not found: {request_id}")
    current = str(projection.get("status") or "draft")
    status = str(status or "").strip().lower()
    if status not in _REQUEST_STATUSES:
        raise WorkflowRequestError(f"unsupported workflow request status: {status}")
    if status == current:
        return projection
    allowed = _REQUEST_TRANSITIONS.get(current, set())
    if status not in allowed:
        raise WorkflowRequestError(
            f"invalid workflow request transition: {current} -> {status}"
        )
    projection = dict(projection)
    projection["status"] = status
    projection["updated_at"] = _now_iso()
    if run_id:
        projection["run_id"] = run_id
    _write_projection(state_dir, projection)
    if event_type:
        _emit(writer, event_type, projection, actor=actor)
    return projection


def bind_workflow_proposal(
    state_dir: Path,
    *,
    request_id: str,
    request_revision: int,
    proposal_ref: dict[str, Any],
    proposal_digest: str,
    actor: str,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    projection = load_workflow_request(state_dir, request_id)
    if not projection:
        raise WorkflowRequestError(f"workflow request not found: {request_id}")
    if int(projection.get("revision") or 0) != int(request_revision):
        raise WorkflowRequestError("workflow proposal targets a stale request revision")
    current = str(projection.get("status") or "")
    existing_digest = str(projection.get("proposal_digest") or "")
    if current in {"proposed", "approved", "submitted", "running"} and existing_digest:
        if existing_digest != proposal_digest:
            raise WorkflowRequestError(
                "current workflow request already has a different proposal"
            )
        return projection
    if current not in {"draft", "ready"}:
        raise WorkflowRequestError(
            f"workflow request is not proposal-ready: {current}"
        )
    projection = dict(projection)
    projection.update({
        "status": "proposed",
        "proposal_ref": dict(proposal_ref),
        "proposal_digest": str(proposal_digest),
        "proposal_revision": int(request_revision),
        "updated_at": _now_iso(),
    })
    _write_projection(state_dir, projection)
    _emit(
        writer,
        "workflow.request.proposed",
        projection,
        actor=actor,
        extra={
            "proposal_ref": dict(proposal_ref),
            "proposal_digest": str(proposal_digest),
            "proposal_revision": int(request_revision),
        },
    )
    return projection


def bind_workflow_synthesis_result(
    state_dir: Path,
    *,
    request_id: str,
    request_revision: int,
    requirement_digest: str,
    synthesis_ref: dict[str, Any],
    synthesis_digest: str,
    selected_flow_family: str,
    open_questions: list[str],
    actor: str,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    """Bind one admitted synthesis result to the current request revision."""

    projection = load_workflow_request(state_dir, request_id)
    if not projection:
        raise WorkflowRequestError(f"workflow request not found: {request_id}")
    if int(projection.get("revision") or 0) != int(request_revision):
        raise WorkflowRequestError(
            "workflow synthesis targets a stale request revision"
        )
    if str(projection.get("requirement_spec_digest") or "") != str(
        requirement_digest
    ):
        raise WorkflowRequestError(
            "workflow synthesis requirement digest is stale"
        )
    current = str(projection.get("status") or "")
    if current not in {"ready", "clarifying"}:
        raise WorkflowRequestError(
            f"workflow request is not synthesis-ready: {current}"
        )
    questions = [str(item).strip() for item in open_questions if str(item).strip()]
    from zf.runtime.workflow_prepare import synthesis_open_questions
    questions = synthesis_open_questions(state_dir, projection, selected_flow_family, questions)
    projection.update({
        "status": "clarifying" if questions else "ready",
        "open_questions": questions,
        "synthesis_ref": dict(synthesis_ref),
        "synthesis_digest": str(synthesis_digest),
        "synthesis_revision": int(request_revision),
        "selected_flow_family": str(selected_flow_family),
        "updated_at": _now_iso(),
    })
    _write_projection(state_dir, projection)
    _emit(
        writer,
        (
            "workflow.synthesis.clarification.required"
            if questions
            else "workflow.synthesis.admitted"
        ),
        projection,
        actor=actor,
        extra={
            "synthesis_ref": dict(synthesis_ref),
            "synthesis_digest": str(synthesis_digest),
            "synthesis_revision": int(request_revision),
            "selected_flow_family": str(selected_flow_family),
        },
    )
    return projection


def validate_current_workflow_proposal(
    state_dir: Path,
    *,
    request_id: str,
    proposal_ref: dict[str, Any],
    proposal_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate an operator decision against the exact current Proposal."""

    projection = load_workflow_request(state_dir, request_id)
    if not projection:
        raise WorkflowRequestError(f"workflow request not found: {request_id}")
    if str(projection.get("status") or "") not in {
        "proposed",
        "approved",
        "submitted",
        "running",
    }:
        raise WorkflowRequestError(
            "workflow request has no current decidable proposal"
        )
    current_ref = projection.get("proposal_ref")
    if not isinstance(current_ref, dict):
        raise WorkflowRequestError("workflow request proposal ref is missing")
    if (
        str(current_ref.get("ref") or "") != str(proposal_ref.get("ref") or "")
        or str(current_ref.get("sha256") or "")
        != str(proposal_ref.get("sha256") or "")
    ):
        raise WorkflowRequestError("workflow proposal ref is stale")
    current_digest = str(projection.get("proposal_digest") or "")
    if not proposal_digest or proposal_digest != current_digest:
        raise WorkflowRequestError("workflow proposal digest is stale")
    from zf.runtime.workflow_proposal import load_workflow_proposal

    proposal = load_workflow_proposal(state_dir, proposal_ref)
    if (
        str(proposal.get("proposal_digest") or "") != proposal_digest
        or str(proposal.get("request_id") or "") != request_id
        or int(proposal.get("request_revision") or 0)
        != int(projection.get("revision") or 0)
    ):
        raise WorkflowRequestError(
            "workflow proposal does not bind the current request revision"
        )
    return projection, proposal


def reject_workflow_proposal(
    state_dir: Path,
    *,
    request_id: str,
    proposal_ref: dict[str, Any],
    proposal_digest: str,
    reason: str,
    actor: str,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    projection, _proposal = validate_current_workflow_proposal(
        state_dir,
        request_id=request_id,
        proposal_ref=proposal_ref,
        proposal_digest=proposal_digest,
    )
    if str(projection.get("status") or "") != "proposed":
        raise WorkflowRequestError(
            "only a proposed workflow request can be rejected"
        )
    rejected = mark_workflow_request(
        state_dir,
        request_id,
        status="rejected",
        actor=actor,
        writer=writer,
        event_type="workflow.request.rejected",
    )
    rejected = dict(rejected)
    rejected["rejection_reason"] = str(reason or "operator rejected proposal")
    _write_projection(state_dir, rejected)
    return rejected


def request_readiness_blockers(projection: dict[str, Any]) -> list[dict[str, Any]]:
    if not projection:
        return [{
            "severity": "STOP",
            "kind": "workflow_request_projection_missing",
            "title": "workflow request projection 缺失",
            "message": "intake 尚未进入统一 Workflow Request 状态机。",
            "fix_it": "重新执行 workflow intake/classify 后再提交。",
            "safe_auto_fix": True,
        }]
    blockers: list[dict[str, Any]] = []
    missing = [str(item) for item in projection.get("missing_required_fields") or []]
    questions = [str(item) for item in projection.get("open_questions") or []]
    if missing:
        blockers.append({
            "severity": "STOP",
            "kind": "workflow_request_required_fields_missing",
            "title": "需求字段尚未补齐",
            "message": ", ".join(missing),
            "fix_it": "通过 CLI/Kanban/Channel 补齐字段并重新确认需求。",
            "safe_auto_fix": False,
        })
    if questions:
        blockers.append({
            "severity": "STOP",
            "kind": "workflow_request_open_questions",
            "title": "需求仍有未决问题",
            "message": "; ".join(questions[:8]),
            "fix_it": "先解决 open questions，再确认并点火。",
            "safe_auto_fix": False,
        })
    return blockers


def _projection(
    manifest: dict[str, Any],
    spec: dict[str, Any],
    *,
    spec_ref: str,
    digest: str,
    prior: dict[str, Any],
) -> dict[str, Any]:
    missing = missing_fields_for_kind(
        str(spec.get("kind") or "issue"),
        objective=str(spec.get("objective") or ""),
        source_ref=str(spec.get("source_ref") or ""),
        source_root=str(spec.get("source_root") or ""),
        target_root=str(spec.get("target_root") or ""),
    )
    questions = _strings(spec.get("open_questions"))
    confirmed = bool(spec.get("confirmed"))
    status = "clarifying" if missing or questions else "ready" if confirmed else "draft"
    origin_binding = (
        dict(prior.get("origin_binding"))
        if isinstance(prior.get("origin_binding"), dict)
        else _manifest_origin_binding(manifest)
    )
    return {
        "schema_version": "workflow.request.v1",
        "request_id": str(spec.get("request_id") or ""),
        "project_id": str(spec.get("project_id") or ""),
        "kind": str(spec.get("kind") or ""),
        "source": str(manifest.get("source") or prior.get("source") or ""),
        "channel_id": str(manifest.get("channel_id") or prior.get("channel_id") or ""),
        "thread_id": str(manifest.get("thread_id") or prior.get("thread_id") or ""),
        "origin_binding": origin_binding,
        "status": status,
        "revision": int(spec.get("revision") or 1),
        "requirement_spec_ref": spec_ref,
        "requirement_spec_digest": digest,
        "workflow_input_manifest_ref": str(
            prior.get("workflow_input_manifest_ref")
            or manifest.get("workflow_input_manifest_ref")
            or ""
        ),
        "missing_required_fields": missing,
        "open_questions": questions,
        "clarification_answer_count": len(
            normalize_clarification_answers(spec.get("clarification_answers"))
        ),
        "confirmed": confirmed,
        "run_id": str(prior.get("run_id") or ""),
        "created_at": str(prior.get("created_at") or manifest.get("created_at") or _now_iso()),
        "updated_at": _now_iso(),
    }


def _write_requirement_spec(
    state_dir: Path,
    spec: dict[str, Any],
) -> tuple[str, str]:
    request_id = str(spec.get("request_id") or "").strip()
    if not request_id:
        raise WorkflowRequestError("requirement spec requires request_id")
    revision = int(spec.get("revision") or 1)
    text = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = (
        Path(state_dir)
        / "workflow-requests"
        / _safe_id(request_id)
        / "requirements"
        / f"revision-{revision:04d}-{digest[:16]}.json"
    )
    atomic_write_text(path, text)
    return str(path), digest


def _bind_effective_manifest(
    state_dir: Path,
    *,
    manifest_path: Path,
    source_manifest: dict[str, Any],
    projection: dict[str, Any],
    spec: dict[str, Any],
) -> None:
    source_text = manifest_path.read_text(encoding="utf-8")
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    manifest = dict(source_manifest)
    manifest.update({
        "objective": str(spec.get("objective") or ""),
        "source_root": str(spec.get("source_root") or ""),
        "target_root": str(spec.get("target_root") or ""),
        "scope": _strings(spec.get("scope")),
        "acceptance": _strings(spec.get("acceptance")),
        "constraints": _strings(spec.get("constraints")),
        "open_questions": _strings(spec.get("open_questions")),
        "clarification_answers": normalize_clarification_answers(
            spec.get("clarification_answers")
        ),
        "missing_required_fields": list(projection.get("missing_required_fields") or []),
        "requirement_spec_ref": projection["requirement_spec_ref"],
        "requirement_spec_digest": projection["requirement_spec_digest"],
        "request_status": projection["status"],
        "request_revision": projection["revision"],
        "origin_binding": dict(projection.get("origin_binding") or {}),
        "source_workflow_input_manifest_ref": str(manifest_path),
        "source_workflow_input_manifest_digest": source_digest,
    })
    refs = [
        dict(item) if isinstance(item, dict) else str(item)
        for item in manifest.get("artifact_refs") or []
        if item not in (None, "", {})
    ]
    ref_values = {
        str(item.get("ref") or item.get("path") or item.get("uri") or "")
        if isinstance(item, dict)
        else str(item)
        for item in refs
    }
    if str(manifest_path) not in ref_values:
        refs.append(str(manifest_path))
    if projection["requirement_spec_ref"] not in ref_values:
        refs.append(projection["requirement_spec_ref"])
    manifest["artifact_refs"] = refs
    text = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    effective_path = (
        Path(state_dir)
        / "workflow-requests"
        / _safe_id(str(projection.get("request_id") or "request"))
        / "effective"
        / (
            f"revision-{int(projection.get('revision') or 1):04d}-"
            f"{digest[:16]}"
        )
        / "workflow-input-manifest.json"
    )
    atomic_write_text(
        effective_path,
        text,
    )
    projection["workflow_input_manifest_ref"] = str(effective_path)
    projection["workflow_input_manifest_digest"] = digest
    projection["source_workflow_input_manifest_ref"] = str(manifest_path)
    projection["source_workflow_input_manifest_digest"] = source_digest


def _write_projection(state_dir: Path, projection: dict[str, Any]) -> None:
    atomic_write_text(
        workflow_request_path(state_dir, str(projection.get("request_id") or "")),
        json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
    )


def _emit(
    writer: EventWriter | None,
    event_type: str,
    projection: dict[str, Any],
    *,
    actor: str,
    extra: dict[str, Any] | None = None,
    causation_id: str = "",
) -> None:
    if writer is None:
        return
    payload = {
        "request_id": str(projection.get("request_id") or ""),
        "project_id": str(projection.get("project_id") or ""),
        "kind": str(projection.get("kind") or ""),
        "status": str(projection.get("status") or ""),
        "revision": int(projection.get("revision") or 1),
        "requirement_spec_ref": str(projection.get("requirement_spec_ref") or ""),
        "requirement_spec_digest": str(projection.get("requirement_spec_digest") or ""),
        "missing_required_fields": list(projection.get("missing_required_fields") or []),
        "open_questions": list(projection.get("open_questions") or []),
        "clarification_answer_count": int(
            projection.get("clarification_answer_count") or 0
        ),
        "origin_binding": dict(projection.get("origin_binding") or {}),
        **(extra or {}),
    }
    writer.append(ZfEvent(
        type=event_type,
        actor=actor,
        task_id="",
        causation_id=causation_id or None,
        correlation_id=str(projection.get("request_id") or ""),
        payload=payload,
    ))


def _manifest_origin_binding(manifest: dict[str, Any]) -> dict[str, str]:
    try:
        return workflow_origin_from_manifest(manifest)
    except WorkflowOriginError as exc:
        raise WorkflowRequestError(str(exc)) from exc


__all__ = [
    "WorkflowRequestConflict",
    "WorkflowRequestError",
    "adopt_workflow_research_result",
    "bind_workflow_synthesis_operation",
    "bind_workflow_synthesis_result",
    "hydrate_workflow_requirement",
    "load_workflow_request",
    "mark_workflow_request",
    "register_workflow_intake",
    "request_readiness_blockers",
    "revise_workflow_request",
    "workflow_request_path",
]
