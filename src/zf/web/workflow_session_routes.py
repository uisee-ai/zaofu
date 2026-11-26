"""A current attempt's explicitly shared final reply, re-authorized on every read."""
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from zf.core.events.factory import event_log_from_project
from zf.runtime.workflow_session_excerpt import current_reply
from zf.web.projections.workflow_workbench_cache import (WorkbenchSourceChanged, load_structure_snapshot,
    read_workbench_projection)


def member_session_router(resolve, detail, auth_response, read_only):
    router = APIRouter()
    path = "/api/projects/{project_id}/workflow-requests/{request_id}/member-session"

    @router.post(path)
    def session(project_id: str, request_id: str, request: Request, data: dict,
                authorization: str | None = Header(default=None), x_zf_web_token: str | None = Header(default=None)):
        def reply(status, value=None):
            return JSONResponse(value or {"available": False}, status_code=status, headers={"Cache-Control": "private, no-store"})
        if read_only:
            return reply(403)
        denied = auth_response("terminal-session-attach", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        context = resolve(project_id)
        if not context.config or not context.config.observability.workflow_session_excerpts:
            return reply(404)
        scope = data.get("scope") or {}
        if (not isinstance(scope, dict) or scope.get("historical") is not False or scope.get("execution_started") is not True
                or scope.get("request_id") != request_id or not scope.get("run_id") or not scope.get("proposal_digest")):
            return reply(404)
        stage = data.get("stage_id")
        generation = data.get("generation")
        if not isinstance(stage, str) or not stage or not isinstance(generation, str) or not generation:
            return reply(404)
        try:
            snapshot = load_structure_snapshot(context, project_id, request_id, 0, generation)
            if not snapshot or snapshot["head_seq"] != scope.get("source_seq"):
                return reply(404)
            selected = read_workbench_projection(context, project_id, request_id, 0,
                lambda pid, rid, **kwargs: detail(pid, rid, context=context, as_mapping=True, **kwargs),
                structure_only=True, structure_node_id="workflow:stage:" + stage)
            if isinstance(selected, JSONResponse):
                return reply(404)
            live, _ = selected
            if live["scope"] != scope:
                return reply(404)
            members = live["node"]["data"].get("team", {}).get("members", [])
            if not any(m["stage"] == stage and m["obligation_id"] == data.get("obligation_id")
                       and m["status"] in {"running", "retrying"} and m["instance"] == data.get("instance")
                       and m["history"] and m["history"][-1]["operation_id"] == data.get("operation_id")
                       and m["history"][-1]["attempt_id"] == data.get("attempt_id") for m in members):
                return reply(404)
            events = event_log_from_project(context.state_dir, config=context.config).read_all()
            if len(events) != live["scope"]["head_seq"]:
                return reply(404)
            value = current_reply(context.state_dir, events,
                run=scope["run_id"], operation=data.get("operation_id", ""), attempt=data.get("attempt_id", ""),
                role=data.get("instance", ""), read=data.get("read") is True)
        except (WorkbenchSourceChanged, OSError, ValueError, KeyError, TypeError):
            value = None
        return reply(200, value) if value else reply(404)

    return router
