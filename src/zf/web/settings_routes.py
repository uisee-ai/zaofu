"""Controlled Settings API; read projections and precise config edits only."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
import json
import yaml

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from zf.core.config.schema import SelfIssueConfig
from zf.core.config.loader import ConfigError
from zf.core.config.self_issue_policy import inherit_workspace_self_issue_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.issue_settings import policy_projection, save_issue_policy
from zf.runtime.feishu_issue_merge_settings import (
    read_issue_merge_settings,
    save_issue_merge_settings,
    test_issue_merge_connection,
)
from zf.web.developer_access import developer_request_allowed, same_origin


def build_settings_router(*, resolve_ctx: Callable, workspace_root: Path, workspace_config: Any,
                          authorize: Callable, authorize_developer: Callable, session_cookie: Callable):
    router = APIRouter()

    def auth(request: Request):
        return authorize("settings-read", authorization=request.headers.get("Authorization"),
                         x_zf_web_token=request.headers.get("X-ZF-Web-Token"), web_session_token=session_cookie(request))

    def is_developer(request: Request) -> bool:
        # Explicit cookie/token only: trusted-local browsing still starts in User mode.
        return developer_request_allowed() or bool(request.headers.get("X-ZF-Developer-Token")) and authorize_developer(
            "settings-project", authorization=None, x_zf_web_token=None,
            x_zf_developer_token=request.headers.get("X-ZF-Developer-Token"), web_session_token=None) is None

    def target(project_id: str):
        context = resolve_ctx(project_id)
        effective = inherit_workspace_self_issue_config(context.config, workspace_config) or context.config
        policy = effective.self_issue if effective else SelfIssueConfig()
        inherited = bool(workspace_config and effective and effective.self_issue.target_locked and
                         Path(context.project_root).resolve() != Path(workspace_root).resolve())
        owner = Path(workspace_root) if inherited else Path(context.project_root)
        return context, policy, owner / "zf.yaml", inherited

    @router.get("/api/projects/{project_id}/settings/issue")
    def get_settings(project_id: str, request: Request):
        error = auth(request)
        if error:
            return JSONResponse({k: v for k, v in error.items() if k != "_status_code"}, status_code=error.get("_status_code", 403))
        try:
            context, policy, path, inherited = target(project_id)
            data = policy_projection(path, policy, developer=is_developer(request), inherited=inherited)
            data["feishu"] = read_issue_merge_settings(context.project_root)
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except (OSError, ValueError, ConfigError, yaml.YAMLError) as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=422)

    @router.patch("/api/projects/{project_id}/settings/issue")
    async def save_settings(project_id: str, request: Request):
        if not same_origin(request):
            return JSONResponse({"ok": False, "reason": "Cross-origin settings mutation rejected."}, status_code=403)
        error = auth(request)
        if error:
            return JSONResponse({k: v for k, v in error.items() if k != "_status_code"}, status_code=error.get("_status_code", 403))
        raw = await request.body()
        if len(raw) > 500_000:
            return JSONResponse({"ok": False, "reason": "Settings request too large."}, status_code=413)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("A settings object is required.")
            context, policy, path, inherited = target(project_id)
            if inherited and payload.get("confirm_workspace_scope") is not True:
                raise ValueError("Confirm that this change affects the workspace policy.")
            result = save_issue_policy(path, payload, developer=is_developer(request))
            if not result["ok"]:
                return JSONResponse(result, status_code=409)
            # Configuration audit is a real event, never a synthetic task state.
            EventWriter(event_log_from_project(context.state_dir, config=context.config)).emit(
                "settings.issue.updated", actor="web-settings", payload={
                    "project_id": project_id, "changed_fields": result["changed_fields"],
                    "scope": "workspace" if inherited else "project", "restart_required": result["restart_required"],
                })
            return JSONResponse({**policy_projection(path, policy, developer=is_developer(request), inherited=inherited), **result}, headers={"Cache-Control": "no-store"})
        except PermissionError as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=403)
        except (OSError, ValueError, TypeError, ConfigError, yaml.YAMLError) as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=422)

    @router.get("/api/projects/{project_id}/settings/issue-execution")
    @router.patch("/api/projects/{project_id}/settings/issue-execution")
    async def issue_execution_settings(project_id: str, request: Request):
        from zf.runtime.issue_execution_settings import execution_settings, save_execution_settings
        if request.method == "PATCH" and not same_origin(request):
            return JSONResponse({"ok": False, "reason": "Cross-origin settings mutation rejected."}, status_code=403)
        if not is_developer(request):
            return JSONResponse({"ok": False, "reason": "Developer access required"}, status_code=403)
        try:
            context = resolve_ctx(project_id)
            if request.method == "GET":
                result = execution_settings(context.project_root, context.config)
            else:
                raw = await request.body()
                if len(raw) > 16_384:
                    return JSONResponse({"ok": False, "reason": "Settings request too large"}, status_code=413)
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("A settings object is required")
                result = save_execution_settings(context.project_root, context.config, payload)
                if result["ok"]:
                    EventWriter(event_log_from_project(context.state_dir, config=context.config)).emit(
                        "settings.issue.updated", actor="web-settings", payload={
                            "project_id": project_id, "changed_fields": ["execution_project_root"],
                            "scope": "project", "restart_required": False,
                        })
            return JSONResponse(result, status_code=200 if result["ok"] else 409, headers={"Cache-Control": "no-store"})
        except (OSError, ValueError, TypeError, ConfigError, yaml.YAMLError) as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=422)

    @router.get("/api/projects/{project_id}/settings/feishu-issue-merge")
    def get_feishu_issue_merge_settings(project_id: str, request: Request):
        error = auth(request)
        if error:
            return JSONResponse({k: v for k, v in error.items() if k != "_status_code"}, status_code=error.get("_status_code", 403))
        try:
            context = resolve_ctx(project_id)
            return JSONResponse(read_issue_merge_settings(context.project_root), headers={"Cache-Control": "no-store"})
        except (OSError, ValueError, ConfigError) as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=422)

    @router.patch("/api/projects/{project_id}/settings/feishu-issue-merge")
    async def save_feishu_issue_merge_settings(project_id: str, request: Request):
        if not same_origin(request):
            return JSONResponse({"ok": False, "reason": "Cross-origin settings mutation rejected."}, status_code=403)
        error = auth(request)
        if error:
            return JSONResponse({k: v for k, v in error.items() if k != "_status_code"}, status_code=error.get("_status_code", 403))
        raw = await request.body()
        if len(raw) > 100_000:
            return JSONResponse({"ok": False, "reason": "Settings request too large."}, status_code=413)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("A settings object is required.")
            context = resolve_ctx(project_id)
            result = save_issue_merge_settings(context.project_root, payload)
            if not result.get("ok"):
                return JSONResponse(result, status_code=409)
            EventWriter(event_log_from_project(context.state_dir, config=context.config)).emit(
                "settings.feishu_issue_merge.updated", actor="web-settings", payload={
                    "project_id": project_id,
                    "changed_fields": result.get("changed_fields", []),
                    "restart_required": True,
                })
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PermissionError as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=403)
        except (OSError, ValueError, TypeError, ConfigError) as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=422)

    @router.post("/api/projects/{project_id}/settings/feishu-issue-merge/test")
    def test_feishu_issue_merge_settings(project_id: str, request: Request):
        if not same_origin(request):
            return JSONResponse({"ok": False, "reason": "Cross-origin settings mutation rejected."}, status_code=403)
        error = auth(request)
        if error:
            return JSONResponse({k: v for k, v in error.items() if k != "_status_code"}, status_code=error.get("_status_code", 403))
        try:
            context = resolve_ctx(project_id)
            result = test_issue_merge_connection(context.project_root)
            return JSONResponse(result, status_code=200 if result.get("ok") else 422, headers={"Cache-Control": "no-store"})
        except (OSError, ValueError, ConfigError) as exc:
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=422)
    return router
