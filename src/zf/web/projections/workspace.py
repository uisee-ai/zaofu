"""Projections layer: workspace (moved verbatim from web/server.py)."""
from __future__ import annotations

from fastapi import HTTPException
from pathlib import Path
from typing import Any
from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import ZfConfig
from zf.core.workspace import ProjectResolver
from zf.core.workspace import WorkspaceProject
from zf.core.workspace import WorkspaceRegistry
from zf.core.workspace import project_lifecycle
from zf.runtime.automation_projection import project_automations
from zf.web.projections.common import _active_workspace_project_id, _default_workspace_project, _no_default_project_payload, _payload_mentions
from zf.web.projections.events import _event_to_dict, _events_with_seq
from zf.web.projections.fanouts import _fanouts
from zf.web.projections.tasks import _kanban


def _workspace_project_payload(project: WorkspaceProject) -> dict[str, Any]:
    payload = project.to_dict()
    lifecycle = project_lifecycle(project)
    payload["state_dir_resolved"] = lifecycle.state_dir_resolved
    payload["lifecycle"] = lifecycle.to_dict()
    payload["can_open_board"] = lifecycle.can_open_board
    return payload


def _workspace_projects_payload(
    *,
    default_project_id: str,
    default_state_dir: Path,
    default_config: ZfConfig | None,
    default_project_root: Path,
    default_project_opened_at: str = "",
) -> dict[str, Any]:
    projects: dict[str, dict[str, Any]] = {}
    if default_project_id:
        default_project = _default_workspace_project(
            project_id=default_project_id,
            state_dir=default_state_dir,
            config=default_config,
            project_root=default_project_root,
            last_opened_at=default_project_opened_at,
        )
        projects[default_project_id] = _workspace_project_payload(default_project)
    try:
        for project in WorkspaceRegistry().list_projects():
            projects[project.project_id] = _workspace_project_payload(project)
    except Exception as exc:
        items = list(projects.values())
        active_project_id = _active_workspace_project_id(
            items,
            default_project_id=default_project_id,
        )
        return {
            "schema_version": "workspace.projects.v1",
            "server_default_project_id": default_project_id,
            "active_project_id": active_project_id,
            "active_project_is_server_default": (
                bool(default_project_id) and active_project_id == default_project_id
            ),
            "items": items,
            "projects": items,
            "warning": str(exc),
        }
    if default_project_id:
        default_project = _default_workspace_project(
            project_id=default_project_id,
            state_dir=default_state_dir,
            config=default_config,
            project_root=default_project_root,
            last_opened_at=default_project_opened_at,
        )
        default_payload = _workspace_project_payload(default_project)
        existing = projects.get(default_project_id)
        if existing:
            registry_opened_at = str(existing.get("last_opened_at") or "")
            default_payload["aliases"] = existing.get("aliases", [])
            default_payload["last_opened_at"] = max(
                registry_opened_at,
                default_project_opened_at,
            )
        projects[default_project_id] = default_payload
    items = sorted(
        projects.values(),
        key=lambda item: (
            str(item.get("last_opened_at") or ""),
            str(item.get("name", "")),
            str(item.get("root", "")),
        ),
        reverse=True,
    )
    active_project_id = _active_workspace_project_id(
        items,
        default_project_id=default_project_id,
    )
    return {
        "schema_version": "workspace.projects.v1",
        "server_default_project_id": default_project_id,
        "active_project_id": active_project_id,
        "active_project_is_server_default": (
            bool(default_project_id) and active_project_id == default_project_id
        ),
        "items": items,
        "projects": items,
    }


def _resolve_api_project(
    project_id: str,
    *,
    default_project_id: str,
    default_state_dir: Path,
    default_config: ZfConfig | None,
    default_project_root: Path,
    require_initialized: bool = True,
) -> ProjectContext:
    if project_id == "default" and not default_project_id:
        raise HTTPException(409, _no_default_project_payload())
    if project_id in {"default", default_project_id}:
        context = ProjectContext(
            project_root=default_project_root,
            config_path=default_project_root / "zf.yaml",
            config=default_config,
            state_dir=default_state_dir,
        )
        if require_initialized:
            _ensure_project_initialized(
                context,
                project_id=default_project_id or "default",
            )
        return context
    try:
        context = ProjectResolver().resolve(project_id).context
        if require_initialized:
            _ensure_project_initialized(context, project_id=project_id)
        return context
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


def _project_initialized(state_dir: Path) -> bool:
    state_dir = Path(state_dir)
    return state_dir.exists() and (state_dir / "kanban.json").exists() and (
        state_dir / "events.jsonl"
    ).exists()


def _ensure_project_initialized(
    context: ProjectContext,
    *,
    project_id: str,
) -> None:
    if _project_initialized(context.state_dir):
        return
    raise HTTPException(
        409,
        _project_uninitialized_payload(
            project_id=project_id,
            state_dir=context.state_dir,
            project_root=context.project_root,
        ),
    )


def _project_uninitialized_payload(
    *,
    project_id: str,
    state_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    missing = [
        name for name in ("kanban.json", "events.jsonl")
        if not (Path(state_dir) / name).exists()
    ]
    return {
        "ok": False,
        "status": "project_uninitialized",
        "project_id": project_id,
        "project_root": str(Path(project_root).resolve()),
        "state_dir": str(Path(state_dir).resolve()),
        "reason": (
            f"state dir not found: {Path(state_dir).resolve()}"
            if not Path(state_dir).exists()
            else "missing runtime truth files: " + ", ".join(missing)
        ),
        "missing_truth_files": missing,
    }


def _workspace_channel_summary(state_dir: Path) -> dict[str, Any]:
    try:
        from zf.runtime.channel_projection import project_channels

        page = project_channels(state_dir)
        # 999451c dropped the "items" alias (list views key on
        # "channels") and b6dbffe flattens reply_requests in list views,
        # exposing failed_reply_count instead.
        channels = page.get("channels", [])
        if not isinstance(channels, list):
            channels = []
        return {
            "count": len(channels),
            "attention": sum(
                len(item.get("attention", []))
                for item in channels
                if isinstance(item, dict) and isinstance(item.get("attention"), list)
            ),
            "pending_replies": sum(
                int(item.get("pending_reply_count") or 0)
                for item in channels
                if isinstance(item, dict)
            ),
            "failed_replies": sum(
                int(item.get("failed_reply_count") or 0)
                for item in channels
                if isinstance(item, dict)
            ),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _workspace_automation_summary(
    state_dir: Path,
    *,
    project_id: str,
    project_name: str,
) -> dict[str, Any]:
    try:
        page = project_automations(
            state_dir,
            project_id=project_id,
            project_name=project_name,
        )
        items = page.get("items", [])
        if not isinstance(items, list):
            items = []
        return {
            "count": len(items),
            "active": sum(
                1 for item in items
                if isinstance(item, dict)
                and str(item.get("status") or "") in {"running", "active"}
            ),
            "failed": sum(
                1 for item in items
                if isinstance(item, dict)
                and str(item.get("status") or "") == "failed"
            ),
            "proposals": sum(
                len(item.get("proposals", []))
                for item in items
                if isinstance(item, dict) and isinstance(item.get("proposals"), list)
            ),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _project_action_envelope(project_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    route_project_id = str(project_id or "").strip()
    body_project_id = str(raw.get("project_id") or "").strip()
    if not body_project_id:
        return {
            "_status_code": 422,
            "ok": False,
            "status": "invalid_envelope",
            "reason": "project_id is required in project-scoped action envelope",
        }
    if body_project_id != route_project_id:
        return {
            "_status_code": 422,
            "ok": False,
            "status": "project_mismatch",
            "reason": "route project_id and body project_id differ",
        }
    inner = raw.get("payload")
    if not isinstance(inner, dict):
        return {
            "_status_code": 422,
            "ok": False,
            "status": "invalid_envelope",
            "reason": "payload object is required in project-scoped action envelope",
        }
    payload = dict(inner)
    payload["project_id"] = route_project_id
    for key in ("action_id", "actor", "source_session_id", "evidence_refs"):
        if key in raw:
            payload[key] = raw[key]
    idempotency_key = str(raw.get("idempotency_key") or "").strip()
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    return {
        "ok": True,
        "payload": payload,
        "idempotency_key": idempotency_key,
    }


def _projection_reply_if_requested(
    state_dir: Path,
    payload: dict,
    message: str,
    task_id: str | None,
) -> dict | None:
    mode = str(payload.get("mode") or "").strip()
    lowered = message.lower()
    status_terms = {
        "status", "why", "blocker", "blocked", "progress", "summary",
        "summarize", "state", "当前", "状态", "为什么", "阻塞", "进度", "总结",
    }
    if mode != "projection_first" and (not task_id or not any(term in lowered for term in status_terms)):
        return None
    tasks = _kanban(state_dir)
    task = next((item for item in tasks if item.get("id") == task_id), None)
    events = _events_with_seq(state_dir)
    relevant_events = [
        (seq, event)
        for seq, event in events
        if (task_id and getattr(event, "task_id", None) == task_id)
        or (task_id and _payload_mentions(getattr(event, "payload", {}) or {}, task_id))
    ]
    if task:
        latest = _event_to_dict(*relevant_events[-1]) if relevant_events else None
        blockers = []
        if task.get("blocked_reason"):
            blockers.append(str(task.get("blocked_reason")))
        if task.get("blocked_by"):
            blockers.append("blocked_by=" + ",".join(str(x) for x in task.get("blocked_by") or []))
        owner = str(task.get("assigned_to") or "unassigned")
        status = str(task.get("status") or "unknown")
        latest_text = (
            f" Latest event is {latest.get('type')} seq {latest.get('seq')}."
            if latest else " No task events are recorded yet."
        )
        blocker_text = f" Blockers: {'; '.join(blockers)}." if blockers else " No explicit blocker is recorded."
        answer = (
            f"{task_id} is {status}, owned by {owner}."
            f"{blocker_text}{latest_text}"
        )
        refs = []
        if latest and latest.get("id"):
            refs.append({"kind": "event", "id": latest["id"]})
        links = task.get("links") if isinstance(task.get("links"), dict) else {}
        for kind, key in (("trace", "trace"), ("fanout", "fanout"), ("candidate", "candidate")):
            value = links.get(key)
            if value:
                refs.append({"kind": kind, "id": value})
        return {
            "source": "projection_explainer",
            "scope": "task",
            "task_id": task_id,
            "answer": answer,
            "evidence_refs": refs,
            "mutates_task_state": False,
            "runtime_followup": "queued_no_runtime",
        }
    fanouts = _fanouts(state_dir)
    active_fanout = fanouts[0] if fanouts else None
    if active_fanout:
        progress = active_fanout.get("progress") or {}
        answer = (
            f"Latest fanout {active_fanout.get('fanout_id')} is "
            f"{active_fanout.get('status', 'observed')} with "
            f"{progress.get('done', 0)}/{progress.get('total', 0)} children done."
        )
    else:
        answer = f"Project has {len(tasks)} active tasks and no active fanout projection."
    return {
        "source": "projection_explainer",
        "scope": "project",
        "task_id": task_id or "",
        "answer": answer,
        "evidence_refs": [],
        "mutates_task_state": False,
        "runtime_followup": "queued_no_runtime",
    }
