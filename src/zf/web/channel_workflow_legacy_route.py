"""Compatibility adapter for the deprecated Channel workflow route."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def run_legacy_channel_workflow_route(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    default_project_id: str,
    channel_id: str,
    payload: dict,
    authorization: str | None,
    web_action_token: str | None,
    web_session_token: str | None,
    idempotency_key: str | None,
    action_runner: Callable[..., dict],
) -> tuple[dict, int]:
    if config is None or not (project_root / "zf.yaml").exists():
        return {
            "ok": False,
            "status": "project_initialization_required",
            "reason": (
                "initialize/register the project before planning a "
                "Channel workflow"
            ),
        }, 409
    thread_id = str(payload.get("thread_id") or "main")
    workflow_context = (
        dict(payload.get("workflow_context"))
        if isinstance(payload.get("workflow_context"), dict)
        else {}
    )
    workflow_context.update({
        key: value
        for key, value in {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "channel_member_id": payload.get("channel_member_id"),
            "leader_revision": payload.get("leader_revision"),
            "prd_revision": payload.get("prd_revision"),
            "source_ref": (
                payload.get("source_ref")
                or payload.get("channel_prd_ref")
            ),
            "source_digest": (
                payload.get("source_digest")
                or payload.get("channel_prd_digest")
            ),
        }.items()
        if value not in (None, "")
    })
    task_id = str(payload.get("task_id") or "").strip()
    objective = str(
        payload.get("objective")
        or payload.get("reason")
        or payload.get("summary")
        or ""
    ).strip()
    backend = str(payload.get("backend") or "deterministic")
    if backend in {"mock", "fake"}:
        backend = "deterministic"
    result = action_runner(
        state_dir,
        "chat-orchestrator",
        payload={
            "backend": backend,
            "permission_profile": "read_only",
            "scope": "project",
            "project_id": default_project_id,
            "conversation_id": f"channel:{channel_id}",
            "thread_key": f"channel-plan:{channel_id}:{thread_id}",
            "task_id": task_id,
            "message": _workflow_plan_message(
                task_id=task_id,
                objective=objective,
            ),
            "workflow_context": workflow_context,
            "source": "web-channel-workflow-plan",
        },
        authorization=authorization,
        x_zf_web_token=web_action_token,
        web_session_token=web_session_token,
        x_idempotency_key=idempotency_key,
        config=config,
        project_root=project_root,
        project_id=default_project_id,
        legacy_route=True,
    )
    status_code = int(result.pop("_status_code", 202))
    result["deprecated_route"] = True
    result["replacement"] = (
        "Channel PRD -> Leader -> zaofu agent Plan -> Owner approval"
    )
    return result, status_code


def _workflow_plan_message(*, task_id: str, objective: str) -> str:
    message = (
        f"Plan a workflow for existing Task {task_id} from the exact "
        "confirmed Channel PRD. Return task_workflow Plan options only; "
        "do not invoke the workflow."
        if task_id
        else (
            "Propose creating a Task from the exact confirmed Channel "
            "PRD. Return a task_create Plan only; do not create the Task "
            "or invoke a workflow."
        )
    )
    if objective:
        message += f"\n\nObjective: {objective}"
    return message
