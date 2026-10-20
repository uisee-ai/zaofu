"""Read-only plan preview projection for operator approval cards."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import (
    PLAN_REVIEW_REF_FIELDS,
    preview_artifact_path,
    preview_package_artifacts,
    read_preview_artifact,
)


PLAN_APPROVAL_REQUESTED = "plan.approval.requested"
PLAN_APPROVED = "plan.approved"
PLAN_REJECTED = "plan.rejected"
PLAN_PREVIEW_SCHEMA_VERSION = "operator-plan-preview.v1"


def build_plan_preview(
    state_dir: Path,
    events: Iterable[Any],
    *,
    plan_id: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Return a full-screen-ready markdown preview for one plan approval."""

    events = list(events)
    requested: Any | None = None
    resolved: Any | None = None
    for event in events:
        payload = _payload(event)
        if str(payload.get("plan_id") or "") != plan_id:
            continue
        if _etype(event) == PLAN_APPROVAL_REQUESTED:
            requested = event
            resolved = None
        elif _etype(event) in {PLAN_APPROVED, PLAN_REJECTED}:
            resolved = event
    if requested is None and resolved is None:
        return {
            "schema_version": PLAN_PREVIEW_SCHEMA_VERSION,
            "ok": False,
            "status": "not_found",
            "plan_id": plan_id,
            "reason": "plan_id not found",
        }

    source_event = requested or resolved
    payload = dict(_payload(source_event))
    bound_source = next((event for event in events if _event_id(event) == plan_id
                         and _etype(event) == "task_map.ready"), None)
    if bound_source is not None:
        original = _payload(bound_source)
        if any(payload.get(key) and original.get(key) and payload[key] != original[key]
               for key in ("task_map_ref", "pdd_id", "trace_id", "workflow_run_id")):
            bound_source = None
        else:
            for key in PLAN_REVIEW_REF_FIELDS:
                if not payload.get(key) and original.get(key):
                    payload[key] = original[key]
    resolved_payload = _payload(resolved) if resolved is not None else {}
    status = "pending"
    if resolved is not None:
        status = "approved" if _etype(resolved) == PLAN_APPROVED else "rejected"

    digest_ref = str(payload.get("digest_ref") or "")
    task_map_ref = str(payload.get("task_map_ref") or "")
    plan_ref = str(
        payload.get("plan_artifact_ref")
        or payload.get("plan_ref")
        or payload.get("artifact_ref")
        or ""
    )
    artifacts, artifacts_error = preview_package_artifacts(
        payload, state_dir=state_dir, project_root=project_root,
    )
    plan_port = next((p for p in artifacts if p["name"] == "accepted_plan"), None)
    if not plan_ref and plan_port:
        plan_ref = plan_port["ref"]
        payload["plan_artifact_digest"] = plan_port["sha256"]
    markdown, markdown_ref, markdown_error = _plan_markdown(
        state_dir,
        project_root=project_root,
        refs=[plan_ref] if plan_ref else [digest_ref],
        digest=str(payload.get("plan_artifact_digest") or "") if plan_ref else "",
    )
    content_kind = ("full_plan" if plan_ref else "digest_only") if markdown else "unavailable"
    digest_markdown, _, _ = _plan_markdown(state_dir, project_root=project_root, refs=[digest_ref])
    task_map_summary = _task_map_summary(
        state_dir,
        project_root=project_root,
        task_map_ref=task_map_ref,
        digest=next((p["sha256"] for p in artifacts if p["name"] == "task_map"), ""),
    )
    if not markdown:
        markdown = _fallback_markdown(
            plan_id=plan_id,
            status=status,
            payload=payload,
            task_map_summary=task_map_summary,
            markdown_error=markdown_error,
        )
        markdown_ref = ""

    return {
        "schema_version": PLAN_PREVIEW_SCHEMA_VERSION,
        "ok": True,
        "plan_id": plan_id,
        "status": status,
        "requested_event_id": _event_id(requested) if requested is not None else "",
        "requested_ts": _event_ts(requested) if requested is not None else "",
        "resolved_event_id": _event_id(resolved) if resolved is not None else "",
        "resolved_ts": _event_ts(resolved) if resolved is not None else "",
        "reject_reason": str(resolved_payload.get("reason") or ""),
        "stage_id": str(payload.get("stage_id") or ""),
        "trace_id": str(payload.get("trace_id") or _correlation_id(source_event) or ""),
        "pdd_id": str(payload.get("pdd_id") or ""),
        "task_count": _int_or_none(payload.get("task_count")),
        "refs": {
            "digest_ref": digest_ref,
            "task_map_ref": task_map_ref,
            "plan_ref": plan_ref,
            "markdown_ref": markdown_ref,
            "plan_artifact_digest": str(payload.get("plan_artifact_digest") or ""),
        },
        "markdown": markdown,
        "content_kind": content_kind,
        "content_error": markdown_error,
        "digest_markdown": digest_markdown,
        "source_event_id": _event_id(bound_source) or _event_id(source_event),
        "artifacts": artifacts,
        "artifacts_error": artifacts_error,
        "task_map_summary": task_map_summary,
        "actions": {
            "approve": "plan-approve",
            "reject": "plan-reject",
            "repair_chat": "chat-orchestrator",
        },
        "policy": {
            "mutation_path": "controlled-action",
            "agent_can_propose_plan_approve": False,
            "repair_owner": "orchestrator",
        },
    }


def plan_preview_available(
    state_dir: Path,
    *,
    project_root: Path | None,
    refs: list[str],
) -> bool:
    return _first_existing_ref(state_dir, project_root=project_root, refs=refs) is not None


def _plan_markdown(
    state_dir: Path,
    *,
    project_root: Path | None,
    refs: list[str],
    digest: str = "",
) -> tuple[str, str, str]:
    diagnostics: list[str] = []
    for ref in refs:
        if not ref:
            continue
        try:
            body = read_preview_artifact(ref, state_dir=state_dir, project_root=project_root, digest=digest)
            if body.strip():
                return body, ref, ""
            diagnostics.append(f"{ref}: artifact is empty")
        except (OSError, ValueError) as exc:
            diagnostics.append(f"{ref}: {exc}")
    return "", "", "; ".join(diagnostics)


def _task_map_summary(
    state_dir: Path,
    *,
    project_root: Path | None,
    task_map_ref: str,
    digest: str = "",
) -> dict[str, Any]:
    if not task_map_ref:
        return {"ok": False, "task_count": 0, "tasks": [], "reason": "task_map_ref missing"}
    path = _first_existing_ref(state_dir, project_root=project_root, refs=[task_map_ref])
    if path is None:
        return {"ok": False, "task_count": 0, "tasks": [], "reason": "task_map_ref unreadable"}
    try:
        data = json.loads(read_preview_artifact(
            task_map_ref, state_dir=state_dir, project_root=project_root, digest=digest,
        ))
    except (OSError, ValueError) as exc:
        return {"ok": False, "task_count": 0, "tasks": [], "reason": str(exc)}
    raw_tasks = data.get("tasks") if isinstance(data, dict) else []
    tasks = raw_tasks if isinstance(raw_tasks, list) else []
    rows: list[dict[str, str]] = []
    for task in tasks[:50]:
        if not isinstance(task, dict):
            continue
        rows.append({
            "task_id": str(task.get("task_id") or task.get("id") or ""),
            "title": str(task.get("title") or task.get("summary") or ""),
            "root_owner_class": str(task.get("root_owner_class") or ""),
            "verification": str(task.get("verification") or ""),
        })
    return {
        "ok": True,
        "task_count": len(tasks),
        "tasks": rows,
        "path": str(path),
        "truncated": len(tasks) > len(rows),
    }


def _fallback_markdown(
    *,
    plan_id: str,
    status: str,
    payload: dict[str, Any],
    task_map_summary: dict[str, Any],
    markdown_error: str,
) -> str:
    lines = [
        "# Plan Preview",
        "",
        f"- plan_id: `{plan_id}`",
        f"- status: `{status}`",
        f"- stage_id: `{payload.get('stage_id') or ''}`",
        f"- task_map_ref: `{payload.get('task_map_ref') or ''}`",
    ]
    if markdown_error:
        lines.append(f"- preview_note: `{markdown_error}`")
    lines.extend(["", "## Task Map Summary", ""])
    if task_map_summary.get("ok"):
        lines.append(f"task_count: `{task_map_summary.get('task_count')}`")
        for task in task_map_summary.get("tasks", [])[:20]:
            lines.append(f"- `{task.get('task_id')}` {task.get('title')}")
    else:
        lines.append(str(task_map_summary.get("reason") or "task map unavailable"))
    return "\n".join(lines).rstrip() + "\n"


def _first_existing_ref(
    state_dir: Path,
    *,
    project_root: Path | None,
    refs: list[str],
) -> Path | None:
    for ref in refs:
        if not ref:
            continue
        for path in _candidate_paths(ref, state_dir=state_dir, project_root=project_root):
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
    return None


def _candidate_paths(ref: str, *, state_dir: Path, project_root: Path | None) -> list[Path]:
    try:
        return [preview_artifact_path(ref, project_root=project_root, state_dir=state_dir)]
    except (OSError, ValueError):
        return []


def read_plan_preview_attachment(
    state_dir: Path, events: Iterable[Any], *, plan_id: str, name: str,
    project_root: Path | None = None,
) -> str:
    preview = build_plan_preview(state_dir, events, plan_id=plan_id, project_root=project_root)
    artifact = next((p for p in preview.get("artifacts", []) if p["name"] == name), None)
    if artifact is None:
        raise ValueError("artifact is not bound to this plan")
    return read_preview_artifact(artifact["ref"], state_dir=state_dir, project_root=project_root, digest=artifact["sha256"])


def _etype(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _payload(event: Any) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _event_id(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("id") or "")
    return str(getattr(event, "id", "") or "")


def _event_ts(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("ts") or "")
    return str(getattr(event, "ts", "") or "")


def _correlation_id(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("correlation_id") or "")
    return str(getattr(event, "correlation_id", "") or "")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


__all__ = [
    "PLAN_APPROVAL_REQUESTED",
    "PLAN_APPROVED",
    "PLAN_PREVIEW_SCHEMA_VERSION",
    "PLAN_REJECTED",
    "build_plan_preview",
    "plan_preview_available",
]
