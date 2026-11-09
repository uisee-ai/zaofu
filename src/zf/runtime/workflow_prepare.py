"""Shared, non-executing preparation of Task-bound Workflow requests."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any
from urllib.parse import urlencode

from zf.core.workspace.registry import stable_project_id

from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.channel_workflow_authority import (
    channel_authority_context_from_task,
    channel_workflow_authority_error,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_anchor import workflow_task_request_binding
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.workflow_request_acceptance import bind_task_workflow_inputs
from zf.runtime.workflow_requests import load_workflow_request, workflow_request_path


def task_preparation_payload(
    state_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    """Inherit canonical Task inputs; preparation never grants start authority."""
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        return dict(payload)
    task = TaskStore(state_dir / "kanban.json").get(task_id)
    if task is None:
        raise ValueError(f"workflow task {task_id!r} does not exist")
    authority = channel_authority_context_from_task(task)
    if authority and (error := channel_workflow_authority_error(state_dir, authority)):
        raise ValueError(error)
    digest = task_workflow_binding_digest(task)
    if payload.get("task_contract_digest") not in (None, "", digest):
        raise ValueError("workflow Task binding is stale")
    # Preserve the whole contract. A caller may add routing inputs, but cannot
    # use preparation to replace the Task's acceptance or Channel source.
    parameters = {
        key: payload[key]
        for key in ("source_root", "target_root", "backend")
        if payload.get(key)
    }
    inherited, binding, contract, _ = bind_task_workflow_inputs(
        parameters,
        task,
        task_contract_digest=digest,
    )
    request_binding = workflow_task_request_binding(task)
    request_id = str(
        request_binding.get("request_id")
        or f"prepare-{task.id}-{digest.removeprefix('sha256:')[:16]}"
    )
    if payload.get("request_id") not in (None, "", request_id):
        existing = load_workflow_request(state_dir, str(payload["request_id"]))
        manifest = (
            json.loads(Path(existing["workflow_input_manifest_ref"]).read_text())
            if existing
            else {}
        )
        if (manifest.get("task_input_binding") or {}).get("task_id") != task_id:
            raise ValueError("workflow Request does not match Task binding")
        request_id = str(payload["request_id"])
    return {
        **payload,
        **inherited,
        **authority,
        "task_id": task.id,
        "request_id": request_id,
        "objective": task.contract.behavior or task.title,
        "task_input_binding": binding,
        "task_input_contract": contract,
    }


def prepared_task_error(state_dir: Path, request: dict, *, actor: str = "") -> str:
    """Recheck Task/Channel identity at review and at explicit submission."""
    manifest = json.loads(Path(request["workflow_input_manifest_ref"]).read_text())
    binding = manifest.get("task_input_binding") or {}
    if not binding:
        return ""
    task = TaskStore(state_dir / "kanban.json").get(str(binding.get("task_id") or ""))
    if task is None or task_workflow_binding_digest(task) != binding.get(
        "task_contract_digest"
    ):
        return "prepared Workflow Task changed; prepare a new proposal"
    authority = channel_authority_context_from_task(task)
    if not authority:
        return ""
    if error := channel_workflow_authority_error(state_dir, authority):
        return error
    if actor:
        from zf.runtime.channel_owner_authority import channel_owner_authority_error
        from zf.runtime.channel_projection import project_channel
        from zf.runtime.channel_readiness import (
            channel_task_execution_authorization_error,
        )

        channel = project_channel(state_dir, authority["channel_id"]) or {}
        if error := channel_owner_authority_error(
            channel, actor=actor, capability="channel.workflow.start"
        ):
            return error
        readiness_error = channel_task_execution_authorization_error(task)
        if (
            readiness_error
            and task.contract.evidence_contract.get("readiness_verdict") != "ready"
        ):
            return readiness_error
    return ""


def prior_synthesis_request(
    state_dir: Path, writer: EventWriter, request_id: str
) -> dict:
    for event in reversed(writer.event_log.read_all()):
        if (
            event.type == "workflow.synthesis.queued"
            and event.payload.get("request_id") == request_id
        ):
            operation = load_workflow_operation(
                writer.event_log, event.payload["operation_id"]
            )
            if operation and operation.get("request_ref"):
                body = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload
                return {**dict(body.get("request") or {}), "_operation": operation}
    return {}


def prepare_request_revision(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    writer: EventWriter,
    request: dict,
    actor: str,
    payload: dict,
) -> dict | None:
    """Queue semantic selection or resume it after answers; no Run is started.

    Explicit kinds without prior Synthesis retain their deterministic compiler.
    The durable operation keeps the backend/context across requirement revisions.
    """
    from zf.runtime.workflow_synthesis import enqueue_workflow_synthesis

    request_id = str(request["request_id"])
    from zf.runtime.workflow_request_concurrency import (
        check_workflow_request_preconditions,
    )

    check_workflow_request_preconditions(
        request,
        expected_revision=payload.get("expected_revision"),
        expected_requirement_digest=str(
            payload.get("expected_requirement_digest") or ""
        ),
    )
    project_id = stable_project_id(name=config.project.name, root=project_root)
    previous = prior_synthesis_request(state_dir, writer, request_id)
    automatic = str(request.get("kind") or "auto") == "auto"
    if (
        previous
        and not automatic
        and payload.get("kind") not in (None, "", "auto")
        and not payload.get("synthesis_backend")
    ):
        old_requirement = json.loads(Path(previous["requirement_ref"]).read_text())
        if old_requirement.get("kind") == "auto":
            return (
                None  # Explicit operator selection supersedes pending auto synthesis.
            )

    backend = str(payload.get("synthesis_backend") or previous.get("backend") or "")
    if automatic and not backend:
        backend = str(
            payload.get("backend") or config.runtime.run_manager.backend or "codex"
        )
    if not backend:
        return None
    common = {
        "ok": True,
        "request_id": request_id,
        "request_revision": int(request.get("revision") or 0),
        "intake_ref": str(payload.get("intake_ref") or ""),
        "preview_url": "/?"
        + urlencode(
            {"page": "workflows", "project": project_id, "request": request_id}
        ),
        "request_projection_ref": str(workflow_request_path(state_dir, request_id)),
        "request_ref": f"/api/projects/{project_id}/workflow-requests/{request_id}",
    }
    if request.get("status") in {"draft", "clarifying"}:
        return {
            **common,
            "status": "clarifying",
            "open_questions": request.get("open_questions", []),
            "missing_required_fields": request.get("missing_required_fields", []),
        }
    from zf.runtime.workflow_git_readiness import preparation_git_diagnostics
    if blockers := preparation_git_diagnostics(project_root):
        return {**common, "ok": False, "status": "preparation_blocked",
                "blockers": blockers, "reason": blockers[0]["message"], "can_retry": True}
    context = dict(previous.get("operation_context") or {})
    if previous.get("request_revision") != request.get("revision"):
        context.pop("retry_of_operation_id", None)
        context.pop("reusable_synthesis_result_ref", None)
    if previous.get("request_revision") == request.get("revision"):
        operation = previous["_operation"]
        status = operation["status"]
        if status in {"failed", "blocked"} and payload.get("retry_failed") is True:
            context["retry_of_operation_id"] = operation["operation_id"]
            context.pop("reusable_synthesis_result_ref", None)
            if operation.get("admitted_call_result_ref"):
                context["reusable_synthesis_result_ref"] = operation["admitted_call_result_ref"]
        else:
            terminal = status in {"failed", "blocked", "cancelled", "superseded"}
            return {
                **common,
                "ok": not terminal,
                "status": ("preparation_failed" if terminal else
                           "proposed" if request.get("proposal_ref") else
                           "synthesis_running" if status in {"running", "settled"} else "synthesis_queued"),
                "reason": str(operation.get("reason") or "") if terminal else "",
                "can_retry": status in {"failed", "blocked"},
                "synthesis_operation_id": operation["operation_id"],
                "synthesis_request_hash": operation["request_hash"],
                "operation_status": status,
                "operation_ref": f"/api/projects/{project_id}/workflow-operations/{operation['operation_id']}",
            }
    context.update(
        {
            key: value
            for key, value in {
                "config_ref": str(project_root / "zf.yaml"),
                "intake_ref": payload.get("intake_ref"),
                "task_id": payload.get("task_id"),
                "pattern_id": payload.get("pattern_id"),
            }.items()
            if value
        }
    )
    context.setdefault("requested_by", actor)
    context.setdefault("reason", "prepare Workflow for review")
    if "allow_missing_env" in payload:
        context["allow_missing_env"] = bool(payload["allow_missing_env"])
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=project_root,
        config=config,
        writer=writer,
        request_id=request_id,
        actor=str(previous.get("actor") or actor),
        backend=backend,
        operation_context=context,
    )
    return {
        **common,
        "status": "synthesis_queued",
        "synthesis_operation_id": queued.operation_id,
        "synthesis_request_hash": queued.request_hash,
        "operation_status": queued.status,
        "operation_ref": f"/api/projects/{project_id}/workflow-operations/{queued.operation_id}",
    }


def synthesis_open_questions(
    state_dir: Path, request: dict, family: str, questions: list[str]
) -> list[str]:
    """Missing selected-route inputs require clarification, never an executable proposal."""
    from zf.core.workflow.request_policy import missing_fields_for_kind
    from zf.runtime.workflow_requests import hydrate_workflow_requirement

    requirement = hydrate_workflow_requirement(state_dir, request)
    kind = {
        "IssueFlow": "issue",
        "PrdFlow": "prd",
        "RefactorFlow": "refactor",
        "Workflow": "workflow",
    }[family]
    missing = missing_fields_for_kind(
        kind,
        **{
            key: str(requirement.get(key) or "")
            for key in ("objective", "source_ref", "source_root", "target_root")
        },
    )
    return list(
        dict.fromkeys(
            [*questions, *(f"Please provide {field} for {kind}." for field in missing)]
        )
    )
