"""Project workflow-request HTTP routes.

The routes live outside the already oversized Web server module. They remain
thin adapters over the canonical CLI/request services and receive auth/session
callbacks from the server so this module does not own Web security policy.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from zf.cli.flow import build_flow_intent
from zf.core.events import ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_delivery import (
    apply_flow_submit,
    build_flow_submit_preview,
)
from zf.runtime.workflow_intake import build_flow_intake
from zf.web.projections.request_util import _request_json
from zf.web.projections.workflow_yaml import project_workflow_yaml
from zf.web.projections.workspace import _resolve_api_project


def workflow_request_strings(value: object) -> list[str]:
    """Normalize repeatable workflow-request fields from JSON surfaces."""

    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def confirm_workflow_intake(
    *,
    state_dir: Path,
    intake_ref: str,
    actor: str,
    config: Any,
) -> dict[str, Any]:
    """Confirm the current Requirement revision before compiling a Proposal."""

    from zf.cli.flow import _load_manifest_for_intake
    from zf.runtime.workflow_requests import (
        load_workflow_request,
        revise_workflow_request,
    )

    manifest_path, manifest = _load_manifest_for_intake(Path(intake_ref))
    request_id = str((manifest or {}).get("request_id") or "")
    current = load_workflow_request(state_dir, request_id) if request_id else {}
    if manifest_path is not None and current and not current.get("confirmed"):
        current = revise_workflow_request(
            state_dir,
            manifest_path,
            actor=actor,
            confirm=True,
            writer=EventWriter(event_log_from_project(state_dir, config=config)),
        )
    return current


def build_workflow_request_router(
    *,
    default_project_id: str,
    default_state_dir: Path,
    default_config: Any,
    default_project_root: Path,
    mutation_auth_error: Callable[..., dict | None],
    session_cookie: Callable[[Request], str | None],
) -> APIRouter:
    router = APIRouter()
    from zf.web.workflow_preparation_worker import WorkflowPreparationWorker
    preparation_worker = WorkflowPreparationWorker()
    preparation_worker.watch(default_project_root)
    router.add_event_handler("startup", preparation_worker.start)
    router.add_event_handler("shutdown", preparation_worker.stop)

    def resolve(project_id: str):
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=default_state_dir,
            default_config=default_config,
            default_project_root=default_project_root,
        )
        preparation_worker.watch(context.project_root)
        return context

    def auth_response(
        action: str,
        request: Request,
        authorization: str | None,
        token: str | None,
    ) -> JSONResponse | None:
        error = mutation_auth_error(
            action,
            authorization=authorization,
            x_zf_web_token=token,
            web_session_token=session_cookie(request),
        )
        if error is None:
            return None
        body = dict(error)
        status_code = int(body.pop("_status_code", 403))
        return JSONResponse(body, status_code=status_code)

    @router.post("/api/projects/{project_id}/workflow-intake")
    async def project_workflow_intake(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-intake", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        context = resolve(project_id)
        payload = await _request_json(request)
        request_id = str(payload.get("request_id") or "")
        output = payload.get("output")
        if not output and request_id:
            output = context.project_root / "docs" / "intake" / f"{request_id}.md"
        result = build_flow_intake(
            kind=str(payload.get("kind") or payload.get("request_kind") or "auto"),
            source_ref=str(payload.get("from") or payload.get("source_ref") or ""),
            objective=str(
                payload.get("objective")
                or payload.get("message")
                or payload.get("reason")
                or ""
            ),
            source_root=str(payload.get("source_root") or ""),
            target_root=str(payload.get("target_root") or payload.get("target") or ""),
            backend=str(payload.get("backend") or "codex"),
            lanes=int(payload.get("lanes") or payload.get("requested_lanes") or 0),
            project_id=project_id,
            project_name=str(payload.get("project_name") or ""),
            request_id=request_id,
            source=str(payload.get("source") or "web"),
            created_by=str(payload.get("created_by") or "web"),
            channel_id=str(payload.get("channel_id") or ""),
            thread_id=str(payload.get("thread_id") or ""),
            acceptance=tuple(workflow_request_strings(payload.get("acceptance"))),
            constraints=tuple(workflow_request_strings(payload.get("constraints"))),
            open_questions=tuple(workflow_request_strings(payload.get("open_questions"))),
            output=Path(str(output)).expanduser() if output else None,
        )
        return JSONResponse({"ok": True, "status": "intake_created", "result": result})

    @router.post("/api/projects/{project_id}/workflow-classify")
    async def project_workflow_classify(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-classify", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        resolve(project_id)
        payload = await _request_json(request)
        intake_ref = str(payload.get("intake") or payload.get("intake_ref") or "").strip()
        if not intake_ref:
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "intake_ref is required"},
                status_code=422,
            )
        result = build_flow_intent(
            intake_path=Path(intake_ref).expanduser(),
            explicit_kind=str(payload.get("kind") or "auto"),
        )
        return JSONResponse({"ok": True, "status": "classified", "result": result})

    @router.post("/api/projects/{project_id}/workflow-clarify")
    async def project_workflow_clarify(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-intake", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        context = resolve(project_id)
        if context.config is None:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "project_not_initialized",
                    "reason": "project zf.yaml is required before requirement clarification",
                },
                status_code=409,
            )
        payload = await _request_json(request)
        intake_ref = str(payload.get("intake") or payload.get("intake_ref") or "").strip()
        if not intake_ref:
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "intake_ref is required"},
                status_code=422,
            )
        from zf.cli.flow import _load_manifest_for_intake
        from zf.runtime.workflow_requests import (
            WorkflowRequestConflict,
            load_workflow_request,
        )

        from zf.runtime.workflow_preview_review import revise_preview_requirement as revise_workflow_request

        request_id = str(payload.get("request_id") or "").strip()
        current = (
            load_workflow_request(context.state_dir, request_id)
            if request_id
            else {}
        )
        current_manifest_ref = str(
            current.get("workflow_input_manifest_ref") or ""
        )
        if current_manifest_ref:
            manifest_path = Path(current_manifest_ref).expanduser()
        else:
            manifest_path, _manifest = _load_manifest_for_intake(
                Path(intake_ref).expanduser()
            )
        if manifest_path is None:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "manifest_not_found",
                    "reason": "workflow input manifest not found for intake",
                },
                status_code=404,
            )
        expected_revision: int | None = None
        if "expected_revision" in payload:
            try:
                expected_revision = int(payload["expected_revision"])
            except (TypeError, ValueError):
                return JSONResponse(
                    {
                        "ok": False,
                        "status": "invalid_payload",
                        "reason": "expected_revision must be an integer",
                    },
                    status_code=422,
                )
            if expected_revision <= 0:
                return JSONResponse(
                    {
                        "ok": False,
                        "status": "invalid_payload",
                        "reason": "expected_revision must be positive",
                    },
                    status_code=422,
                )
        writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
        try:
            result = revise_workflow_request(
                context.state_dir,
                manifest_path,
                actor=str(payload.get("actor") or payload.get("requested_by") or "web"),
                objective=str(payload["objective"]) if "objective" in payload else None,
                source_root=str(payload["source_root"]) if "source_root" in payload else None,
                target_root=(
                    str(payload.get("target_root") or payload.get("target") or "")
                    if "target_root" in payload or "target" in payload
                    else None
                ),
                acceptance=(
                    workflow_request_strings(payload.get("acceptance"))
                    if "acceptance" in payload
                    else None
                ),
                constraints=(
                    workflow_request_strings(payload.get("constraints"))
                    if "constraints" in payload
                    else None
                ),
                open_questions=(
                    workflow_request_strings(payload.get("open_questions"))
                    if "open_questions" in payload
                    else None
                ),
                clarification_answers=(
                    [
                        dict(item)
                        for item in payload.get("clarification_answers") or []
                        if isinstance(item, dict)
                    ]
                    if "clarification_answers" in payload
                    else None
                ),
                confirm=bool(payload.get("confirm")),
                expected_revision=expected_revision,
                expected_requirement_digest=str(
                    payload.get("expected_requirement_digest") or ""
                ),
                writer=writer,
            )
        except WorkflowRequestConflict as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "stale_workflow_request",
                    "reason": str(exc),
                    "request_id": request_id,
                    "current_revision": int(current.get("revision") or 0),
                    "current_requirement_digest": str(
                        current.get("requirement_spec_digest") or ""
                    ),
                },
                status_code=409,
            )
        status = str(result.get("status") or "")
        return JSONResponse(
            {"ok": True, "status": status, "result": result},
            status_code=200,
        )

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}")
    def project_workflow_request_detail(project_id: str, request_id: str) -> JSONResponse:
        from zf.runtime.workflow_review_candidate import review_candidate
        from zf.runtime.workflow_requests import load_workflow_request

        context = resolve(project_id)
        result = load_workflow_request(context.state_dir, request_id)
        if not result:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "not_found",
                    "reason": f"workflow request {request_id!r} not found",
                },
                status_code=404,
            )
        proposal: dict[str, Any] = {}
        artifacts: dict[str, Any] = {}
        events = event_log_from_project(context.state_dir, config=context.config).read_all()
        try:
            proposal, _ = review_candidate(context.state_dir, result, events)
        except (ValueError, OSError, KeyError, TypeError):
            proposal = {}
        for key in (
            "short_flow_spec_ref",
            "config_diff_ref",
            "effective_config_ref",
        ):
            descriptor = proposal.get(key)
            if not isinstance(descriptor, dict):
                continue
            try:
                artifacts[key.removesuffix("_ref")] = hydrate_sidecar_ref(
                    context.state_dir,
                    descriptor,
                ).payload
                if key == "effective_config_ref":
                    artifacts["effective_config"] = project_workflow_yaml(
                        artifacts["effective_config"], proposal,
                        state_dir=context.state_dir,
                        project_root=context.project_root,
                    )
            except Exception:
                artifacts[key.removesuffix("_ref")] = {
                    "status": "unreadable",
                }
        requirement = _bounded_json_body(
            context.state_dir,
            str(result.get("requirement_spec_ref") or ""),
        )
        manifest = _bounded_json_body(
            context.state_dir,
            str(result.get("workflow_input_manifest_ref") or ""),
        )
        lifecycle = _workflow_request_lifecycle(
            events,
            request_id=request_id,
            proposal_digest=str(result.get("proposal_digest") or ""),
            run_id=str(result.get("run_id") or ""),
        )
        admission = (
            lifecycle.get("admission")
            if isinstance(lifecycle.get("admission"), dict)
            else {}
        )
        operation = _workflow_synthesis_operation(
            context.state_dir,
            str(result.get("synthesis_operation_id") or ""),
            config=context.config,
        )
        effective_result = {
            **result,
            "status": _request_status(result, admission, operation),
            "run_id": str(admission.get("run_id") or result.get("run_id") or ""),
            "queue_position": int(admission.get("queue_position") or 0),
            "run_admission": admission,
        }
        return JSONResponse(redact_obj({
            "ok": True,
            "status": effective_result.get("status"),
            "result": effective_result,
            "requirement": requirement,
            "proposal": proposal,
            "artifacts": artifacts,
            "lifecycle": lifecycle,
            "operation": operation,
            "links": {
                "intake_ref": str(
                    manifest.get("intake_json_ref")
                    or manifest.get("intake_ref")
                    or ""
                ),
                "workflow_input_manifest_ref": str(
                    result.get("workflow_input_manifest_ref") or ""
                ),
                "run_id": str(
                    lifecycle.get("run_id")
                    or result.get("run_id")
                    or ""
                ),
                "run_contract_ref": str(
                    lifecycle.get("refs", {}).get("run_contract_ref")
                    or result.get("run_contract_ref")
                    or ""
                ),
            },
        }))

    @router.get("/api/projects/{project_id}/workflow-requests")
    def project_workflow_requests(project_id: str) -> JSONResponse:
        context = resolve(project_id)
        root = context.state_dir / "workflow-requests"
        events = event_log_from_project(
            context.state_dir,
            config=context.config,
        ).read_all()
        from zf.runtime.run_admission import request_admission_view

        items: list[dict[str, Any]] = []
        for path in root.glob("*.json") if root.exists() else ():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("request_id"):
                admission = request_admission_view(
                    events,
                    request_id=str(value.get("request_id") or ""),
                    run_id=str(value.get("run_id") or ""),
                )
                requirement = _bounded_json_body(
                    context.state_dir,
                    str(value.get("requirement_spec_ref") or ""),
                )
                operation = _workflow_synthesis_operation(
                    context.state_dir,
                    str(value.get("synthesis_operation_id") or ""),
                    config=context.config,
                )
                items.append({
                    **value,
                    "status": _request_status(value, admission, operation),
                    "run_id": str(
                        admission.get("run_id")
                        or value.get("run_id")
                        or ""
                    ),
                    "queue_position": int(
                        admission.get("queue_position") or 0
                    ),
                    "run_admission": admission,
                    "operation": operation,
                    "objective": str(requirement.get("objective") or ""),
                    "acceptance_count": len(
                        requirement.get("acceptance")
                        if isinstance(requirement.get("acceptance"), list)
                        else []
                    ),
                })
        items.sort(
            key=lambda item: str(
                item.get("updated_at") or item.get("created_at") or ""
            ),
            reverse=True,
        )
        return JSONResponse(redact_obj({
            "ok": True,
            "items": items,
            "count": len(items),
        }))

    @router.get(
        "/api/projects/{project_id}/workflow-operations/{operation_id}"
    )
    def project_workflow_operation(
        project_id: str,
        operation_id: str,
    ) -> JSONResponse:
        context = resolve(project_id)
        operation = _workflow_synthesis_operation(
            context.state_dir,
            operation_id,
            config=context.config,
        )
        return JSONResponse(redact_obj({
            "ok": bool(operation),
            **operation,
        }), status_code=200 if operation else 404)

    @router.post("/api/projects/{project_id}/workflow-submit")
    async def project_workflow_submit(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-submit", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        context = resolve(project_id)
        payload = await _request_json(request)
        intake_ref = str(payload.get("intake") or payload.get("intake_ref") or "").strip()
        if not intake_ref:
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "intake_ref is required"},
                status_code=422,
            )
        apply = bool(payload.get("apply"))
        current: dict[str, Any] = {}
        if not apply:
            from zf.cli.flow import _load_manifest_for_intake
            from zf.runtime.workflow_requests import (
                load_workflow_request,
                revise_workflow_request,
            )

            request_id = str(payload.get("request_id") or "").strip()
            current = (
                load_workflow_request(context.state_dir, request_id)
                if request_id
                else {}
            )
            manifest_path: Path | None = None
            if current.get("workflow_input_manifest_ref"):
                manifest_path = Path(
                    str(current["workflow_input_manifest_ref"])
                ).expanduser()
            else:
                manifest_path, manifest = _load_manifest_for_intake(
                    Path(intake_ref).expanduser()
                )
                request_id = str((manifest or {}).get("request_id") or "")
                current = (
                    load_workflow_request(context.state_dir, request_id)
                    if request_id
                    else {}
                )
            if manifest_path is not None and current and not current.get("confirmed"):
                current = revise_workflow_request(
                    context.state_dir,
                    manifest_path,
                    actor=str(payload.get("requested_by") or "web"),
                    confirm=True,
                    writer=EventWriter(
                        event_log_from_project(
                            context.state_dir,
                            config=context.config,
                        )
                    ),
                )
        config_ref = Path(
            str(payload.get("config") or payload.get("config_ref") or context.config_path)
        ).expanduser()
        if apply:
            from zf.runtime.control_actions import ControlledActionService

            writer = EventWriter(
                event_log_from_project(context.state_dir, config=context.config)
            )
            requested = writer.append(ZfEvent(
                type="web.action.requested",
                actor="web",
                correlation_id=str(payload.get("request_id") or ""),
                payload={
                    "action": "workflow-submit",
                    "requested_action": "workflow-submit",
                    "request": redact_obj(payload),
                },
            ))
            result = ControlledActionService(
                context.state_dir,
                writer,
                config=context.config,
                project_root=context.project_root,
                actor=str(payload.get("requested_by") or "web"),
                source="workflow-proposal-page",
                surface="web",
            ).execute(
                action="workflow-submit",
                requested_action="workflow-submit",
                payload=payload,
                requested=requested,
            )
            status_code = int(result.pop("_status_code", 200))
            return JSONResponse(result, status_code=status_code)
        if current:
            from zf.runtime.workflow_prepare import prepare_request_revision
            try:
                prepared = prepare_request_revision(
                    state_dir=context.state_dir, project_root=context.project_root, config=context.config,
                    writer=EventWriter(event_log_from_project(context.state_dir, config=context.config)),
                    request=current, actor=str(payload.get("requested_by") or "web"),
                    payload={**payload, "intake_ref": intake_ref},
                )
            except ValueError as exc:
                return JSONResponse({"ok": False, "status": "preparation_blocked", "reason": str(exc)}, status_code=409)
            if prepared is not None:
                return JSONResponse({**prepared, "result": prepared}, status_code=202)
        synthesis_ref = (
            dict(current.get("synthesis_ref"))
            if isinstance(current.get("synthesis_ref"), dict)
            else None
        )
        result = build_flow_submit_preview(
            config_path=config_ref,
            intake_path=Path(intake_ref).expanduser(),
            flow_kind=str(payload.get("kind") or current.get("kind") or ""),
            task_id=str(payload.get("task_id") or ""),
            pattern_id=str(payload.get("pattern_id") or ""),
            requested_by=str(payload.get("requested_by") or "web"),
            reason=str(payload.get("reason") or ""),
            allow_missing_env=bool(payload.get("allow_missing_env")),
            synthesis_result_ref=synthesis_ref,
        )
        status = str(result.get("status") or "")
        code = 409 if status == "STOP" else 200
        return JSONResponse(
            {"ok": status != "STOP", "status": status, "applied": False, "result": result},
            status_code=code,
        )

    from zf.web.workflow_preview_review_routes import build_preview_review_router
    router.include_router(build_preview_review_router(resolve=resolve, auth_response=auth_response))
    from zf.web.workflow_workbench_routes import build_workbench_router
    router.include_router(build_workbench_router(resolve=resolve, detail=project_workflow_request_detail))
    from zf.web.plan_artifact_review_routes import build_plan_artifact_review_router
    router.include_router(build_plan_artifact_review_router(resolve=resolve, auth_response=auth_response))
    return router


def _bounded_json_body(state_dir: Path, raw_ref: str) -> dict[str, Any]:
    if not raw_ref:
        return {}
    try:
        root = Path(state_dir).resolve()
        path = Path(raw_ref).expanduser().resolve(strict=True)
        path.relative_to(root)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _workflow_request_lifecycle(
    events: list[Any],
    *,
    request_id: str,
    proposal_digest: str,
    run_id: str,
) -> dict[str, Any]:
    from zf.runtime.run_admission import request_admission_view

    refs: dict[str, Any] = {}
    matched: list[Any] = []
    admission = request_admission_view(
        events, request_id=request_id, run_id=run_id,
    )
    # Synthesis operations have a workflow-request:* identity, not a Run.
    effective_run_id = str(admission.get("run_id") or run_id or "")
    ref_keys = (
        "workflow_proposal_ref",
        "effective_config_ref",
        "run_contract_ref",
        "plan_package_ref",
        "plan_artifact_package_ref",
        "task_map_ref",
        "completion_receipt_ref",
        "goal_dossier_ref",
    )
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_request_id = str(payload.get("request_id") or "")
        event_run_id = str(payload.get("run_id") or payload.get("workflow_run_id") or "")
        event_proposal_digest = str(
            payload.get("proposal_digest")
            or payload.get("workflow_proposal_digest")
            or ""
        )
        belongs = (
            str(event.correlation_id or "") == request_id
            or event_request_id == request_id
            or (bool(effective_run_id) and event_run_id == effective_run_id)
            or (
                bool(proposal_digest)
                and event_proposal_digest == proposal_digest
            )
        )
        if not belongs:
            continue
        matched.append(event)
        for key in ref_keys:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                refs[key] = value
    types = [event.type for event in matched]
    terminal = next(
        (
            event
            for event in reversed(matched)
            if event.type in {
                "run.goal.completed",
                "run.goal.blocked",
                "run.completed",
                "run.failed",
                "run.cancelled",
                "run.abandoned",
            }
        ),
        None,
    )
    if admission.get("active"):
        terminal = None
    return {
        "config_applied": "workflow.config.change.applied" in types,
        "submitted": "workflow.submit.accepted" in types,
        "run_started": any(
            event_type in {
                "workflow.invoke.accepted",
                "run.admission.admitted",
            }
            for event_type in types
        ),
        "terminal": terminal.type if terminal is not None else "",
        "terminal_event_id": str(terminal.id if terminal is not None else ""),
        "run_id": effective_run_id,
        "event_count": len(matched),
        "refs": refs,
        "admission": admission,
    }


def _request_status(request: dict, admission: dict, operation: dict) -> str:
    status = str(admission.get("status") or request.get("status") or "")
    if (
        not (admission.get("run_id") or request.get("run_id"))
        and status in {"draft", "ready", "clarifying"}
        and operation.get("queue_status") == "cancelled"
        and request.get("synthesis_operation_revision") == request.get("revision")
    ):
        return "cancelled"
    return status


def _workflow_synthesis_operation(
    state_dir: Path,
    operation_id: str,
    *,
    config: Any,
) -> dict[str, Any]:
    if not operation_id:
        return {}
    from zf.web.projections.operations import workflow_operation

    projection = workflow_operation(
        state_dir,
        operation_id,
        config=config,
    )
    status = str(projection.get("status") or "unknown")
    return {
        **projection,
        "queue_status": {
            "requested": "queued",
            "reserved": "queued",
            "running": "running",
            "settled": "settled",
            "failed": "failed",
            "blocked": "failed",
            "superseded": "obsolete",
            "cancelled": "cancelled",
        }.get(status, status),
        "query_ref": f"workflow-operations/{operation_id}",
    }


__all__ = [
    "build_workflow_request_router",
    "confirm_workflow_intake",
    "workflow_request_strings",
]
