"""Durable workflow request state built from versioned requirement artifacts.

EventLog records transitions. ``workflow-requests/*.json`` is a rebuildable
read projection used by CLI/Web before a Run exists.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.workflow.request_policy import missing_fields_for_kind


class WorkflowRequestError(ValueError):
    pass


_REQUEST_STATUSES = {
    "draft",
    "clarifying",
    "ready",
    "proposed",
    "approved",
    "submitted",
    "running",
}
_REQUEST_TRANSITIONS = {
    "draft": {"proposed"},
    "ready": {"proposed"},
    "proposed": {"approved"},
    "approved": {"submitted"},
    "submitted": {"running"},
    "running": set(),
    "clarifying": set(),
}


def workflow_request_path(state_dir: Path, request_id: str) -> Path:
    return Path(state_dir) / "workflow-requests" / f"{_safe_id(request_id)}.json"


def load_workflow_request(state_dir: Path, request_id: str) -> dict[str, Any]:
    path = workflow_request_path(state_dir, request_id)
    if not path.exists():
        return {}
    return _read_json(path)


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
        current = _read_json(Path(str(existing.get("requirement_spec_ref") or "")))
        if not current:
            raise WorkflowRequestError("current requirement spec is missing")
        spec_ref, digest = _write_requirement_spec(state_dir, current)
        projection = dict(existing)
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
    spec = _requirement_spec(
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
    objective: str | None = None,
    source_root: str | None = None,
    target_root: str | None = None,
    acceptance: list[str] | None = None,
    constraints: list[str] | None = None,
    open_questions: list[str] | None = None,
    confirm: bool = False,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
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
    current = _read_json(Path(str(prior.get("requirement_spec_ref") or "")))
    if not current:
        raise WorkflowRequestError("current requirement spec is missing")
    revision = int(prior.get("revision") or 0) + 1
    updates = {
        "objective": objective,
        "source_root": source_root,
        "target_root": target_root,
        "acceptance": acceptance,
        "constraints": constraints,
        "open_questions": open_questions,
    }
    spec = dict(current)
    spec["revision"] = revision
    spec["updated_at"] = _now_iso()
    for key, value in updates.items():
        if value is not None:
            spec[key] = value
    if confirm:
        spec["confirmed"] = True
        spec["confirmed_at"] = _now_iso()
        spec["confirmed_by"] = actor
    spec = _normalize_spec(spec)
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
        extra={"previous_revision": int(prior.get("revision") or 0)},
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


def _requirement_spec(
    manifest: dict[str, Any],
    intake: dict[str, Any],
    *,
    revision: int,
    confirmed: bool,
) -> dict[str, Any]:
    return _normalize_spec({
        "schema_version": "requirement-spec.v1",
        "request_id": str(manifest.get("request_id") or ""),
        "project_id": str(manifest.get("project_id") or ""),
        "kind": str(manifest.get("kind") or intake.get("effective_kind") or "issue"),
        "revision": revision,
        "objective": str(manifest.get("objective") or intake.get("objective") or ""),
        "source_ref": str(manifest.get("source_ref") or ""),
        "source_root": str(manifest.get("source_root") or intake.get("source_root") or ""),
        "target_root": str(manifest.get("target_root") or intake.get("target_root") or ""),
        "acceptance": _strings(intake.get("acceptance") or manifest.get("acceptance")),
        "constraints": _strings(intake.get("constraints") or manifest.get("constraints")),
        "open_questions": _strings(intake.get("open_questions") or manifest.get("open_questions")),
        "confirmed": confirmed,
        "created_at": str(manifest.get("created_at") or _now_iso()),
        "updated_at": _now_iso(),
    })


def _normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = dict(spec)
    for key in ("acceptance", "constraints", "open_questions"):
        out[key] = _strings(out.get(key))
    return out


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
    return {
        "schema_version": "workflow.request.v1",
        "request_id": str(spec.get("request_id") or ""),
        "project_id": str(spec.get("project_id") or ""),
        "kind": str(spec.get("kind") or ""),
        "source": str(manifest.get("source") or prior.get("source") or ""),
        "channel_id": str(manifest.get("channel_id") or prior.get("channel_id") or ""),
        "thread_id": str(manifest.get("thread_id") or prior.get("thread_id") or ""),
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
        "acceptance": _strings(spec.get("acceptance")),
        "constraints": _strings(spec.get("constraints")),
        "open_questions": _strings(spec.get("open_questions")),
        "missing_required_fields": list(projection.get("missing_required_fields") or []),
        "requirement_spec_ref": projection["requirement_spec_ref"],
        "requirement_spec_digest": projection["requirement_spec_digest"],
        "request_status": projection["status"],
        "request_revision": projection["revision"],
        "source_workflow_input_manifest_ref": str(manifest_path),
        "source_workflow_input_manifest_digest": source_digest,
    })
    refs = [str(item) for item in manifest.get("artifact_refs") or []]
    if str(manifest_path) not in refs:
        refs.append(str(manifest_path))
    if projection["requirement_spec_ref"] not in refs:
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
        **(extra or {}),
    }
    writer.append(ZfEvent(
        type=event_type,
        actor=actor,
        task_id="",
        correlation_id=str(projection.get("request_id") or ""),
        payload=payload,
    ))


def _read_json(path: Path) -> dict[str, Any]:
    if not str(path) or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value) or "request"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "WorkflowRequestError",
    "load_workflow_request",
    "mark_workflow_request",
    "register_workflow_intake",
    "request_readiness_blockers",
    "revise_workflow_request",
    "workflow_request_path",
]
