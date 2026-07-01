"""FastAPI app for zaofu local Web dashboard (F-WEB-MVP-01).

Endpoints:
  GET /                         → React/Vite dist when built, else static/index.html
  GET /assets/<file>            → React/Vite built assets when present
  GET /static/<file>            → static/<file>
  GET /api/state                → kanban + features + cost + workers snapshot
  GET /api/snapshot             → React workbench snapshot with seq/project/runtime
  GET /api/views/tasks          → TaskView[] (Feishu projection reused)
  GET /api/views/workers        → Agent View worker/session cockpit projection
  GET /api/views/recent         → recent N event payloads
  GET /api/progress             → progress.md raw markdown
  GET /api/instructions/{role}  → role.md (read-only)
  GET /api/briefings/{name}     → briefing markdown by filename
  GET /api/cost                 → per-role + total cost
  GET /api/stream               → SSE tail of events.jsonl

State is read fresh on every request (no in-memory caching). zaofu's
write volume is low enough that re-reading kanban.json / events.jsonl
on each call is fine and avoids cache invalidation bugs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from zf.core.config.schema import ZfConfig
from zf.core.config.project_context import ProjectContext, resolve_project_context
from zf.core.cost.tracker import CostTracker
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.safety import PathGuard
from zf.core.security.redaction import redact_event, redact_obj
from zf.core.feature.store import FeatureStore
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.skills.provenance import LOCKFILE_NAME, read_skill_metadata, resolve_skill
from zf.core.task.lifecycle import derive_phase
from zf.core.task.kanban_projection import (
    kanban_column_projection,
    workflow_projection,
)
from zf.core.task.schema import Task, TaskContract, TaskEvidence
from zf.core.task.store import TaskStore
from zf.core.trace.diagnostics import _safe_trace_id
from zf.integrations.feishu.views import TaskView
from zf.runtime.run_archive import (
    RunArchiveError,
    RunProjector,
    read_run_detail,
    read_run_events,
    read_task_runs,
    validate_run_id,
)
from zf.autoresearch.projection import project_autoresearch_state
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.channel_contracts import (
    CHANNEL_DISCUSSION_MODES,
    validate_channel_member_contract,
)
from zf.runtime.agent_live import project_agent_live
from zf.runtime.assignment_route import project_assignment_routes
from zf.runtime.automation_projection import AUTOMATIONS, project_automations
from zf.runtime.execution_route import (
    project_execution_route,
    project_route_summary,
)
from zf.runtime.gate_projection import project_gate_projection
from zf.runtime.hook_registry import project_hook_registry
from zf.runtime.kanban_agent_summary import project_kanban_agent_summary
from zf.runtime.operator_reliability import (
    project_agent_cockpit,
    project_mutation_audit,
    project_recovery_catalog,
    project_worktree_drift_audit,
)
from zf.runtime.pause_lifecycle import project_pause_lifecycle
from zf.runtime.provider_capabilities import (
    project_provider_capabilities,
    provider_capability_for_backend,
)
from zf.runtime.project_spine_review import project_spine_review_insight
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.runtime.provider_permissions import emit_provider_permission_snapshot
from zf.core.workspace import (
    ProjectInitializer,
    ProjectResolver,
    RuntimeManager,
    WorkspaceProject,
    WorkspaceRegistry,
    project_lifecycle,
    stable_project_id,
)
from zf.runtime.workdirs import WorkdirManager
from zf.web.operator_contract import (
    KANBAN_AGENT_ALLOWED_ACTIONS,
    KANBAN_AGENT_CAPABILITIES,
    KANBAN_AGENT_FORBIDDEN_CAPABILITIES,
    kanban_agent_boundary,
    kanban_agent_evidence_model,
    kanban_agent_shared_context,
    kanban_agent_status_model,
)
from zf.web.headless_agent import HeadlessMessage, KanbanHeadlessAgent, canonical_headless_backend
from zf.web.agent_session_runtime import (
    begin_agent_session_run,
    cancel_agent_session_run,
    run_key,
)
from zf.web.operator_session import OperatorSessionManager
from zf.web.perf import (
    record_timing,
    response_size_from_headers,
    route_pattern,
    should_record_path,
    summarize_timings,
    timing_log_path,
)


_STATIC_DIR = Path(__file__).parent / "static"
# --- P1 seam 1: moved read-side projections (re-exported verbatim) ----
from zf.web.projections.common import (  # noqa: F401
    _GitResult,
    _resolve_project_root_for_state,
    _snapshot_cache_seconds,
    _default_project_id,
    _default_workspace_project,
    _active_workspace_project_id,
    _no_default_project_payload,
    _artifact_ref_warnings_from_events,
    _first_artifact_ref_path,
    _deep_kanban_enabled,
    _cost,
    _empty_queue_role_summary,
    _age_seconds,
    _parent_role_from_instance,
    _instance_origin,
    _attention_state_needs_operator,
    _clear_context_attention,
    _derive_lifecycle_state,
    _derive_attention_state,
    _allowed_worker_actions,
    _attention_from_provider_stop_reason,
    _payload_first_string,
    _payload_context_ratio,
    _briefing_paths_for_instance,
    _display_path,
    _canonical_operator_backend,
    _action_payload,
    _payload_hash,
    _message_allows_create_task_proposal,
    _message_allows_idea_to_product_proposal,
    _is_lifecycle_probe_request,
    _emit_action_completed,
    _action_failed,
    _optional_str,
    _string_list,
    _read_jsonl_dicts,
    _append_jsonl,
    _raw_event_has_task_id,
    _cost_by_instance,
    _empty_cost_summary,
    _git,
    _git_branch_or_ref,
    _git_commit,
    _git_dirty,
    _payload_ref,
    _payload_mentions,
    _first_nonempty,
    _read_json_file,
    _sha256_file,
    _truthy,
    _positive_int,
    _is_failed_event,
    _is_blocked_event,
    _parse_search_query,
    _matches_task_filters,
    _matches_event_filters,
    _read_events_with_seq,
    _line_count,
)
from zf.web.projections.request_util import (  # noqa: F401
    _request_json,
    _idempotency_path,
    _reserve_idempotency_key,
    _complete_idempotency_key,
    _web_passcode_configured,
    _request_client_id,
    _web_unlock_rate_limit,
    _web_trusted_session_enabled,
    _web_trusted_session_nonloopback_override,
    _bearer_token,
    _sse_event,
    _sse_gap,
    _parse_cursor,
)
from zf.web.projections.summaries import (  # noqa: F401
    _safe_snapshot_projection,
    _provider_health_projection,
    _safe_task_progress_projection,
    _safe_task_capsule_projection,
    _safe_task_operations_projection,
    _safe_task_run_panel_projection,
    _safe_handoff_summary_projection,
    _metrics_snapshot_projection,
    _features,
    _delivery_features,
    _feature_index,
    _delivery_feature_title,
    _delivery_feature_statuses,
    _archive_tasks,
    _safe_run_dir,
    _safe_session_segment,
    _skills,
    _safe_token,
    _refs_from_events,
)
from zf.web.projections.events import (  # noqa: F401
    _EVENT_LOG_RUN_ID,
    _trace_detail,
    _fleet_stats_projection,
    _event_log_run_summary,
    _traces,
    _event_signal_summary,
    _events_page,
    _diagnostics,
    _search,
    _events_with_seq,
    _event_log_fingerprint,
    _events_with_exact_task_id,
    _trace_id_from_events,
    _event_to_dict,
    _last_event_by_actor,
    _active_task_by_instance,
    _stage_summary,
    _recent_events,
    _tail_events,
)
from zf.web.projections.workflow_graph import (  # noqa: F401
    _workflow_judge_configured,
    _workflow_terminal_success_event,
    _workflow_stage,
    _workflow_graph,
)
from zf.web.projections.operator import (  # noqa: F401
    _operator_skills_available,
    _operator_task_evidence,
    _operator_backend_options,
    _operator_backend_capabilities,
    _default_operator_backend,
    _operator_backend_available,
    _operator_backend_command_available,
    _operator_session_id,
    _operator_action_command,
)
from zf.web.projections.runs import (  # noqa: F401
    _runtime_snapshots,
    _run_projector,
    _runs_index,
    _active_runs,
    _run_detail,
    _run_events,
    _run_scorecard,
    _run_fanouts,
    _runtime_instance_retired,
)
from zf.web.projections.agents import (  # noqa: F401
    _NEXT_EVENT_BY_TYPE,
    _ORCHESTRATOR_ATTENTION_EVENTS,
    _workers,
    _agent_view_queue_projection,
    _agent_view_role_capacity,
    _agent_view_waiting_role,
    _agent_view_next_expected_event,
    _roles,
    _agent_classification,
    _worker_signal_index,
    _agent_debug_projection,
    _workdirs,
    _role_sessions_data,
    _role_session_meta,
    _role_session_ids,
    _worker_states,
    _workdir_for_instance,
    _workdir_projection,
)
from zf.web.projections.fanouts import (  # noqa: F401
    _candidate_detail,
    _fanout_detail,
    _fanout_child_projection,
    _fanout_progress,
    _fanout_trigger_projection,
    _candidates,
    _candidate_manifest,
    _fanout_manifest,
    _fanouts,
    _requested_fanout_id,
)
from zf.web.projections.tasks import (  # noqa: F401
    _TASK_TIMELINE_CACHE,
    _TASK_TIMELINE_CACHE_MAX,
    _task_counts,
    _task_detail,
    _task_timeline,
    _task_artifact_refs,
    _task_diff,
    _kanban,
    _kanban_column,
    _task_source_from_events,
    _task_evidence_badges,
    _task_fanout_projection,
    _task_runs,
    _task_id_from_payload,
    _task_contract_from_payload,
    _task_updates_from_payload,
    _task_metadata_payload,
    _task_priority,
    _task_evidence_from_payload,
    _ship_blockers,
    _task_events_with_seq,
    _task_briefing,
    _task_index_with_archive,
    _task_views,
)
from zf.web.projections.workspace import (  # noqa: F401
    _workspace_project_payload,
    _workspace_projects_payload,
    _resolve_api_project,
    _project_initialized,
    _ensure_project_initialized,
    _project_uninitialized_payload,
    _workspace_channel_summary,
    _workspace_automation_summary,
    _project_action_envelope,
    _projection_reply_if_requested,
)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REACT_DIST_DIR = _REPO_ROOT / "web" / "dist"
_HUMAN_DECISION_ACTIONS = {
    "human-decision-approve-controlled-action",
    "human-decision-request-autoresearch",
    "human-decision-safe-halt",
}
_HUMAN_DECISION_BY_ACTION = {
    "human-decision-approve-controlled-action": "approve_controlled_action",
    "human-decision-request-autoresearch": "request_autoresearch",
    "human-decision-safe-halt": "safe_halt",
}
_ACTION_ALIASES = {
    "dispatch",
    "dispatch-task",
    "rerun-verify",
    "request-verify",
    "rerun-task",
    "request-review",
    "rebuild-candidate",
    "ship",
    "ship-candidate",
    "suspend",
    "pause-agent",
    "resume",
    "resume-agent",
    "mark-blocked",
    "chat-orchestrator",
    "operator-intent-create",
    "operator.intent.create",
    "operator-intent-approve",
    "operator.intent.approve",
    "operator-intent-reject",
    "operator.intent.reject",
    "replan-approve",
    "replan.approve",
    "replan-defer",
    "replan.defer",
    "replan-reject",
    "replan.reject",
    "plan-approve",
    "plan.approve",
    "plan-reject",
    "plan.reject",
    "human-decision-approve-controlled-action",
    "human-decision-request-autoresearch",
    "human-decision-safe-halt",
    "agent-session-cancel",
    "cancel-agent-session",
    "agent.session.cancel",
    "start-collaboration",
    "request-fanout",
    "start-operator-session",
    "create-task",
    "capture-regression-case",
    "replay-regression-case",
    "update-task",
    "decompose-feature",
    "link-evidence",
    "archive-task",
    "cleanup-workdir",
    "worker-reply",
    "reply-worker",
    "worker-respawn",
    "respawn-worker",
    "worker-drain",
    "drain-worker",
    "channel-create",
    "channel.create",
    "channel-new",
    "channel-post-message",
    "channel-invite-member",
    "channel.add_member",
    "channel-add-member",
    "channel-update-member-permission",
    "channel.member.permission",
    "channel.member.permission.update",
    "channel-remove-member",
    "channel.member.remove",
    "channel-remove-agent",
    "channel-delete",
    "channel.delete",
    "channel-clear-history",
    "channel.history.clear",
    "channel-synthesis",
    "channel-synthesis-request",
    "channel.synthesis.request",
    "channel-drain-replies",
    "channel-mark-read",
    "channel.mark_read",
    "channel-handoff",
    "channel.handoff",
    "channel-discussion-mode",
    "channel.discussion_mode",
    "channel-owner-report",
    "channel.owner_report.request",
    "channel-owner-report-request",
    "workflow-invoke",
    "workflow.invoke",
    "workflow-batch-resume",
    "workflow.batch.resume",
    "candidate-rework-apply",
    "candidate.rework.apply",
    "idea-to-product",
    "idea.to_product",
    "productize-idea",
    "assignment-propose",
    "assignment.propose",
    "assignment-intent",
    "automation-run",
    "automation.run",
    "automation.run.manual",
    "run-automation",
    "maintenance-prepare",
    "maintenance.prepare",
    "maintenance_prepare",
    "attention-ack",
    "attention.ack",
    "attention-snooze",
    "attention.snooze",
    "attention-resolve",
    "attention.resolve",
    "attention-feedback",
    "attention.feedback",
    "attention-escalate",
    "attention.escalate",
    "provider-dev-chat-start",
    "provider.dev_chat.start",
    "provider-dev-chat-send",
    "provider.dev_chat.send",
    "provider-dev-chat-stop",
    "provider.dev_chat.stop",
    "workflow-config-propose",
    "workflow.config.propose",
    "workflow-config-validate",
    "workflow.config.validate",
    "workflow-config-apply",
    "workflow.config.apply",
    "runtime-stop",
    "runtime.stop",
    "runtime-restart",
    "runtime.restart",
    "runtime-resume",
    "runtime.resume",
}
_CANONICAL_ACTIONS = {
    "dispatch": "dispatch-task",
    "rerun-verify": "request-verify",
    "ship": "ship-candidate",
    "suspend": "pause-agent",
    "resume": "resume-agent",
    "create-issue": "create-task",
    "update-issue": "update-task",
    "reply-worker": "worker-reply",
    "respawn-worker": "worker-respawn",
    "drain-worker": "worker-drain",
    "channel.create": "channel-create",
    "channel-new": "channel-create",
    "channel.add_member": "channel-invite-member",
    "channel-add-member": "channel-invite-member",
    "channel.member.permission": "channel-update-member-permission",
    "channel.member.permission.update": "channel-update-member-permission",
    "channel.member.remove": "channel-remove-member",
    "channel-remove-agent": "channel-remove-member",
    "channel.delete": "channel-delete",
    "channel.history.clear": "channel-clear-history",
    "channel.synthesis.request": "channel-synthesis-request",
    "channel.mark_read": "channel-mark-read",
    "channel.handoff": "channel-handoff",
    "channel.discussion_mode": "channel-discussion-mode",
    "channel.owner_report.request": "channel-owner-report",
    "channel-owner-report-request": "channel-owner-report",
    "cancel-agent-session": "agent-session-cancel",
    "agent.session.cancel": "agent-session-cancel",
    "assignment.propose": "assignment-propose",
    "assignment-intent": "assignment-propose",
    "automation.run": "automation-run",
    "automation.run.manual": "automation-run",
    "run-automation": "automation-run",
    "maintenance.prepare": "maintenance-prepare",
    "maintenance_prepare": "maintenance-prepare",
    "attention.ack": "attention-ack",
    "attention.snooze": "attention-snooze",
    "attention.resolve": "attention-resolve",
    "attention.feedback": "attention-feedback",
    "attention.escalate": "attention-escalate",
    "operator.intent.create": "operator-intent-create",
    "operator.intent.approve": "operator-intent-approve",
    "operator.intent.reject": "operator-intent-reject",
    "replan.approve": "replan-approve",
    "replan.defer": "replan-defer",
    "replan.reject": "replan-reject",
    "plan.approve": "plan-approve",
    "plan.reject": "plan-reject",
    "workflow.invoke": "workflow-invoke",
    "workflow.batch.resume": "workflow-batch-resume",
    "candidate.rework.apply": "candidate-rework-apply",
    "idea.to_product": "idea-to-product",
    "productize-idea": "idea-to-product",
    "provider.dev_chat.start": "provider-dev-chat-start",
    "provider.dev_chat.send": "provider-dev-chat-send",
    "provider.dev_chat.stop": "provider-dev-chat-stop",
    "workflow.config.propose": "workflow-config-propose",
    "workflow.config.validate": "workflow-config-validate",
    "workflow.config.apply": "workflow-config-apply",
    "runtime.stop": "runtime-stop",
    "runtime.restart": "runtime-restart",
    "runtime.resume": "runtime-resume",
}
_ALLOWED_WEB_ACTIONS = set(_ACTION_ALIASES)
_CHANNEL_DISCUSSION_MODES = CHANNEL_DISCUSSION_MODES
_PROJECT_OPERATOR_CONTROLLED_ACTIONS = {
    "operator-intent-create",
    "operator-intent-approve",
    "operator-intent-reject",
    "replan-approve",
    "replan-defer",
    "replan-reject",
    "plan-approve",
    "plan-reject",
    "workflow-batch-resume",
    "candidate-rework-apply",
    "idea-to-product",
    "provider-dev-chat-start",
    "provider-dev-chat-send",
    "provider-dev-chat-stop",
    "workflow-config-propose",
    "workflow-config-validate",
    "workflow-config-apply",
    "runtime-stop",
    "runtime-restart",
    "runtime-resume",
}
_OPERATOR_MANAGERS: dict[str, OperatorSessionManager] = {}
_WEB_SESSION_COOKIE = "zf_web_session"
_WEB_SESSIONS: dict[str, float] = {}
_WEB_UNLOCK_FAILURES: dict[str, list[float]] = {}




def create_app(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    *,
    default_project_enabled: bool = True,
) -> FastAPI:
    """Build a FastAPI app reading state from ``state_dir`` (.zf path).

    Factory pattern keeps the app stateless across tests — each test
    gets a fresh app pointing at its own tmp .zf.
    """
    app = FastAPI(title="zaofu dashboard", version="0.1.0")
    state_dir = Path(state_dir).resolve()
    project_root = _resolve_project_root_for_state(state_dir, project_root)
    default_project_id = (
        _default_project_id(config=config, project_root=project_root)
        if default_project_enabled else ""
    )
    default_project_opened_at = datetime.now(timezone.utc).isoformat()

    react_dist = _react_dist_dir()
    react_assets = react_dist / "assets" if react_dist is not None else None

    if react_assets is not None and react_assets.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=react_assets),
            name="react-assets",
        )

    if _STATIC_DIR.exists():
        app.mount(
            "/static", StaticFiles(directory=_STATIC_DIR), name="static",
        )

    snapshot_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    snapshot_lock = threading.Lock()

    @app.middleware("http")
    async def web_api_timing_middleware(request: Request, call_next):
        path = request.url.path
        if not should_record_path(path):
            return await call_next(request)
        started = time.perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500) or 500)
            return response
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            record_timing(
                state_dir,
                method=request.method,
                path=path,
                route=route_pattern(request),
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                response_bytes=response_size_from_headers(response.headers) if response is not None else None,
            )

    # ---- HTML root ----

    @app.get("/")
    def root() -> FileResponse:
        index = _ui_index()
        if not index.exists():
            raise HTTPException(500, "web UI index missing")
        return FileResponse(index, media_type="text/html")

    # ---- Snapshot endpoints ----

    @app.get("/api/state")
    def state() -> JSONResponse:
        if not default_project_id:
            return JSONResponse(_no_default_project_payload(), status_code=409)
        return JSONResponse({
            "tasks": _kanban(state_dir, config=config),
            "archive_tasks": _archive_tasks(state_dir, include_active=False),
            "features": _features(state_dir),
            "delivery_features": _delivery_features(state_dir),
            "cost": _cost(state_dir),
            "workers": _workers(state_dir, config=config),
            "state_dir": str(state_dir),
        })

    @app.get("/api/snapshot")
    def snapshot(request: Request) -> JSONResponse:
        if not default_project_id:
            return JSONResponse(_no_default_project_payload(), status_code=409)
        if not _project_initialized(state_dir):
            return JSONResponse(
                _project_uninitialized_payload(
                    project_id=default_project_id,
                    state_dir=state_dir,
                    project_root=project_root,
                ),
                status_code=409,
            )
        web_session_token = _web_session_cookie(request)
        cache_key = web_session_token or ""
        ttl = _snapshot_cache_seconds()
        with snapshot_lock:
            cached = snapshot_cache.get(cache_key)
            now = time.monotonic()
            if cached is not None and now - cached[0] <= ttl:
                return JSONResponse(cached[1])
            data = _snapshot(
                state_dir,
                config=config,
                project_root=project_root,
                web_session_token=web_session_token,
            )
            snapshot_cache[cache_key] = (time.monotonic(), data)
            return JSONResponse(data)

    @app.get("/api/snapshot/light")
    def snapshot_light(request: Request) -> JSONResponse:
        if not default_project_id:
            return JSONResponse(_no_default_project_payload(), status_code=409)
        if not _project_initialized(state_dir):
            return JSONResponse(
                _project_uninitialized_payload(
                    project_id=default_project_id,
                    state_dir=state_dir,
                    project_root=project_root,
                ),
                status_code=409,
            )
        return JSONResponse(_snapshot_slice(
            state_dir,
            slice_name="light",
            config=config,
            project_root=project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/kanban-agent/summary")
    def kanban_agent_summary() -> JSONResponse:
        if not default_project_id:
            return JSONResponse(_no_default_project_payload(), status_code=409)
        if not _project_initialized(state_dir):
            return JSONResponse(
                _project_uninitialized_payload(
                    project_id=default_project_id,
                    state_dir=state_dir,
                    project_root=project_root,
                ),
                status_code=409,
            )
        return JSONResponse(project_kanban_agent_summary(
            state_dir,
            config=config,
            project_root=project_root,
            project_id=default_project_id,
        ))

    @app.get("/api/web/perf/summary")
    def web_perf_summary(limit: int = 2000) -> JSONResponse:
        return JSONResponse(summarize_timings(state_dir, limit=limit))

    @app.get("/api/workspace/projects")
    def workspace_projects() -> JSONResponse:
        return JSONResponse(_workspace_projects_payload(
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
            default_project_opened_at=default_project_opened_at,
        ))

    @app.get("/api/workspace/overview")
    def workspace_overview() -> JSONResponse:
        return JSONResponse(_workspace_overview_payload(
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
            default_project_opened_at=default_project_opened_at,
        ))

    @app.post("/api/workspace/projects/validate-path")
    async def workspace_validate_path(request: Request) -> JSONResponse:
        payload = await _request_json(request)
        raw_root = str(payload.get("root") or "").strip()
        if not raw_root:
            return JSONResponse({"ok": False, "status": "invalid", "reason": "root is required"}, status_code=422)
        root = Path(raw_root).expanduser()
        exists = root.exists()
        return JSONResponse({
            "ok": exists,
            "status": "valid" if exists else "missing",
            "root": str(root.resolve()) if exists else str(root),
            "config_path": str((root / "zf.yaml").resolve()) if exists else str(root / "zf.yaml"),
            "has_config": bool((root / "zf.yaml").exists()) if exists else False,
        })

    @app.post("/api/workspace/projects/register")
    async def workspace_register_project(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth_error = _web_mutation_auth_error(
            "workspace-project-register",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)
        payload = await _request_json(request)
        root = Path(str(payload.get("root") or "")).expanduser()
        workspace = str(payload.get("workspace") or "default")
        display_name = str(
            payload.get("display_name")
            or payload.get("name")
            or ""
        ).strip()
        try:
            context = resolve_project_context(cwd=root, require_config=True)
            project = WorkspaceRegistry(workspace=workspace).upsert_context(
                context,
                display_name=display_name,
            )
        except Exception as exc:
            return JSONResponse({
                "ok": False,
                "status": "invalid_project",
                "reason": str(exc),
            }, status_code=422)
        return JSONResponse({
            "ok": True,
            "status": "registered",
            "project": _workspace_project_payload(project),
        })

    def _apply_profile_overlay(
        root: Path,
        *,
        stack: str = "",
        surface: str = "",
        scale: str = "",
        scaffold: bool = False,
        intent: str = "build",
    ) -> dict:
        """Post-init stack overlay (doc 102 §4.3): no-clobber required_checks +
        AGENTS.md stack section, + optional from-0 scaffold. When a stack is
        declared (greenfield survey) use declared_profile instead of detection.
        Token gate already passed upstream."""
        from zf.core.profile.apply import (
            apply_agents_md_stack,
            fill_required_checks,
            scaffold_from_zero,
        )
        from zf.core.profile.detector import declared_profile, detect
        from zf.core.profile.recommender import recommend

        profile = declared_profile(stack, surface) if stack else detect(root)
        rec = recommend(profile, intent, declared=bool(stack), scale=scale or None)
        out: dict = {"archetype": rec.archetype, "harness_profile": rec.harness_profile,
                     "languages": list(profile.languages)}
        zf_yaml = root / "zf.yaml"
        if zf_yaml.exists():
            out["required_checks"] = fill_required_checks(
                zf_yaml, rec.required_checks, write=True)
        agents = root / "AGENTS.md"
        if agents.exists():
            out["agents_md"] = apply_agents_md_stack(agents, profile, write=True)["action"]
        if scaffold:
            out["scaffold"] = scaffold_from_zero(root, profile, write=True)["created"]
        return out

    @app.post("/api/workspace/projects/init")
    async def workspace_init_project(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth_error = _web_mutation_auth_error(
            "workspace-project-init",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)
        payload = await _request_json(request)
        root = Path(str(payload.get("root") or "")).expanduser()
        preset_arg = str(payload.get("preset") or "") or None
        # A validated prod flow archetype → write its yaml directly, skip preset gen.
        if preset_arg:
            from zf.core.profile.flows import is_flow_id, read_flow_yaml
            if is_flow_id(preset_arg):
                flow_text = read_flow_yaml(preset_arg)
                if flow_text is not None:
                    root.mkdir(parents=True, exist_ok=True)
                    (root / "zf.yaml").write_text(flow_text, encoding="utf-8")
                preset_arg = None
        try:
            result = ProjectInitializer(
                workspace=str(payload.get("workspace") or "default"),
            ).initialize(
                cwd=root,
                explicit_state_dir=payload.get("state_dir"),
                force=bool(payload.get("force")),
                preset=preset_arg,
                with_instruction_docs=not bool(payload.get("skip_instruction_docs")),
                workspace_register=True,
                create_root=True,
            )
        except Exception as exc:
            return JSONResponse({
                "ok": False,
                "status": "init_failed",
                "reason": str(exc),
            }, status_code=422)
        profile_applied = None
        if bool(payload.get("apply_profile")):
            profile_applied = _apply_profile_overlay(
                root,
                stack=str(payload.get("stack") or ""),
                surface=str(payload.get("surface") or ""),
                scale=str(payload.get("scale") or ""),
                scaffold=bool(payload.get("scaffold")),
                intent=str(payload.get("intent") or "build"),
            )
        notes_written = None
        description = str(payload.get("description") or "").strip()
        if description:
            from zf.core.profile.apply import apply_project_notes
            notes_written = apply_project_notes(
                root / "CLAUDE.md", description, write=True)["action"]
        return JSONResponse({
            "ok": True,
            "status": "initialized",
            "state_dir": str(result.state_dir),
            "instruction_docs": {
                "created": list(result.instruction_docs.created),
                "updated": list(result.instruction_docs.updated),
                "skipped": list(result.instruction_docs.skipped),
            },
            "profile": profile_applied,
            "notes": notes_written,
            "project": (
                _workspace_project_payload(result.registered_project)
                if result.registered_project is not None else None
            ),
        }, status_code=201)

    @app.post("/api/workspace/projects/{project_id}/touch")
    def workspace_touch_project(project_id: str) -> JSONResponse:
        touched = WorkspaceRegistry().touch(project_id)
        if touched is None and project_id == default_project_id and default_project_id:
            registry = WorkspaceRegistry()
            registry.upsert(
                _default_workspace_project(
                    project_id=default_project_id,
                    state_dir=state_dir,
                    config=config,
                    project_root=project_root,
                )
            )
            touched = registry.touch(project_id)
        if touched is None:
            raise HTTPException(404, f"project {project_id!r} is not registered")
        return JSONResponse({
            "ok": True,
            "status": "touched",
            "project": _workspace_project_payload(touched),
        })

    @app.delete("/api/workspace/projects/{project_id}")
    def workspace_remove_project(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth_error = _web_mutation_auth_error(
            "workspace-project-remove",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)
        # The server's own default project is re-injected into every project
        # listing (see _workspace_projects_payload), so removing it from the
        # registry is a silent no-op. Reject it honestly instead.
        if default_project_id and project_id in {"default", default_project_id}:
            return JSONResponse({
                "ok": False,
                "status": "server_default",
                "project_id": project_id,
                "reason": (
                    "cannot remove the project the server was started with; "
                    "restart `zf web` (or use --workspace-only) to drop it"
                ),
            }, status_code=409)
        removed = WorkspaceRegistry().remove(project_id)
        return JSONResponse({
            "ok": removed,
            "status": "removed" if removed else "not_found",
            "project_id": project_id,
        }, status_code=200 if removed else 404)

    @app.get("/api/projects/{project_id}/snapshot")
    def project_snapshot(project_id: str, request: Request) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_snapshot(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/projects/{project_id}/web/perf/summary")
    def project_web_perf_summary(project_id: str, limit: int = 2000) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        summary_state_dir = context.state_dir if timing_log_path(context.state_dir).exists() else state_dir
        return JSONResponse(summarize_timings(
            summary_state_dir,
            project_id="" if summary_state_dir == context.state_dir else project_id,
            limit=limit,
        ))

    @app.get("/api/projects/{project_id}/snapshot/light")
    def project_snapshot_light(project_id: str, request: Request) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_snapshot_slice(
            context.state_dir,
            slice_name="light",
            config=context.config,
            project_root=context.project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/projects/{project_id}/snapshot/board")
    def project_snapshot_board(project_id: str, request: Request) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_snapshot_slice(
            context.state_dir,
            slice_name="board",
            config=context.config,
            project_root=context.project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/projects/{project_id}/snapshot/runtime")
    def project_snapshot_runtime(project_id: str, request: Request) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_snapshot_slice(
            context.state_dir,
            slice_name="runtime",
            config=context.config,
            project_root=context.project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/projects/{project_id}/snapshot/observability")
    def project_snapshot_observability(project_id: str, request: Request) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_snapshot_slice(
            context.state_dir,
            slice_name="observability",
            config=context.config,
            project_root=context.project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/projects/{project_id}/delivery-features")
    def project_delivery_features(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse({
            "delivery_features": _safe_snapshot_projection(
                "delivery_features", [], lambda: _delivery_features(context.state_dir)
            ),
            "features": _safe_snapshot_projection(
                "features", [], lambda: _features(context.state_dir)
            ),
        })

    @app.get("/api/projects/{project_id}/kanban-agent/summary")
    def project_kanban_agent_summary_api(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_kanban_agent_summary(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
            project_id=project_id,
        ))

    @app.get("/api/projects/{project_id}/tasks/{task_id}")
    def project_task_detail(project_id: str, task_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        detail = _task_detail(
            context.state_dir,
            task_id,
            config=context.config,
            project_root=context.project_root,
        )
        if detail is None:
            raise HTTPException(404, f"task {task_id!r} not found")
        return JSONResponse(detail)

    @app.get("/api/projects/{project_id}/tasks/{task_id}/timeline")
    def project_task_timeline(
        project_id: str,
        task_id: str,
        limit: int = 200,
        deep: bool = False,
    ) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        timeline = _task_timeline(
            context.state_dir,
            task_id,
            config=context.config,
            limit=limit,
            deep=deep,
        )
        if timeline is None:
            raise HTTPException(404, f"task {task_id!r} not found")
        return JSONResponse(timeline)

    @app.get("/api/projects/{project_id}/tasks/{task_id}/diff")
    def project_task_diff(project_id: str, task_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_task_diff(
            context.state_dir,
            task_id,
            config=context.config,
            project_root=context.project_root,
        ))

    @app.get("/api/projects/{project_id}/agents")
    def project_agents(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_agents(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
        ))

    @app.get("/api/projects/{project_id}/roles")
    def project_roles(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_roles(context.state_dir, config=context.config))

    @app.get("/api/projects/{project_id}/workdirs")
    def project_workdirs(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_workdirs(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
        ))

    @app.get("/api/projects/{project_id}/integration-queue")
    def project_integration_queue(project_id: str) -> JSONResponse:
        from zf.runtime.integration_queue import read_integration_queue

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(read_integration_queue(
            context.state_dir,
            project_root=context.project_root,
        ))

    @app.get("/api/projects/{project_id}/repair-actions")
    def project_repair_actions(project_id: str) -> JSONResponse:
        from zf.runtime.repair_actions import read_repair_actions

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(read_repair_actions(context.state_dir))

    @app.get("/api/projects/{project_id}/runtime")
    def project_runtime(project_id: str, request: Request) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_runtime(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/projects/{project_id}/run-manager")
    def project_run_manager(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        from zf.runtime.run_manager import build_run_manager_projection

        return JSONResponse(build_run_manager_projection(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
        ))

    @app.get("/api/projects/{project_id}/run-goal")
    def project_run_goal(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        from zf.runtime.run_manager import build_run_goal_projection

        return JSONResponse(build_run_goal_projection(
            EventLog(context.state_dir / "events.jsonl").read_all(),
        ))

    @app.get("/api/projects/{project_id}/workflow/graph")
    def project_workflow_graph(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_workflow_graph(context.state_dir, config=context.config))

    @app.get("/api/projects/{project_id}/regression-cases")
    def project_regression_cases(project_id: str, feature_id: str = "") -> JSONResponse:
        # design 101 §8 C — list captured deterministic regression cases.
        from dataclasses import asdict as _asdict

        from zf.runtime.regression_case import list_regression_cases

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        cases = list_regression_cases(context.state_dir)
        if feature_id:
            cases = [c for c in cases if not c.feature_id or c.feature_id == feature_id]
        return JSONResponse({"cases": [_asdict(c) for c in cases]})

    @app.get("/api/projects/{project_id}/events")
    def project_events(
        project_id: str,
        limit: int = 100,
        cursor: int | None = None,
        task_id: str | None = None,
        actor: str | None = None,
        type: str | None = None,  # noqa: A002
        prefix: str | None = None,
        failed: bool = False,
        blocked: bool = False,
    ) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_events_page(
            context.state_dir,
            limit=limit,
            cursor=cursor,
            task_id=task_id,
            actor=actor,
            event_type=type,
            event_prefix=prefix,
            failed=failed,
            blocked=blocked,
            config=context.config,
        ))

    @app.get("/api/projects/{project_id}/events/{event_id}")
    def project_event_detail(project_id: str, event_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_event_detail_payload(
            context.state_dir,
            event_id,
            config=context.config,
        ))

    @app.get("/api/projects/{project_id}/agent-session/history")
    def project_agent_session_history(
        project_id: str,
        surface: str = "kanban_agent",
        thread_id: str = "",
        conversation_id: str = "",
        backend: str = "",
        task_id: str = "",
        limit: int = 160,
        before_seq: int | None = None,
    ) -> JSONResponse:
        from zf.web.projections import read_model

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        page = read_model.agent_session_history(
            context.state_dir,
            surface=surface,
            thread_id=thread_id,
            conversation_id=conversation_id,
            backend=backend,
            task_id=task_id,
            project_id=project_id,
            limit=limit,
            before_seq=before_seq,
            config=context.config,
        )
        if page is None:
            raise HTTPException(404, f"agent session history surface {surface!r} not found")
        return JSONResponse(page)

    @app.get("/api/projects/{project_id}/agent-session/raw-output")
    def project_agent_session_raw_output(
        project_id: str,
        ref: str,
        offset: int = 0,
        limit: int = 524288,
    ) -> JSONResponse:
        from zf.runtime.agent_session_output import read_agent_output_artifact

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        try:
            payload = read_agent_output_artifact(
                context.state_dir,
                ref,
                offset=offset,
                limit=limit,
            )
        except FileNotFoundError:
            raise HTTPException(404, f"raw output artifact {ref!r} not found") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(payload)

    @app.get("/api/projects/{project_id}/diagnostics/logs")
    def project_diagnostics_logs(
        project_id: str,
        limit: int = 200,
        level: str = "INFO",
        task_id: str | None = None,
        role: str | None = None,
        trace_id: str | None = None,
    ) -> JSONResponse:
        from zf.runtime.log_projection import build_log_rows

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        rows = build_log_rows(
            _events_with_seq(context.state_dir, config=context.config),
            limit=max(1, min(limit, 500)),
            level_min=level,
            task_id=task_id or "",
            role=role or "",
            trace_id=trace_id or "",
        )
        return JSONResponse({
            "schema_version": "diagnostics-logs.v1",
            "project_id": project_id,
            "rows": rows,
            "count": len(rows),
        })

    @app.get("/api/projects/{project_id}/channels")
    def project_channels(project_id: str) -> JSONResponse:
        from zf.runtime.channel_projection import project_channels as _project_channels
        from zf.web.projections import read_model

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        try:
            projected = read_model.channel_summary(
                context.state_dir,
                config=context.config,
            )
            if projected is not None:
                return JSONResponse(projected)
        except Exception:
            pass
        return JSONResponse(_project_channels(context.state_dir))

    @app.get("/api/projects/{project_id}/channels/{channel_id}")
    def project_channel_detail(project_id: str, channel_id: str) -> JSONResponse:
        from zf.runtime.channel_projection import (
            DEFAULT_CHANNEL_IDS,
            project_channel,
            project_empty_channel,
        )

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        detail = project_channel(context.state_dir, channel_id)
        if detail is None:
            if channel_id.strip().lower() in DEFAULT_CHANNEL_IDS:
                return JSONResponse(project_empty_channel(channel_id))
            raise HTTPException(404, f"channel {channel_id!r} not found")
        return JSONResponse(detail)

    @app.get("/api/projects/{project_id}/channels/{channel_id}/history/search")
    def project_channel_history_search(
        project_id: str,
        channel_id: str,
        q: str = "",
        thread_id: str = "",
        member_id: str = "",
        mention: str = "",
        limit: int = 50,
    ) -> JSONResponse:
        from zf.runtime.channel_projection import search_channel_history

        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(search_channel_history(
            context.state_dir,
            channel_id,
            q=q,
            thread_id=thread_id,
            member_id=member_id,
            mention=mention,
            limit=limit,
        ))

    @app.get("/api/projects/{project_id}/traces")
    def project_traces(project_id: str) -> JSONResponse:
        # Scoped list endpoint so the Event Traces page can fetch just the trace
        # roll-up (fast, read-model slim) instead of pulling the whole snapshot.
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        traces = _traces(context.state_dir, config=context.config)
        return JSONResponse({
            "schema_version": "traces.v1",
            "is_derived_projection": True,
            "items": traces,
            "traces": traces,
        })

    @app.get("/api/projects/{project_id}/traces/{trace_id}")
    def project_trace_detail(project_id: str, trace_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_trace_detail(context.state_dir, trace_id, config=context.config))

    @app.get("/api/projects/{project_id}/candidates/{pdd_id}")
    def project_candidate_detail(project_id: str, pdd_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_candidate_detail(context.state_dir, pdd_id, config=context.config))

    @app.get("/api/projects/{project_id}/fanouts/{fanout_id}")
    def project_fanout_detail(project_id: str, fanout_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_fanout_detail(context.state_dir, fanout_id, config=context.config))

    @app.get("/api/projects/{project_id}/runs/{run_id}")
    def project_run_detail(project_id: str, run_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_run_detail(context.state_dir, run_id))

    @app.get("/api/projects/{project_id}/search")
    def project_search(project_id: str, q: str = "", limit: int = 50) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_search(
            context.state_dir,
            q=q,
            limit=limit,
            config=context.config,
        ))

    @app.get("/api/projects/{project_id}/automations")
    def project_automation_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_automations(
            context.state_dir,
            project_id=project_id,
            project_name=(
                context.config.project.name
                if context.config is not None else context.project_root.name
            ),
        ))

    @app.get("/api/projects/{project_id}/agent-live")
    def project_agent_live_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_agent_live(context.state_dir))

    @app.get("/api/projects/{project_id}/agent-cockpit")
    def project_agent_cockpit_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        agents = _agents(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
        )
        return JSONResponse(project_agent_cockpit(context.state_dir, agents=agents))

    @app.get("/api/projects/{project_id}/assignment-routes")
    def project_assignment_routes_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_assignment_routes(context.state_dir))

    @app.get("/api/projects/{project_id}/recovery")
    def project_recovery_catalog_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_recovery_catalog(context.state_dir))

    @app.get("/api/projects/{project_id}/pause-lifecycle")
    def project_pause_lifecycle_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_pause_lifecycle(context.state_dir))

    @app.get("/api/projects/{project_id}/gate-projection")
    def project_gate_projection_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        configured_backend = _canonical_operator_backend(
            os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
            or getattr(getattr(context.config, "orchestrator", None), "backend", "")
        )
        event_log = event_log_from_project(context.state_dir, config=context.config)
        return JSONResponse(project_gate_projection(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
            operator_backends=_operator_backend_options(
                configured_backend=configured_backend,
            ),
            allowed_actions=KANBAN_AGENT_ALLOWED_ACTIONS,
            web_token_configured=_web_action_token_configured(),
            web_authorization_available=_web_action_authorization_available(),
            web_mutation_mode=_web_mutation_mode(),
            events=event_log.read_all(),
        ))

    @app.get("/api/projects/{project_id}/hook-registry")
    def project_hook_registry_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        event_log = event_log_from_project(context.state_dir, config=context.config)
        return JSONResponse(project_hook_registry(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
            events=event_log.read_all(),
        ))

    @app.get("/api/projects/{project_id}/provider-capabilities")
    def project_provider_capabilities_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        configured_backend = _canonical_operator_backend(
            os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
            or getattr(getattr(context.config, "orchestrator", None), "backend", "")
        )
        return JSONResponse(project_provider_capabilities(
            config=context.config,
            operator_backends=_operator_backend_options(
                configured_backend=configured_backend,
            ),
        ))

    @app.get("/api/projects/{project_id}/spine-review")
    def project_spine_review_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_spine_review_insight(
            context.state_dir,
            project_id=project_id,
        ))

    @app.get("/api/projects/{project_id}/mutation-audit")
    def project_mutation_audit_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_mutation_audit(context.state_dir))

    @app.get("/api/projects/{project_id}/worktree-drift")
    def project_worktree_drift_page(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(project_worktree_drift_audit(context.state_dir))

    @app.get("/api/projects/{project_id}/operator/session")
    def project_operator_session(project_id: str) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return JSONResponse(_operator_session_status(
            context.state_dir,
            project_root=context.project_root,
        ))

    @app.get("/api/projects/{project_id}/operator/output")
    def project_operator_output(
        project_id: str,
        cursor: int = 0,
        limit: int = 200,
    ) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        manager = _operator_session_manager(
            context.state_dir,
            project_root=context.project_root,
        )
        return JSONResponse(redact_obj(
            manager.output_since(cursor=max(cursor, 0), limit=limit),
        ))

    @app.get("/api/projects/{project_id}/operator/stream")
    async def project_operator_stream(
        project_id: str,
        request: Request,
        cursor: int = 0,
    ) -> StreamingResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        return StreamingResponse(
            _tail_operator_output(
                context.state_dir,
                project_root=context.project_root,
                request=request,
                cursor=max(cursor, 0),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/projects/{project_id}/operator/start")
    async def project_operator_start(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        payload = await _request_json(request)
        payload["project_id"] = project_id
        result = _web_action(
            context.state_dir,
            "start-operator-session",
            payload=payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key,
            config=context.config,
            project_root=context.project_root,
            project_id=project_id,
            legacy_route=False,
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.post("/api/projects/{project_id}/operator/input")
    async def project_operator_input(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        auth_error = _web_mutation_auth_error(
            "operator-input",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)
        payload = await _request_json(request)
        text = str(payload.get("text") or "")
        if not text.strip():
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "text is required"},
                status_code=422,
            )
        return JSONResponse(_operator_input(
            context.state_dir,
            project_root=context.project_root,
            project_id=project_id,
            text=text,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            config=context.config,
        ))

    @app.post("/api/projects/{project_id}/operator/stop")
    async def project_operator_stop(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        auth_error = _web_mutation_auth_error(
            "operator-stop",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)
        payload = await _request_json(request)
        reason = str(payload.get("reason") or "web stop requested")
        return JSONResponse(_operator_stop(
            context.state_dir,
            project_root=context.project_root,
            reason=reason,
        ))

    @app.get("/api/projects/{project_id}/stream")
    async def project_stream(project_id: str, request: Request) -> StreamingResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        cursor = _parse_cursor(request.query_params.get("cursor"))
        return StreamingResponse(
            _tail_events(
                context.state_dir,
                request,
                event_log=event_log_from_project(
                    context.state_dir,
                    config=context.config,
                ),
                cursor=cursor,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.websocket("/api/projects/{project_id}/operator/io")
    async def project_operator_io(project_id: str, websocket: WebSocket) -> None:
        try:
            context = _resolve_api_project(
                project_id,
                default_project_id=default_project_id,
                default_state_dir=state_dir,
                default_config=config,
                default_project_root=project_root,
            )
        except HTTPException:
            await websocket.close(code=1008)
            return
        await _operator_io_socket(
            websocket,
            state_dir=context.state_dir,
            project_root=context.project_root,
        )

    @app.websocket("/api/projects/{project_id}/operator/control")
    async def project_operator_control(project_id: str, websocket: WebSocket) -> None:
        try:
            context = _resolve_api_project(
                project_id,
                default_project_id=default_project_id,
                default_state_dir=state_dir,
                default_config=config,
                default_project_root=project_root,
            )
        except HTTPException:
            await websocket.close(code=1008)
            return
        await _operator_control_socket(
            websocket,
            state_dir=context.state_dir,
            project_root=context.project_root,
        )

    @app.post("/api/projects/{project_id}/actions/{action_name}")
    async def project_action(
        project_id: str,
        action_name: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        context = _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )
        raw_payload = await _request_json(request)
        envelope = _project_action_envelope(project_id, raw_payload)
        if not envelope["ok"]:
            return JSONResponse(envelope, status_code=int(envelope["_status_code"]))
        result = _web_action(
            context.state_dir,
            action_name,
            payload=envelope["payload"],
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key or envelope.get("idempotency_key"),
            config=context.config,
            project_root=context.project_root,
            project_id=project_id,
            legacy_route=False,
            source_session_id=str(raw_payload.get("source_session_id") or ""),
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.get("/api/provider-health")
    def provider_health() -> JSONResponse:
        from zf.runtime.provider_health import project_provider_health

        return JSONResponse(project_provider_health(state_dir))

    @app.get("/api/stage-reports/latest")
    def latest_stage_report() -> JSONResponse:
        if not default_project_id:
            return JSONResponse(_no_default_project_payload(), status_code=409)
        from zf.runtime.stage_reports import read_latest_stage_report

        return JSONResponse(read_latest_stage_report(state_dir))

    @app.get("/api/autoresearch")
    def autoresearch_projection() -> JSONResponse:
        return JSONResponse(project_autoresearch_state(
            state_dir,
            project_root=project_root,
        ))

    @app.get("/api/repair-actions")
    def repair_actions_projection() -> JSONResponse:
        from zf.runtime.repair_actions import read_repair_actions

        return JSONResponse(read_repair_actions(state_dir))

    @app.get("/api/tasks/{task_id}/diff")
    def task_diff(task_id: str) -> JSONResponse:
        return JSONResponse(_task_diff(
            state_dir,
            task_id,
            config=config,
            project_root=project_root,
        ))

    @app.get("/api/tasks/{task_id}/timeline")
    def task_timeline(
        task_id: str,
        limit: int = 200,
        deep: bool = False,
    ) -> JSONResponse:
        timeline = _task_timeline(
            state_dir,
            task_id,
            config=config,
            limit=limit,
            deep=deep,
        )
        if timeline is None:
            raise HTTPException(404, f"task {task_id!r} not found")
        return JSONResponse(timeline)

    @app.get("/api/tasks/{task_id}/runs")
    def task_runs(task_id: str) -> JSONResponse:
        return JSONResponse(_task_runs(state_dir, project_root=project_root, task_id=task_id))

    @app.get("/api/tasks/{task_id}/why-not-done")
    def task_why_not_done(task_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import project_why_not_done

        return JSONResponse(project_why_not_done(
            state_dir,
            task_id,
            config=config,
            project_root=project_root,
        ).to_dict())

    @app.get("/api/tasks/{task_id}/resume-packet")
    def task_resume_packet(task_id: str, dispatch_id: str = "") -> JSONResponse:
        from zf.runtime.long_horizon import build_resume_packet

        return JSONResponse(build_resume_packet(
            state_dir,
            task_id,
            dispatch_id=dispatch_id,
            config=config,
            project_root=project_root,
        ))

    @app.get("/api/tasks/{task_id}/workpad")
    def task_workpad(task_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import project_workpad

        return JSONResponse(project_workpad(
            state_dir,
            task_id,
            config=config,
            project_root=project_root,
        ).to_dict())

    @app.get("/api/tasks/{task_id}/retry-metadata")
    def task_retry_metadata(task_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import project_retry_metadata

        return JSONResponse(project_retry_metadata(state_dir, task_id).to_dict())

    @app.get("/api/tasks/{task_id}/decision-trace")
    def task_decision_trace(task_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import decision_trace_for_task

        return JSONResponse(decision_trace_for_task(state_dir, task_id))

    @app.get("/api/tasks/{task_id}/progress-projection")
    def task_progress_projection(task_id: str) -> JSONResponse:
        from zf.runtime.progress_projection import project_task_progress

        return JSONResponse(project_task_progress(state_dir, task_id))

    @app.get("/api/tasks/{task_id}/operations")
    def task_operations(task_id: str) -> JSONResponse:
        from zf.runtime.operation_projection import project_task_operations

        return JSONResponse(project_task_operations(state_dir, task_id))

    @app.get("/api/tasks/{task_id}/harness-score")
    def task_harness_score(task_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import (
            audit_completion,
            harness_strength_score,
            project_why_not_done,
        )

        projection = project_why_not_done(
            state_dir,
            task_id,
            config=config,
            project_root=project_root,
        )
        audit = audit_completion(
            state_dir,
            task_id,
            config=config,
            project_root=project_root,
        )
        return JSONResponse(harness_strength_score(
            why_not_done=projection,
            completion_audit=audit,
        ))

    @app.get("/api/tasks/{task_id}/skills-projection")
    def task_skills_projection(task_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import project_skill_set

        return JSONResponse(project_skill_set(
            state_dir,
            task_id,
            config=config,
        ))

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str) -> JSONResponse:
        detail = _task_detail(
            state_dir,
            task_id,
            config=config,
            project_root=project_root,
        )
        if detail is None:
            raise HTTPException(404, f"task {task_id!r} not found")
        return JSONResponse(detail)

    @app.get("/api/traces/{trace_id}")
    def trace_detail(trace_id: str) -> JSONResponse:
        return JSONResponse(_trace_detail(state_dir, trace_id, config=config))

    @app.get("/api/operations/{dispatch_id}")
    def operation_detail(dispatch_id: str) -> JSONResponse:
        from zf.runtime.operation_projection import project_operation

        return JSONResponse(project_operation(state_dir, dispatch_id))

    @app.get("/api/candidates/{pdd_id}")
    def candidate_detail(pdd_id: str) -> JSONResponse:
        return JSONResponse(_candidate_detail(state_dir, pdd_id, config=config))

    @app.get("/api/fanouts/{fanout_id}")
    def fanout_detail(fanout_id: str) -> JSONResponse:
        return JSONResponse(_fanout_detail(state_dir, fanout_id, config=config))

    @app.get("/api/fanout-identities")
    def fanout_identities() -> JSONResponse:
        from zf.runtime.fanout_identity import read_fanout_identities

        return JSONResponse(read_fanout_identities(state_dir))

    @app.get("/api/integrations/{feature_id}")
    def integration_item(feature_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import build_integration_item

        return JSONResponse(build_integration_item(
            state_dir,
            feature_id,
            project_root=project_root,
        ).to_dict())

    @app.get("/api/integration-queue")
    def integration_queue() -> JSONResponse:
        from zf.runtime.integration_queue import read_integration_queue

        return JSONResponse(read_integration_queue(
            state_dir,
            project_root=project_root,
        ))

    @app.get("/api/goals/{feature_id}")
    def goal_projection(feature_id: str) -> JSONResponse:
        from zf.runtime.long_horizon import map_goal_to_work_units

        return JSONResponse(map_goal_to_work_units(
            state_dir,
            feature_id,
            config=config,
        ))

    @app.get("/api/archives/tasks")
    def archive_tasks(include_active: bool = False) -> JSONResponse:
        return JSONResponse(_archive_tasks(state_dir, include_active=include_active))

    @app.get("/api/runs")
    def runs() -> JSONResponse:
        return JSONResponse(_runs_index(state_dir, project_root=project_root))

    @app.get("/api/runs/active")
    def active_runs() -> JSONResponse:
        return JSONResponse(_active_runs(state_dir, project_root=project_root))

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str) -> JSONResponse:
        return JSONResponse(_run_events(state_dir, run_id))

    @app.get("/api/runs/{run_id}/scorecard")
    def run_scorecard(run_id: str) -> JSONResponse:
        return JSONResponse(_run_scorecard(state_dir, run_id))

    @app.get("/api/runs/{run_id}/fanouts")
    def run_fanouts(run_id: str) -> JSONResponse:
        return JSONResponse(_run_fanouts(state_dir, run_id))

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> JSONResponse:
        return JSONResponse(_run_detail(state_dir, run_id))

    @app.get("/api/workdirs")
    def workdirs() -> JSONResponse:
        return JSONResponse(_workdirs(
            state_dir,
            config=config,
            project_root=project_root,
        ))

    @app.get("/api/agents")
    def agents() -> JSONResponse:
        return JSONResponse(_agents(state_dir, config=config, project_root=project_root))

    @app.get("/api/roles")
    def roles() -> JSONResponse:
        return JSONResponse(_roles(state_dir, config=config))

    @app.get("/api/runtime")
    def runtime(request: Request) -> JSONResponse:
        return JSONResponse(_runtime(
            state_dir,
            config=config,
            project_root=project_root,
            web_session_token=_web_session_cookie(request),
        ))

    @app.get("/api/run-manager")
    def run_manager_projection() -> JSONResponse:
        from zf.runtime.run_manager import build_run_manager_projection

        return JSONResponse(build_run_manager_projection(
            state_dir,
            config=config,
            project_root=project_root,
        ))

    @app.get("/api/run-goal")
    def run_goal_projection() -> JSONResponse:
        from zf.runtime.run_manager import build_run_goal_projection

        return JSONResponse(build_run_goal_projection(
            EventLog(state_dir / "events.jsonl").read_all(),
        ))

    @app.get("/api/gate-projection")
    def gate_projection() -> JSONResponse:
        configured_backend = _canonical_operator_backend(
            os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
            or getattr(getattr(config, "orchestrator", None), "backend", "")
        )
        event_log = event_log_from_project(state_dir, config=config)
        return JSONResponse(project_gate_projection(
            state_dir,
            config=config,
            project_root=project_root,
            operator_backends=_operator_backend_options(
                configured_backend=configured_backend,
            ),
            allowed_actions=KANBAN_AGENT_ALLOWED_ACTIONS,
            web_token_configured=_web_action_token_configured(),
            web_authorization_available=_web_action_authorization_available(),
            web_mutation_mode=_web_mutation_mode(),
            events=event_log.read_all(),
        ))

    @app.get("/api/hook-registry")
    def hook_registry() -> JSONResponse:
        event_log = event_log_from_project(state_dir, config=config)
        return JSONResponse(project_hook_registry(
            state_dir,
            config=config,
            project_root=project_root,
            events=event_log.read_all(),
        ))

    @app.get("/api/workflow/graph")
    def workflow_graph() -> JSONResponse:
        return JSONResponse(_workflow_graph(state_dir, config=config))

    @app.get("/api/execution-patterns")
    def execution_patterns() -> JSONResponse:
        from zf.runtime.execution_patterns import project_execution_patterns

        return JSONResponse(project_execution_patterns(config, state_dir=state_dir))

    @app.get("/api/channels")
    def channels() -> JSONResponse:
        from zf.runtime.channel_projection import project_channels
        from zf.web.projections import read_model

        try:
            projected = read_model.channel_summary(state_dir, config=config)
            if projected is not None:
                return JSONResponse(projected)
        except Exception:
            pass
        return JSONResponse(project_channels(state_dir))

    @app.get("/api/channels/{channel_id}")
    def channel_detail(channel_id: str) -> JSONResponse:
        from zf.runtime.channel_projection import (
            DEFAULT_CHANNEL_IDS,
            project_channel,
            project_empty_channel,
        )

        detail = project_channel(state_dir, channel_id)
        if detail is None:
            if channel_id.strip().lower() in DEFAULT_CHANNEL_IDS:
                return JSONResponse(project_empty_channel(channel_id))
            raise HTTPException(404, f"channel {channel_id!r} not found")
        return JSONResponse(detail)

    @app.get("/api/channels/{channel_id}/history/search")
    def channel_history_search(
        channel_id: str,
        q: str = "",
        thread_id: str = "",
        member_id: str = "",
        mention: str = "",
        limit: int = 50,
    ) -> JSONResponse:
        from zf.runtime.channel_projection import search_channel_history

        return JSONResponse(search_channel_history(
            state_dir,
            channel_id,
            q=q,
            thread_id=thread_id,
            member_id=member_id,
            mention=mention,
            limit=limit,
        ))

    @app.post("/api/channels/{channel_id}/messages")
    async def channel_post_message(
        channel_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        payload = await _request_json(request)
        payload["channel_id"] = channel_id
        result = _web_action(
            state_dir,
            "channel-post-message",
            payload=payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key,
            config=config,
            project_root=project_root,
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.post("/api/channels/{channel_id}/members/invite")
    async def channel_invite_member(
        channel_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        payload = await _request_json(request)
        payload["channel_id"] = channel_id
        result = _web_action(
            state_dir,
            "channel-invite-member",
            payload=payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key,
            config=config,
            project_root=project_root,
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.post("/api/channels/{channel_id}/synthesis")
    async def channel_synthesis(
        channel_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        payload = await _request_json(request)
        payload["channel_id"] = channel_id
        result = _web_action(
            state_dir,
            "channel-synthesis",
            payload=payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key,
            config=config,
            project_root=project_root,
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.post("/api/channels/{channel_id}/workflow-request")
    async def channel_workflow_request(
        channel_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        payload = await _request_json(request)
        payload["channel_id"] = channel_id
        result = _web_action(
            state_dir,
            "workflow-invoke",
            payload=payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key,
            config=config,
            project_root=project_root,
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.get("/api/web-session")
    def web_session(request: Request) -> JSONResponse:
        return JSONResponse(_web_session_projection(_web_session_cookie(request)))

    @app.post("/api/web-session/unlock")
    async def web_session_unlock(request: Request) -> JSONResponse:
        payload = await _request_json(request)
        result = _unlock_web_session(
            str(payload.get("passcode") or ""),
            client_id=_request_client_id(request),
        )
        status_code = int(result.pop("_status_code", 200))
        token = result.pop("_session_token", None)
        response = JSONResponse(result, status_code=status_code)
        if result.get("ok") and isinstance(token, str):
            response.set_cookie(
                _WEB_SESSION_COOKIE,
                token,
                httponly=True,
                max_age=_web_session_ttl_seconds(),
                samesite="lax",
            )
        return response

    @app.post("/api/web-session/lock")
    def web_session_lock(request: Request) -> JSONResponse:
        token = _web_session_cookie(request)
        if token:
            _WEB_SESSIONS.pop(token, None)
        response = JSONResponse({
            "ok": True,
            "status": "locked",
            "session": _web_session_projection(None),
        })
        response.delete_cookie(_WEB_SESSION_COOKIE)
        return response

    @app.get("/api/operator/session")
    def operator_session() -> JSONResponse:
        return JSONResponse(_operator_session_status(state_dir, project_root=project_root))

    @app.get("/api/operator/output")
    def operator_output(cursor: int = 0, limit: int = 200) -> JSONResponse:
        manager = _operator_session_manager(state_dir, project_root=project_root)
        return JSONResponse(redact_obj(manager.output_since(cursor=max(cursor, 0), limit=limit)))

    @app.get("/api/operator/stream")
    async def operator_stream(request: Request, cursor: int = 0) -> StreamingResponse:
        return StreamingResponse(
            _tail_operator_output(
                state_dir,
                project_root=project_root,
                request=request,
                cursor=max(cursor, 0),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/operator/start")
    async def operator_start(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        payload = await _request_json(request)
        result = _web_action(
            state_dir,
            "start-operator-session",
            payload=payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key,
            config=config,
            project_root=project_root,
            project_id=default_project_id,
            legacy_route=True,
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.websocket("/api/operator/io")
    async def operator_io(websocket: WebSocket) -> None:
        await _operator_io_socket(
            websocket,
            state_dir=state_dir,
            project_root=project_root,
        )

    @app.websocket("/api/operator/control")
    async def operator_control(websocket: WebSocket) -> None:
        await _operator_control_socket(
            websocket,
            state_dir=state_dir,
            project_root=project_root,
        )

    @app.post("/api/operator/input")
    async def operator_input(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth_error = _web_mutation_auth_error(
            "operator-input",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)
        payload = await _request_json(request)
        text = str(payload.get("text") or "")
        if not text.strip():
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "text is required"},
                status_code=422,
            )
        return JSONResponse(_operator_input(
            state_dir,
            project_root=project_root,
            text=text,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            config=config,
        ))

    @app.post("/api/operator/stop")
    async def operator_stop(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth_error = _web_mutation_auth_error(
            "operator-stop",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)
        payload = await _request_json(request)
        reason = str(payload.get("reason") or "web stop requested")
        return JSONResponse(_operator_stop(state_dir, project_root=project_root, reason=reason))

    @app.get("/api/skills")
    def skills() -> JSONResponse:
        return JSONResponse(_skills(
            state_dir,
            config=config,
            project_root=project_root,
        ))

    @app.get("/api/events")
    def events(
        limit: int = 100,
        cursor: int | None = None,
        task_id: str | None = None,
        actor: str | None = None,
        type: str | None = None,  # noqa: A002 - API query name is intentional
        prefix: str | None = None,
        failed: bool = False,
        blocked: bool = False,
    ) -> JSONResponse:
        return JSONResponse(_events_page(
            state_dir,
            limit=limit,
            cursor=cursor,
            task_id=task_id,
            actor=actor,
            event_type=type,
            event_prefix=prefix,
            failed=failed,
            blocked=blocked,
            config=config,
        ))

    @app.get("/api/events/{event_id}")
    def event_detail(event_id: str) -> JSONResponse:
        return JSONResponse(_event_detail_payload(state_dir, event_id, config=config))

    @app.get("/api/diagnostics/{trace_id}")
    def diagnostics(trace_id: str) -> JSONResponse:
        return JSONResponse(_diagnostics(state_dir, trace_id))

    @app.get("/api/search")
    def search(q: str = "", limit: int = 50) -> JSONResponse:
        return JSONResponse(_search(state_dir, q=q, limit=limit, config=config))

    @app.post("/api/actions/{action_name}")
    async def action(
        action_name: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        payload = await _request_json(request)
        result = _web_action(
            state_dir,
            action_name,
            payload=payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=_web_session_cookie(request),
            x_idempotency_key=x_idempotency_key,
            config=config,
            project_root=project_root,
            project_id=default_project_id,
            legacy_route=True,
        )
        status_code = int(result.pop("_status_code", 200))
        return JSONResponse(result, status_code=status_code)

    @app.get("/api/views/tasks")
    def views_tasks() -> JSONResponse:
        return JSONResponse([asdict(v) for v in _task_views(state_dir)])

    @app.get("/api/views/workers")
    def views_workers() -> JSONResponse:
        return JSONResponse(_agent_view(
            state_dir,
            config=config,
            project_root=project_root,
        ))

    @app.get("/api/views/recent")
    def views_recent(limit: int = 30) -> JSONResponse:
        return JSONResponse(_recent_events(state_dir, limit, config=config))

    @app.get("/api/progress", response_class=PlainTextResponse)
    def progress() -> str:
        path = state_dir / "progress.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @app.get("/api/instructions/{role}", response_class=PlainTextResponse)
    def instructions(role: str) -> str:
        # Defensive path: only allow filenames matching role pattern
        if "/" in role or "\\" in role or role.startswith("."):
            raise HTTPException(400, "invalid role name")
        path = state_dir / "instructions" / f"{role}.md"
        if not path.exists():
            raise HTTPException(404, f"no instructions for {role}")
        return path.read_text(encoding="utf-8")

    @app.get("/api/briefings/{name}", response_class=PlainTextResponse)
    def briefing(name: str) -> str:
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(400, "invalid briefing name")
        path = state_dir / "briefings" / name
        if not path.exists():
            raise HTTPException(404, f"no briefing {name}")
        return path.read_text(encoding="utf-8")

    @app.get("/api/cost")
    def cost() -> JSONResponse:
        return JSONResponse(_cost(state_dir))

    # ---- SSE ----

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        cursor = _parse_cursor(request.query_params.get("cursor"))
        return StreamingResponse(
            _tail_events(
                state_dir,
                request,
                event_log=event_log_from_project(state_dir, config=config),
                cursor=cursor,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx pass-through if proxied
            },
        )

    # doc 68 S3 / E1a: delivery-trace read-only API as a sibling APIRouter
    # mounted here — NOT appended to create_app's inline routes. Must be
    # registered before the catch-all SPA fallback below (first match wins).
    from zf.web.delivery_trace_routes import build_delivery_trace_router

    def _delivery_trace_ctx(project_id: str):
        return _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=project_root,
        )

    app.include_router(build_delivery_trace_router(resolve_ctx=_delivery_trace_ctx))

    # doc94: Loop projection is a read-only sibling router. It stays out of
    # snapshot fan-out and is loaded only when the Loop page/API is requested.
    from zf.web.loop_routes import build_loop_router

    app.include_router(build_loop_router(
        resolve_ctx=_delivery_trace_ctx,
        authorize_mutation=_web_mutation_auth_error,
    ))

    # Measure Loop projection is the product-delivery metric companion to
    # doc94 loop.v1. It is read-only and stays out of snapshot fan-out.
    from zf.web.measure_loop_routes import build_measure_loop_router

    app.include_router(build_measure_loop_router(resolve_ctx=_delivery_trace_ctx))

    # B9/B15 (doc 91 §8 / doc 93 §7): plan 审核 pending + contract-health
    from zf.web.plan_health_routes import build_plan_health_router

    app.include_router(
        build_plan_health_router(resolve_ctx=_delivery_trace_ctx),
    )

    # doc96 P5: runtime resources/session mirror/read-only terminal projection.
    from zf.web.runtime_resource_routes import build_runtime_resource_router

    app.include_router(
        build_runtime_resource_router(resolve_ctx=_delivery_trace_ctx),
    )

    # Overview pulse bands (overview-pulse.v1) — same sibling-router pattern.
    from zf.web.overview_pulse import build_overview_pulse_router

    app.include_router(build_overview_pulse_router(resolve_ctx=_delivery_trace_ctx))

    # Project profile detect/recommend (project-profile.v1, doc 102 §6).
    from zf.web.profile_routes import build_profile_router

    app.include_router(build_profile_router())

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "assets/", "static/")):
            raise HTTPException(404, "not found")
        index = _ui_index()
        if not index.exists():
            raise HTTPException(500, "web UI index missing")
        return FileResponse(index, media_type="text/html")

    return app


# ----------------- helpers (pure data assembly) -----------------


def _react_dist_dir() -> Path | None:
    index = _REACT_DIST_DIR / "index.html"
    return _REACT_DIST_DIR if index.exists() else None




def _ui_index() -> Path:
    react_dist = _react_dist_dir()
    if react_dist is not None:
        return react_dist / "index.html"
    return _STATIC_DIR / "index.html"












def _workspace_overview_payload(
    *,
    default_project_id: str,
    default_state_dir: Path,
    default_config: ZfConfig | None,
    default_project_root: Path,
    default_project_opened_at: str = "",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    manager = RuntimeManager()
    projects = _workspace_projects_payload(
        default_project_id=default_project_id,
        default_state_dir=default_state_dir,
        default_config=default_config,
        default_project_root=default_project_root,
        default_project_opened_at=default_project_opened_at,
    ).get("items", [])
    for item in projects:
        project_id = str(item.get("project_id") or "")
        lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
        try:
            context = _resolve_api_project(
                project_id,
                default_project_id=default_project_id,
                default_state_dir=default_state_dir,
                default_config=default_config,
                default_project_root=default_project_root,
                require_initialized=False,
            )
            can_open_board = bool(lifecycle.get("can_open_board"))
            rows.append({
                **manager.overview_row(
                    project_id=project_id,
                    name=str(item.get("name") or project_id),
                    root=context.project_root,
                    state_dir=context.state_dir,
                    config=context.config,
                ),
                "lifecycle": lifecycle,
                "can_open_board": can_open_board,
                "task_counts": _task_counts(context.state_dir) if can_open_board else {},
                "last_event_seq": (
                    _line_count(context.state_dir / "events.jsonl")
                    if can_open_board else 0
                ),
                "resources": (
                    _workspace_resource_summary(context, project_id=project_id)
                    if can_open_board else {}
                ),
            })
        except Exception as exc:
            rows.append({
                "project_id": project_id,
                "name": str(item.get("name") or project_id),
                "root": str(item.get("root") or ""),
                "lifecycle": lifecycle,
                "can_open_board": False,
                "state": "unavailable",
                "reason": str(exc),
            })
    session_counts: dict[str, int] = {}
    for row in rows:
        runtime = row.get("runtime")
        if not isinstance(runtime, dict):
            continue
        tmux_session = str(runtime.get("tmux_session") or "")
        if tmux_session:
            session_counts[tmux_session] = session_counts.get(tmux_session, 0) + 1
    for row in rows:
        runtime = row.get("runtime")
        if not isinstance(runtime, dict):
            continue
        tmux_session = str(runtime.get("tmux_session") or "")
        if tmux_session and session_counts.get(tmux_session, 0) > 1:
            runtime["state"] = "conflicted"
            runtime["reason"] = (
                "tmux session is configured by multiple workspace projects"
            )
    active_project_id = _active_workspace_project_id(
        [row for row in rows if isinstance(row, dict)],
        default_project_id=default_project_id,
    )
    return {
        "schema_version": "workspace.overview.v1",
        "server_default_project_id": default_project_id,
        "active_project_id": active_project_id,
        "active_project_is_server_default": (
            bool(default_project_id) and active_project_id == default_project_id
        ),
        "projects": rows,
    }












def _workspace_resource_summary(
    context: ProjectContext,
    *,
    project_id: str,
) -> dict[str, Any]:
    return {
        "channels": _workspace_channel_summary(context.state_dir),
        "automations": _workspace_automation_summary(
            context.state_dir,
            project_id=project_id,
            project_name=(
                context.config.project.name
                if context.config is not None else context.project_root.name
            ),
        ),
        "operator": _workspace_operator_summary(context),
        "agent_cockpit": _workspace_agent_cockpit_summary(context),
    }






def _workspace_operator_summary(context: ProjectContext) -> dict[str, Any]:
    try:
        session = _operator_session_status(
            context.state_dir,
            project_root=context.project_root,
        )
        return {
            "status": str(session.get("status") or session.get("runtime_status") or "idle"),
            "alive": bool(session.get("alive")),
            "backend": str(session.get("backend") or ""),
            "session_id": str(session.get("session_id") or ""),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _workspace_agent_cockpit_summary(context: ProjectContext) -> dict[str, Any]:
    try:
        agents = _agents(
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
        )
        cockpit = project_agent_cockpit(context.state_dir, agents=agents)
        summary = cockpit.get("summary", {})
        return summary if isinstance(summary, dict) else {}
    except Exception as exc:
        return {"error": str(exc)}






def _snapshot(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    web_session_token: str | None = None,
) -> dict:
    events_path = state_dir / "events.jsonl"
    seq = _line_count(events_path) if events_path.exists() else 0
    project_root = _resolve_project_root_for_state(state_dir, project_root)
    roles = _safe_snapshot_projection(
        "roles",
        [],
        lambda: _roles(state_dir, config=config),
    )
    workdirs = _safe_snapshot_projection(
        "workdirs",
        [],
        lambda: _workdirs(state_dir, config=config, project_root=project_root),
    )
    agents = _safe_snapshot_projection(
        "agents",
        [],
        lambda: _agents(
            state_dir,
            config=config,
            project_root=project_root,
            workdirs_snapshot=workdirs,
        ),
    )
    agent_view = _safe_snapshot_projection(
        "agent_view",
        {},
        lambda: _agent_view(
            state_dir,
            config=config,
            project_root=project_root,
            agents=agents,
        ),
    )
    runtime = _safe_snapshot_projection(
        "runtime",
        {},
        lambda: _runtime(
            state_dir,
            config=config,
            project_root=project_root,
            web_session_token=web_session_token,
        ),
    )
    configured_operator_backend = _canonical_operator_backend(
        os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
        or getattr(getattr(config, "orchestrator", None), "backend", "")
    )
    from zf.runtime.channel_projection import project_channels
    from zf.runtime.execution_patterns import project_execution_patterns

    return {
        "seq": seq,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "project_id": _default_project_id(config=config, project_root=project_root),
            "name": (
                config.project.name
                if config is not None and config.project.name else project_root.name
            ),
            "root": str(project_root),
            "state_dir": str(state_dir),
        },
        "tasks": _safe_snapshot_projection(
            "tasks", [], lambda: _kanban(state_dir, config=config)
        ),
        "archive_tasks": _safe_snapshot_projection(
            "archive_tasks", [], lambda: _archive_tasks(state_dir, include_active=False)
        ),
        "features": _safe_snapshot_projection("features", [], lambda: _features(state_dir)),
        "delivery_features": _safe_snapshot_projection(
            "delivery_features", [], lambda: _delivery_features(state_dir)
        ),
        "metrics_snapshot": _safe_snapshot_projection(
            "metrics_snapshot", {}, lambda: _metrics_snapshot_projection(state_dir)
        ),
        "fleet_stats": _safe_snapshot_projection(
            "fleet_stats", {}, lambda: _fleet_stats_projection(state_dir, config=config)
        ),
        "provider_health": _safe_snapshot_projection(
            "provider_health", {}, lambda: _provider_health_projection(state_dir)
        ),
        "traces": _safe_snapshot_projection(
            "traces", [], lambda: _traces(state_dir, config=config)
        ),
        "fanouts": _safe_snapshot_projection(
            "fanouts", [], lambda: _fanouts(state_dir, config=config)
        ),
        "channels": _safe_snapshot_projection(
            "channels", [], lambda: project_channels(state_dir).get("channels", [])
        ),
        "automations": _safe_snapshot_projection(
            "automations",
            [],
            lambda: project_automations(
                state_dir,
                project_id=_default_project_id(config=config, project_root=project_root),
                project_name=(
                    config.project.name
                    if config is not None else project_root.name
                ),
            ),
        ),
        "execution_patterns": _safe_snapshot_projection(
            "execution_patterns",
            {},
            lambda: project_execution_patterns(config, state_dir=state_dir),
        ),
        "candidates": _safe_snapshot_projection(
            "candidates", [], lambda: _candidates(state_dir, config=config)
        ),
        "runs": _safe_snapshot_projection(
            "runs", [], lambda: _runs_index(state_dir, project_root=project_root).get("runs", [])
        ),
        "active_runs": _safe_snapshot_projection(
            "active_runs",
            [],
            lambda: _active_runs(state_dir, project_root=project_root).get("active_runs", []),
        ),
        "agents": agents,
        "agent_view": agent_view,
        "agent_live": _safe_snapshot_projection(
            "agent_live", {}, lambda: project_agent_live(state_dir)
        ),
        "assignment_routes": _safe_snapshot_projection(
            "assignment_routes", {}, lambda: project_assignment_routes(state_dir)
        ),
        "agent_cockpit": _safe_snapshot_projection(
            "agent_cockpit", {}, lambda: project_agent_cockpit(state_dir, agents=agents)
        ),
        "recovery": _safe_snapshot_projection(
            "recovery", {}, lambda: project_recovery_catalog(state_dir)
        ),
        "pause_lifecycle": _safe_snapshot_projection(
            "pause_lifecycle", {}, lambda: project_pause_lifecycle(state_dir)
        ),
        "gate_projection": _safe_snapshot_projection(
            "gate_projection",
            {},
            lambda: project_gate_projection(
                state_dir,
                config=config,
                project_root=project_root,
                operator_backends=_operator_backend_options(
                    configured_backend=configured_operator_backend,
                ),
                allowed_actions=KANBAN_AGENT_ALLOWED_ACTIONS,
                web_token_configured=_web_action_token_configured(),
                web_authorization_available=_web_action_authorization_available(),
                web_mutation_mode=_web_mutation_mode(),
            ),
        ),
        "hook_registry": _safe_snapshot_projection(
            "hook_registry",
            {},
            lambda: project_hook_registry(
                state_dir,
                config=config,
                project_root=project_root,
            ),
        ),
        "provider_capabilities": _safe_snapshot_projection(
            "provider_capabilities",
            {},
            lambda: project_provider_capabilities(
                config=config,
                operator_backends=_operator_backend_options(
                    configured_backend=configured_operator_backend,
                ),
            ),
        ),
        "runtime_snapshots": _safe_snapshot_projection(
            "runtime_snapshots",
            {},
            lambda: _runtime_snapshots(state_dir, project_root=project_root),
        ),
        "spine_review": _safe_snapshot_projection(
            "spine_review",
            {},
            lambda: project_spine_review_insight(
                state_dir,
                project_id=_default_project_id(config=config, project_root=project_root),
            ),
        ),
        "mutation_audit": _safe_snapshot_projection(
            "mutation_audit", {}, lambda: project_mutation_audit(state_dir)
        ),
        "worktree_drift": _safe_snapshot_projection(
            "worktree_drift", {}, lambda: project_worktree_drift_audit(state_dir)
        ),
        "roles": roles,
        "workdirs": workdirs,
        "skills": _safe_snapshot_projection(
            "skills", [], lambda: _skills(state_dir, config=config, project_root=project_root)
        ),
        "cost": _safe_snapshot_projection("cost", {}, lambda: _cost(state_dir)),
        "workers": _safe_snapshot_projection(
            "workers", [], lambda: _workers(state_dir, config=config)
        ),
        "runtime": runtime,
    }


def _snapshot_slice(
    state_dir: Path,
    *,
    slice_name: str,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    web_session_token: str | None = None,
) -> dict:
    if slice_name == "observability":
        data = _snapshot(
            state_dir,
            config=config,
            project_root=project_root,
            web_session_token=web_session_token,
        )
        data["snapshot_slice"] = "observability"
        return data

    project_root = _resolve_project_root_for_state(state_dir, project_root)
    data = _snapshot_base(state_dir, config=config, project_root=project_root)
    data.update(_snapshot_empty_defaults())
    data["snapshot_slice"] = slice_name

    if slice_name in {"light", "board"}:
        data.update({
            "tasks": _safe_snapshot_projection(
                "tasks", [], lambda: _kanban(state_dir, config=config)
            ),
            "archive_tasks": _safe_snapshot_projection(
                "archive_tasks", [], lambda: _archive_tasks(state_dir, include_active=False)
            ),
            "features": _safe_snapshot_projection("features", [], lambda: _features(state_dir)),
            "delivery_features": _safe_snapshot_projection(
                "delivery_features", [], lambda: _delivery_features(state_dir)
            ),
            "channels": _safe_snapshot_projection(
                "channels", [], lambda: _snapshot_channel_list(state_dir, config=config)
            ),
            "runtime": _light_runtime_projection(
                state_dir,
                config=config,
                web_session_token=web_session_token,
            ),
            "event_projection": _safe_snapshot_projection(
                "event_projection",
                {},
                lambda: _event_projection_status(state_dir),
            ),
            "cost": _safe_snapshot_projection("cost", _empty_cost_projection(), lambda: _cost(state_dir)),
        })
        return data

    if slice_name == "runtime":
        roles = _safe_snapshot_projection(
            "roles", [], lambda: _roles(state_dir, config=config)
        )
        workdirs = _safe_snapshot_projection(
            "workdirs", [], lambda: _workdirs(state_dir, config=config, project_root=project_root)
        )
        agents = _safe_snapshot_projection(
            "agents",
            [],
            lambda: _agents(
                state_dir,
                config=config,
                project_root=project_root,
                workdirs_snapshot=workdirs,
            ),
        )
        data.update({
            "roles": roles,
            "workdirs": workdirs,
            "agents": agents,
            "agent_view": _safe_snapshot_projection(
                "agent_view",
                {},
                lambda: _agent_view(
                    state_dir,
                    config=config,
                    project_root=project_root,
                    agents=agents,
                ),
            ),
            "agent_live": _safe_snapshot_projection(
                "agent_live", {}, lambda: project_agent_live(state_dir)
            ),
            "agent_cockpit": _safe_snapshot_projection(
                "agent_cockpit", {}, lambda: project_agent_cockpit(state_dir, agents=agents)
            ),
            "runtime": _safe_snapshot_projection(
                "runtime",
                _empty_runtime_projection(),
                lambda: _runtime(
                    state_dir,
                    config=config,
                    project_root=project_root,
                    web_session_token=web_session_token,
                ),
            ),
            "skills": _safe_snapshot_projection(
                "skills", _empty_skills_projection(), lambda: _skills(state_dir, config=config, project_root=project_root)
            ),
            "cost": _safe_snapshot_projection("cost", _empty_cost_projection(), lambda: _cost(state_dir)),
            "workers": _safe_snapshot_projection(
                "workers", [], lambda: _workers(state_dir, config=config)
            ),
        })
        return data

    data["snapshot_slice"] = "unknown"
    return data


def _snapshot_base(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    project_root: Path,
) -> dict:
    events_path = state_dir / "events.jsonl"
    seq = _line_count(events_path) if events_path.exists() else 0
    return {
        "seq": seq,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "project_id": _default_project_id(config=config, project_root=project_root),
            "name": (
                config.project.name
                if config is not None and config.project.name else project_root.name
            ),
            "root": str(project_root),
            "state_dir": str(state_dir),
        },
    }


def _snapshot_empty_defaults() -> dict:
    return {
        "tasks": [],
        "archive_tasks": [],
        "features": [],
        "delivery_features": [],
        "metrics_snapshot": {},
        "fleet_stats": {},
        "provider_health": {},
        "traces": [],
        "fanouts": [],
        "channels": [],
        "automations": [],
        "execution_patterns": {},
        "candidates": [],
        "runs": [],
        "active_runs": [],
        "agents": [],
        "agent_view": {},
        "agent_live": {},
        "assignment_routes": {},
        "agent_cockpit": {},
        "recovery": {},
        "pause_lifecycle": {},
        "gate_projection": {},
        "hook_registry": {},
        "provider_capabilities": {},
        "runtime_snapshots": {},
        "spine_review": {},
        "mutation_audit": {},
        "worktree_drift": {},
        "roles": [],
        "workdirs": [],
        "skills": _empty_skills_projection(),
        "cost": _empty_cost_projection(),
        "workers": [],
        "runtime": _empty_runtime_projection(),
    }


def _empty_runtime_projection() -> dict:
    return {
        "live": False,
        "mode": "snapshot-light",
        "actions": {
            "mutation_enabled": False,
            "requires_token": True,
            "allowed": [],
        },
        "web_session": {},
        "agent_surface": {},
        "sessions": {},
        "workdirs": {},
    }


def _light_runtime_projection(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    web_session_token: str | None = None,
) -> dict:
    runtime = _empty_runtime_projection()
    events_path = state_dir / "events.jsonl"
    web_session = _web_session_projection(web_session_token)
    runtime.update({
        "live": True,
        "mode": "snapshot-light",
        "state_dir": str(state_dir),
        "seq": _line_count(events_path) if events_path.exists() else 0,
        "actions": {
            "mutation_enabled": _web_action_authorization_available(),
            "mutation_mode": _web_mutation_mode(),
            "allowed": sorted(_ALLOWED_WEB_ACTIONS),
            "requires_token": web_session["mode"] == "token_required",
        },
        "web_session": web_session,
        "sessions": {
            "count": len(_role_session_ids(state_dir)),
            "tmux_session": getattr(config.session, "tmux_session", "")
            if config is not None else "",
            "tmux_layout": getattr(config.session, "tmux_layout", "")
            if config is not None else "",
        },
    })
    return runtime


def _event_projection_status(state_dir: Path) -> dict:
    try:
        from zf.web.projections import read_model

        status = read_model.projection_status(state_dir)
        return {
            "schema_version": status.get("schema_version", "event-read-model.v1"),
            "projection_state": status.get("projection_state", "unknown"),
            "source_seq": status.get("source_seq", 0),
            "projected_seq": status.get("projected_seq", 0),
            "projection_lag": status.get("projection_lag"),
            "segment_count": status.get("segment_count", 0),
            "total_bytes": status.get("total_bytes", 0),
            "updated_at": status.get("updated_at", ""),
            "source": "read_model.sqlite",
        }
    except Exception as exc:
        return {
            "schema_version": "event-read-model.v1",
            "projection_state": "unavailable",
            "error": str(exc),
        }


def _event_detail_payload(
    state_dir: Path,
    event_id: str,
    *,
    config: ZfConfig | None = None,
) -> dict:
    event_id = str(event_id or "").strip()
    if not event_id:
        raise HTTPException(404, "event id is required")
    try:
        from zf.web.projections import read_model

        hydrated = read_model.hydrate_event_by_id(state_dir, event_id, config=config)
        if hydrated is not None:
            seq, event = hydrated
            payload = _event_to_dict(seq, event)
            payload["payload_slim"] = False
            return {
                "schema_version": "event-detail.v1",
                "event_id": event_id,
                "event": payload,
                "source": "read_model.sqlite",
            }
    except Exception:
        pass
    for seq, event in _events_with_seq(state_dir, config=config):
        if str(getattr(event, "id", "") or "") == event_id:
            payload = _event_to_dict(seq, event)
            payload["payload_slim"] = False
            return {
                "schema_version": "event-detail.v1",
                "event_id": event_id,
                "event": payload,
                "source": "events.jsonl",
            }
    raise HTTPException(404, f"event {event_id!r} not found")


def _empty_skills_projection() -> dict:
    return {
        "loaded": [],
        "enabled": [],
        "pool": [],
        "warnings": [],
        "lock_file": "",
    }


def _empty_cost_projection() -> dict:
    return {
        "total_usd": 0,
        "per_role": {},
    }


def _snapshot_channel_list(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
) -> list[dict]:
    try:
        from zf.web.projections import read_model

        projected = read_model.channel_summary(state_dir, config=config)
        if projected is not None:
            return list(projected.get("channels") or [])
    except Exception:
        pass
    from zf.runtime.channel_projection import project_channels

    return list(project_channels(state_dir).get("channels", []))














































































































def _agent_view(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    agents: list[dict] | None = None,
) -> dict:
    """Agent View read model for the Web cockpit.

    The projection is worker-instance-first and intentionally read-only.
    It includes configured roles plus runtime-only instances restored from
    role_sessions.yaml, such as future autoscaled dev-auto-* workers.
    """
    projected_agents = agents if agents is not None else _agents(
        state_dir,
        config=config,
        project_root=project_root,
    )
    workers = [
        dict(agent)
        for agent in projected_agents
        if agent.get("agent_kind") != "web_surface"
    ]
    workers.sort(key=lambda item: (
        str(item.get("parent_role") or item.get("role_type") or ""),
        str(item.get("instance_id") or ""),
    ))
    queue_projection = _agent_view_queue_projection(
        state_dir,
        config=config,
        workers=workers,
    )
    try:
        from zf.runtime.dispatch_diagnostics import build_dispatch_diagnostics

        dispatch_diagnostics = build_dispatch_diagnostics(
            state_dir,
            config=config,
            project_root=project_root,
        )
    except Exception:
        dispatch_diagnostics = {
            "loop": {},
            "worker_availability": [],
            "notifications": [],
            "ready_task_count": 0,
            "dispatchable_worker_count": 0,
        }
    try:
        from zf.runtime.owner_visible_delivery import project_owner_visible_inbox

        owner_visible_inbox = project_owner_visible_inbox(state_dir)
    except Exception:
        owner_visible_inbox = {
            "schema_version": "owner.visible_message.inbox.v0",
            "summary": {"total": 0, "pending": 0, "failed": 0, "delivered": 0},
            "pending": [],
            "failed": [],
            "recent": [],
            "error": "projection_failed",
        }

    groups: dict[str, dict] = {}
    attention: list[dict] = []
    for worker in workers:
        role = str(worker.get("parent_role") or worker.get("role_type") or "unknown")
        origin = str(worker.get("origin") or "runtime")
        group = groups.setdefault(role, {
            "role": role,
            "count": 0,
            "static_count": 0,
            "autoscale_count": 0,
            "runtime_count": 0,
            "attention_count": 0,
            "worker_ids": [],
        })
        group["count"] += 1
        group["worker_ids"].append(worker.get("instance_id", ""))
        if origin == "static":
            group["static_count"] += 1
        elif origin == "autoscale":
            group["autoscale_count"] += 1
        else:
            group["runtime_count"] += 1

        attention_state = str(worker.get("attention_state") or "idle")
        if _attention_state_needs_operator(attention_state):
            group["attention_count"] += 1
            attention.append({
                "instance_id": worker.get("instance_id", ""),
                "parent_role": role,
                "attention_state": attention_state,
                "lifecycle_state": worker.get("lifecycle_state", ""),
                "task_id": worker.get("task_id", "") or worker.get("active_task", ""),
                "reason": worker.get("needs_input_reason", "")
                or worker.get("provider_stop_reason", "")
                or worker.get("last_output_summary", ""),
                "last_event_type": worker.get("last_event_type", ""),
                "last_event_seq": worker.get("last_event_seq", 0),
            })
    for role, summary in queue_projection.get("by_role", {}).items():
        group = groups.setdefault(role, {
            "role": role,
            "count": 0,
            "static_count": 0,
            "autoscale_count": 0,
            "runtime_count": 0,
            "attention_count": 0,
            "worker_ids": [],
        })
        group["queue"] = summary
        if summary.get("needs_attention_count", 0):
            group["needs_attention"] = True

    selected = ""
    if attention:
        selected = str(attention[0].get("instance_id") or "")
    elif workers:
        selected = str(workers[0].get("instance_id") or "")

    return {
        "mode": "autopilot_cockpit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state_dir": str(state_dir),
        "role_groups": sorted(groups.values(), key=lambda item: item["role"]),
        "attention": attention,
        "owner_visible_inbox": owner_visible_inbox,
        "queue_waiting": queue_projection,
        "dispatch_diagnostics": dispatch_diagnostics,
        "workers": workers,
        "selected_instance_id": selected,
        "write_boundary": {
            "tab_selection": "read_only_projection",
            "writes": "kernel_action_path_only",
            "manual_assign_default": "disabled_in_autopilot",
        },
    }



























def _agents(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    workdirs_snapshot: list[dict] | None = None,
) -> list[dict]:
    """Runtime-facing agent projection for the Web UI.

    This is a read model over zf.yaml, role_sessions.yaml, events, cost,
    and workdir probes. It intentionally does not expose a control path
    to tmux or backend sessions.
    """
    project_root = _resolve_project_root_for_state(state_dir, project_root)
    roles = _roles(state_dir, config=config)
    workdirs = {
        str(item.get("instance_id") or ""): item
        for item in (
            workdirs_snapshot
            if workdirs_snapshot is not None
            else _workdirs(
                state_dir,
                config=config,
                project_root=project_root,
            )
        )
    }
    sessions = _role_session_ids(state_dir)
    heartbeat = _last_event_by_actor(state_dir, config=config)
    state_by_actor = _worker_states(state_dir, config=config)
    cost_by_instance = _cost_by_instance(state_dir)
    signals = _worker_signal_index(state_dir, config=config)

    out: list[dict] = []
    surface = _operator_agent_surface(
        state_dir,
        config=config,
        project_root=project_root,
    )
    out.append({
        "instance_id": "kanban-agent",
        "parent_role": "kanban-agent",
        "origin": "web_surface",
        "role_type": "kanban-agent",
        "role_kind": "operator",
        "agent_kind": "web_surface",
        "layer": "web_operator",
        "control_scope": "projection_explain_action_request",
        "backend": surface["backend"],
        "model": "",
        "transport": "web-terminal" if surface["terminal_backed"] else "projection",
        "skills": list(surface["capabilities"]),
        "plugins": [],
        "agent": "operator",
        "runtime_state": surface["status"],
        "state": surface["status"],
        "lifecycle_state": "healthy" if surface["status"] == "active" else surface["status"],
        "attention_state": "idle",
        "active_task": "",
        "task_id": "",
        "session_id": surface["session_id"],
        "session_path": str(state_dir / "operator" / "kanban-agent.json"),
        "spawned_at": surface["started_at"],
        "last_heartbeat": "",
        "cost": cost_by_instance.get("kanban-agent", _empty_cost_summary()),
        "workdir": str(project_root),
        "project_path": str(project_root),
        "cwd": str(project_root),
        "worktree_path": str(project_root),
        "branch_or_ref": _git_branch_or_ref(project_root),
        "branch": _git_branch_or_ref(project_root),
        "commit": _git_commit(project_root),
        "dirty": _git_dirty(project_root),
        "workdir_mode": "operator-read-mostly",
        "last_event_seq": 0,
        "last_event_type": "",
        "last_output_summary": "",
        "needs_input_reason": "",
        "provider_stop_reason": "",
        "context_usage_ratio": None,
        "allowed_actions": ["peek", "logs"],
        "capabilities": list(surface["capabilities"]),
        "forbidden": list(surface["forbidden"]),
        "shared_context": surface.get("shared_context", {}),
        "skills_available": surface.get("skills_available", {}),
        "boundary": surface.get("boundary", {}),
        "debug": {
            "transport": "web-terminal" if surface["terminal_backed"] else "projection",
            "log_path": "",
            "briefing_paths": [],
            "attach_hint": "Kanban Agent uses controlled Web actions; no role pane attach",
            "tmux_session": "",
            "tmux_target": "",
            "state_inference": "projection_not_truth",
        },
    })
    has_configured_orchestrator = any(
        str(role.get("instance_id") or role.get("name") or "") == "orchestrator"
        for role in roles
    )
    if config is not None and not has_configured_orchestrator:
        orchestrator_state = "running" if (state_dir / "loop.lock").exists() else "not_running"
        orchestrator_signal = signals.get("orchestrator", {})
        orchestrator_lifecycle = _derive_lifecycle_state(
            orchestrator_state,
            active_task="",
            signal=orchestrator_signal,
        )
        orchestrator_attention = _derive_attention_state(
            lifecycle_state=orchestrator_lifecycle,
            runtime_state=orchestrator_state,
            active_task="",
            signal=orchestrator_signal,
        )
        out.append({
            "instance_id": "orchestrator",
            "parent_role": "orchestrator",
            "origin": "static",
            "role_type": "orchestrator",
            "role_kind": "control",
            "agent_kind": "control",
            "layer": "layer2_brain",
            "control_scope": "planning_dispatch_recovery",
            "backend": config.orchestrator.backend,
            "model": config.orchestrator.model,
            "skills": [],
            "plugins": [],
            "agent": "",
            "runtime_state": state_by_actor.get("orchestrator", orchestrator_state),
            "state": state_by_actor.get("orchestrator", orchestrator_state),
            "lifecycle_state": orchestrator_lifecycle,
            "attention_state": orchestrator_attention,
            "active_task": "",
            "task_id": "",
            "session_id": sessions.get("orchestrator", ""),
            "session_path": "",
            "spawned_at": "",
            "last_heartbeat": heartbeat.get("orchestrator", ""),
            "cost": cost_by_instance.get("orchestrator", _empty_cost_summary()),
            "workdir": str(project_root),
            "project_path": str(project_root),
            "cwd": str(project_root),
            "worktree_path": str(project_root),
            "branch_or_ref": _git_branch_or_ref(project_root),
            "branch": _git_branch_or_ref(project_root),
            "commit": _git_commit(project_root),
            "dirty": _git_dirty(project_root),
            "workdir_mode": "project-root",
            "last_event_seq": orchestrator_signal.get("last_event_seq", 0),
            "last_event_type": orchestrator_signal.get("last_event_type", ""),
            "last_output_summary": orchestrator_signal.get("last_output_summary", ""),
            "needs_input_reason": orchestrator_signal.get("needs_input_reason", ""),
            "provider_stop_reason": orchestrator_signal.get("provider_stop_reason", ""),
            "context_usage_ratio": orchestrator_signal.get("context_usage_ratio"),
            "allowed_actions": _allowed_worker_actions(
                origin="static",
                lifecycle_state=orchestrator_lifecycle,
                attention_state=orchestrator_attention,
                active_task="",
            ),
            "debug": _agent_debug_projection(
                state_dir,
                instance_id="orchestrator",
                transport="tmux",
                config=config,
                project_root=project_root,
            ),
        })

    for role in roles:
        instance_id = str(role.get("instance_id") or "")
        workdir = workdirs.get(instance_id, {})
        role_type = str(role.get("name") or instance_id.split("-")[0])
        role_kind = str(role.get("role_kind") or "")
        agent_kind, layer, control_scope = _agent_classification(role_type, role_kind)
        active_task = str(role.get("active_task") or "")
        runtime_state = str(role.get("state") or "unknown")
        signal = signals.get(instance_id, {})
        freshness = {}
        if active_task:
            try:
                from zf.runtime.long_horizon import project_state_freshness

                freshness = project_state_freshness(
                    state_dir,
                    task_id=active_task,
                    actor=instance_id,
                )
            except Exception:
                freshness = {}
        elif signal:
            freshness = {
                "last_event_at": signal.get("last_activity_at", ""),
                "context_usage_ratio": signal.get("context_usage_ratio"),
            }
        lifecycle_state = _derive_lifecycle_state(
            runtime_state,
            active_task=active_task,
            signal=signal,
        )
        attention_state = _derive_attention_state(
            lifecycle_state=lifecycle_state,
            runtime_state=runtime_state,
            active_task=active_task,
            signal=signal,
        )
        origin = str(role.get("origin") or "static")
        cwd = str(workdir.get("project_path") or workdir.get("workdir") or "")
        out.append({
            "instance_id": instance_id,
            "parent_role": str(role.get("parent_role") or role_type),
            "origin": origin,
            "role_type": role_type,
            "role_kind": role_kind,
            "agent_kind": agent_kind,
            "layer": layer,
            "control_scope": control_scope,
            "backend": role.get("backend", ""),
            "model": role.get("model", ""),
            "transport": role.get("transport", ""),
            "skills": list(role.get("skills", []) or []),
            "plugins": list(role.get("plugins", []) or []),
            "agent": role.get("agent", ""),
            "runtime_state": runtime_state,
            "state": runtime_state,
            "lifecycle_state": lifecycle_state,
            "attention_state": attention_state,
            "active_task": active_task,
            "task_id": active_task,
            "session_id": role.get("session_id", ""),
            "session_path": role.get("session_path", ""),
            "spawned_at": role.get("spawned_at", ""),
            "last_heartbeat": role.get("last_heartbeat", ""),
            "cost": role.get("cost", _empty_cost_summary()),
            "workdir": workdir.get("workdir", ""),
            "project_path": workdir.get("project_path", ""),
            "cwd": cwd,
            "worktree_path": workdir.get("project_path", ""),
            "branch_or_ref": workdir.get("branch_or_ref", ""),
            "branch": workdir.get("branch", ""),
            "commit": workdir.get("commit", ""),
            "dirty": bool(workdir.get("dirty", False)),
            "workdir_mode": workdir.get("mode", ""),
            "last_event_seq": signal.get("last_event_seq", 0),
            "last_event_type": signal.get("last_event_type", ""),
            "last_output_summary": signal.get("last_output_summary", ""),
            "needs_input_reason": signal.get("needs_input_reason", ""),
            "provider_stop_reason": signal.get("provider_stop_reason", ""),
            "context_usage_ratio": signal.get("context_usage_ratio"),
            "freshness": freshness,
            "allowed_actions": _allowed_worker_actions(
                origin=origin,
                lifecycle_state=lifecycle_state,
                attention_state=attention_state,
                active_task=active_task,
            ),
            "debug": _agent_debug_projection(
                state_dir,
                instance_id=instance_id,
                transport=str(role.get("transport", "") or "tmux"),
                config=config,
                project_root=project_root,
            ),
        })
    return out






























def _runtime(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    web_session_token: str | None = None,
) -> dict:
    events_path = state_dir / "events.jsonl"
    providers: dict[str, int] = {}
    roles = list(config.roles) if config is not None else []
    for role in roles:
        providers[role.backend or "unknown"] = providers.get(role.backend or "unknown", 0) + 1
    validation_report = _read_json_file(state_dir / "config" / "validation-report.json")
    web_session = _web_session_projection(web_session_token)
    agent_surface = _operator_agent_surface(
        state_dir,
        config=config,
        project_root=project_root,
    )
    try:
        from zf.runtime.runtime_resources import build_runtime_resource_projection

        resources = build_runtime_resource_projection(
            state_dir,
            config=config,
            project_root=project_root,
        )
    except Exception as exc:
        resources = {
            "schema_version": "runtime-resources.v1",
            "error": str(exc),
        }
    return {
        "mode": "read-only",
        "live": True,
        "state_dir": str(state_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seq": _line_count(events_path) if events_path.exists() else 0,
        "providers": providers,
        "sessions": {
            "count": len(_role_session_ids(state_dir)),
            "tmux_session": getattr(config.session, "tmux_session", "")
            if config is not None else "",
            "tmux_layout": getattr(config.session, "tmux_layout", "")
            if config is not None else "",
        },
        "workdirs": {
            "enabled": bool(config.runtime.workdirs.enabled)
            if config is not None else False,
            "mode": config.runtime.workdirs.mode if config is not None else "",
            "root": config.runtime.workdirs.root if config is not None else "",
        },
        "git": {
            "writer_branch_prefix": config.runtime.git.writer_branch_prefix
            if config is not None else "worker",
            "task_ref_prefix": config.runtime.git.task_ref_prefix
            if config is not None else "task",
            "candidate_branch_prefix": config.runtime.git.candidate_branch_prefix
            if config is not None else "candidate",
        },
        "actions": {
            "mutation_enabled": _web_action_authorization_available(),
            "mutation_mode": _web_mutation_mode(),
            "allowed": sorted(_ALLOWED_WEB_ACTIONS),
            "requires_token": web_session["mode"] == "token_required",
        },
        "web_session": web_session,
        "agent_surface": agent_surface,
        "resources": resources,
        "last_known_good": {
            "exists": (state_dir / "config" / "last-known-good.yaml").exists(),
            "validation": redact_obj(validation_report),
        },
    }


def _web_session_projection(web_session_token: str | None = None) -> dict:
    trusted = os.environ.get("ZF_WEB_TRUSTED_SESSION", "").strip().lower()
    if trusted in {"1", "true", "yes", "local_trusted"}:
        return {
            "mode": "local_trusted",
            "unlocked": True,
            "actions_enabled": True,
            "expires_at": None,
            "requires_token": False,
            "token_fallback_enabled": _web_action_token_configured(),
        }
    if _web_passcode_configured():
        expires_at = _web_session_expires_at(web_session_token)
        unlocked = expires_at is not None
        return {
            "mode": "remote_passcode",
            "unlocked": unlocked,
            "actions_enabled": unlocked,
            "expires_at": expires_at,
            "requires_token": False,
            "token_fallback_enabled": _web_action_token_configured(),
        }
    if _web_action_token_configured():
        return {
            "mode": "token_required",
            "unlocked": False,
            "actions_enabled": False,
            "expires_at": None,
            "requires_token": True,
            "token_fallback_enabled": True,
        }
    return {
        "mode": "read_only",
        "unlocked": False,
        "actions_enabled": False,
        "expires_at": None,
        "requires_token": False,
        "token_fallback_enabled": False,
    }


def _operator_agent_surface(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict:
    project_root = _resolve_project_root_for_state(state_dir, project_root)
    session_path = state_dir / "operator" / "kanban-agent.json"
    session = _read_json_file(session_path)
    if not isinstance(session, dict):
        session = {}
    runtime_session = _operator_session_status(
        state_dir,
        project_root=project_root,
    )
    runtime_has_process = runtime_session.get("status") not in {"idle"} or bool(runtime_session.get("alive"))
    if runtime_has_process:
        merged = dict(session)
        merged.update(runtime_session)
        session = merged
    configured_backend = _canonical_operator_backend(
        os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
        or getattr(getattr(config, "orchestrator", None), "backend", "")
    )
    default_backend = _default_operator_backend(configured_backend)
    backend = _canonical_operator_backend(session.get("backend")) or default_backend
    backends = _operator_backend_options(configured_backend=configured_backend)
    context_task_id = str(session.get("context_task_id") or session.get("task_id") or "")
    status = str(session.get("status") or "active")
    if (
        status == "active"
        and session.get("terminal_backed")
        and not session.get("alive")
        and not runtime_has_process
    ):
        status = "detached"
    last_event_seq = _line_count(state_dir / "events.jsonl")
    operator_workdir = Path(str(session.get("workdir") or (state_dir / "operator" / "workdir")))
    shared_context = kanban_agent_shared_context(
        project_root=project_root,
        state_dir=state_dir,
        operator_workdir=operator_workdir,
    )
    skills_available = _operator_skills_available(
        state_dir,
        config=config,
        project_root=project_root,
    )
    descriptor = dict(session.get("descriptor") or {})
    descriptor.update({
        "scope": "project",
        "task_id": "",
        "context_task_id": context_task_id,
        "backend": backend,
    })
    project_session_id = _operator_session_id(
        state_dir,
        backend=backend,
        scope="project",
        task_id="",
    )
    session_id = str(session.get("session_id") or project_session_id)
    if ":task:" in session_id:
        session_id = project_session_id
    return {
        "id": "kanban-agent",
        "session_id": session_id,
        "status": status,
        "scope": "project",
        "task_id": "",
        "context_task_id": context_task_id,
        "backend": backend,
        "configured_backend": configured_backend or "",
        "default_backend": default_backend,
        "backends": backends,
        "descriptor": descriptor,
        "profile": "operator",
        "terminal_backed": bool(session.get("terminal_backed")) or backend in {
            "deterministic",
            "claude-code",
            "claude",
            "codex",
        },
        "delivery": str(session.get("delivery") or "projection_and_actions"),
        "capabilities": list(KANBAN_AGENT_CAPABILITIES),
        "allowed_actions": list(KANBAN_AGENT_ALLOWED_ACTIONS),
        "forbidden": list(KANBAN_AGENT_FORBIDDEN_CAPABILITIES),
        "forbidden_capabilities": list(KANBAN_AGENT_FORBIDDEN_CAPABILITIES),
        "boundary": kanban_agent_boundary(),
        "status_model": kanban_agent_status_model(),
        "evidence_model": kanban_agent_evidence_model(),
        "shared_context": shared_context,
        "skills_available": skills_available,
        "last_event_seq": last_event_seq,
        "started_at": str(session.get("started_at") or ""),
        "alive": bool(session.get("alive", False)),
        "output_seq": int(session.get("output_seq") or 0),
        "state_dir": str(state_dir),
        "shared_project_workdir": str(project_root),
        "operator_workdir": str(operator_workdir),
        "workdir": str(operator_workdir),
        "transcript_path": str(session.get("transcript_path") or (state_dir / "operator" / "kanban-agent.log")),
    }






















def _operator_session_manager(
    state_dir: Path,
    *,
    project_root: Path,
) -> OperatorSessionManager:
    key = str(Path(state_dir).resolve())
    manager = _OPERATOR_MANAGERS.get(key)
    if manager is None:
        manager = OperatorSessionManager(
            state_dir=Path(state_dir).resolve(),
            project_root=Path(project_root).resolve(),
        )
        _OPERATOR_MANAGERS[key] = manager
    return manager


def _operator_session_status(state_dir: Path, *, project_root: Path) -> dict:
    manager = _operator_session_manager(state_dir, project_root=project_root)
    live = manager.status()
    persisted = _read_json_file(state_dir / "operator" / "kanban-agent.json")
    if live.get("status") == "idle" and persisted:
        merged = dict(persisted)
        context_task_id = str(merged.get("context_task_id") or merged.get("task_id") or "")
        merged["scope"] = "project"
        merged["task_id"] = ""
        merged["context_task_id"] = context_task_id
        merged["session_id"] = _operator_session_id(
            state_dir,
            backend=str(merged.get("backend") or "deterministic"),
            scope="project",
            task_id="",
        )
        merged["alive"] = False
        merged["runtime_status"] = "idle"
        merged.setdefault("transcript_path", str(state_dir / "operator" / "kanban-agent.log"))
        merged.setdefault("workdir", str(state_dir / "operator" / "workdir"))
        operator_workdir = Path(str(merged.get("workdir") or (state_dir / "operator" / "workdir")))
        merged.setdefault("operator_workdir", str(operator_workdir))
        merged.setdefault("shared_project_workdir", str(project_root))
        merged.setdefault("state_dir", str(state_dir))
        merged.setdefault("shared_context", kanban_agent_shared_context(
            project_root=project_root,
            state_dir=state_dir,
            operator_workdir=operator_workdir,
        ))
        merged.setdefault("allowed_actions", list(KANBAN_AGENT_ALLOWED_ACTIONS))
        merged.setdefault("forbidden_capabilities", list(KANBAN_AGENT_FORBIDDEN_CAPABILITIES))
        merged.setdefault("boundary", kanban_agent_boundary())
        merged.setdefault("status_model", kanban_agent_status_model())
        merged.setdefault("evidence_model", kanban_agent_evidence_model())
        return redact_obj(merged)
    return redact_obj(live)


def _operator_input(
    state_dir: Path,
    *,
    project_root: Path,
    project_id: str | None = None,
    text: str,
    authorization: str | None = None,
    x_zf_web_token: str | None = None,
    web_session_token: str | None = None,
    config: ZfConfig | None = None,
) -> dict:
    manager = _operator_session_manager(state_dir, project_root=project_root)
    parsed_action = _operator_action_command(text)
    if parsed_action is not None:
        if not parsed_action.get("ok"):
            manager.append_system(
                f"$ {text.strip()}\naction helper failed: {parsed_action['reason']}\n",
            )
            return {
                "ok": False,
                "status": "invalid_action_command",
                "reason": parsed_action["reason"],
                "session": _operator_session_status(state_dir, project_root=project_root),
            }
        action_name = str(parsed_action["action"])
        action_payload = parsed_action["payload"]
        if project_id:
            action_payload = dict(action_payload)
            action_payload["project_id"] = project_id
        result = _web_action(
            state_dir,
            action_name,
            payload=action_payload,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=web_session_token,
            x_idempotency_key=f"operator-{action_name}-{time.time_ns()}",
            config=config,
            project_root=project_root,
            project_id=project_id,
            legacy_route=not bool(project_id),
            source_session_id="kanban-agent:project",
        )
        status_code = int(result.pop("_status_code", 200))
        redacted = redact_obj(result)
        manager.append_system(
            "$ "
            + text.strip()
            + "\n"
            + "action "
            + action_name
            + " => "
            + json.dumps(redacted, ensure_ascii=False, sort_keys=True)
            + "\n",
        )
        writer = EventWriter(event_log_from_project(state_dir))
        event = writer.emit(
            "operator.action.completed" if result.get("ok") else "operator.action.failed",
            actor="web",
            payload={
                "session_id": "kanban-agent:project",
                "action": action_name,
                "status": result.get("status", ""),
                "status_code": status_code,
                "task_id": result.get("task_id", ""),
                "fanout_id": result.get("fanout_id", ""),
            },
        )
        return {
            **redacted,
            "status_code": status_code,
            "event_id": event.id,
            "session": _operator_session_status(state_dir, project_root=project_root),
        }

    result = manager.write(text)
    writer = EventWriter(event_log_from_project(state_dir))
    event_type = "operator.input.submitted" if result.get("ok") else "operator.input.failed"
    event = writer.emit(
        event_type,
        actor="web",
        payload={
            "session_id": "kanban-agent:project",
            "status": result.get("status", ""),
            "bytes": result.get("bytes", 0),
            "reason": result.get("reason", ""),
        },
    )
    return {
        **redact_obj(result),
        "event_id": event.id,
        "session": _operator_session_status(state_dir, project_root=project_root),
    }




def _operator_stop(state_dir: Path, *, project_root: Path, reason: str) -> dict:
    manager = _operator_session_manager(state_dir, project_root=project_root)
    session = manager.stop(reason=reason)
    session_path = state_dir / "operator" / "kanban-agent.json"
    atomic_write_text(
        session_path,
        json.dumps(redact_obj(session), ensure_ascii=False, indent=2) + "\n",
    )
    writer = EventWriter(event_log_from_project(state_dir))
    event = writer.emit(
        "operator.session.stopped",
        actor="web",
        payload={
            "session_id": "kanban-agent:project",
            "reason": reason,
            "session": redact_obj(session),
        },
    )
    return {
        "ok": True,
        "status": "stopped",
        "reason": reason,
        "event_id": event.id,
        "session": redact_obj(session),
    }












def _web_action(
    state_dir: Path,
    action_name: str,
    *,
    payload: dict,
    authorization: str | None,
    x_zf_web_token: str | None,
    web_session_token: str | None = None,
    x_idempotency_key: str | None = None,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    project_id: str | None = None,
    legacy_route: bool = False,
    source_session_id: str = "",
) -> dict:
    if action_name not in _ALLOWED_WEB_ACTIONS:
        return {
            "_status_code": 404,
            "ok": False,
            "status": "unknown_action",
            "action": action_name,
            "reason": "action is not in the web action allowlist",
        }

    configured_token = os.environ.get("ZF_WEB_ACTION_TOKEN", "")
    trusted_session = _web_trusted_session_enabled()
    passcode_session = _web_session_token_valid(web_session_token)
    if not configured_token and not trusted_session and not _web_passcode_configured():
        return {
            "_status_code": 403,
            "ok": False,
            "status": "disabled",
            "action": action_name,
            "reason": "mutation disabled; set ZF_WEB_ACTION_TOKEN, ZF_WEB_PASSCODE, or ZF_WEB_TRUSTED_SESSION=1 to enable controlled actions",
        }

    supplied = x_zf_web_token or _bearer_token(authorization)
    token_ok = bool(configured_token and supplied == configured_token)
    if not trusted_session and not passcode_session and not token_ok:
        return {
            "_status_code": 403,
            "ok": False,
            "status": "unauthorized",
            "action": action_name,
            "reason": "missing or invalid web action token/session",
        }

    canonical_action = _canonical_action(action_name)
    request_payload = _action_payload(payload)
    idempotency_key = str(
        x_idempotency_key
        or payload.get("idempotency_key")
        or payload.get("request_id")
        or ""
    )
    payload_hash = _payload_hash(request_payload)
    if idempotency_key:
        idempotency_state = _reserve_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
        )
        status = idempotency_state.get("status")
        if status == "replayed":
            response = dict(idempotency_state.get("response") or {})
            response["idempotency"] = {
                "key": idempotency_key,
                "status": "replayed",
            }
            return response
        if status == "pending":
            return {
                "_status_code": 202,
                "ok": True,
                "status": "duplicate_pending",
                "action": canonical_action,
                "requested_action": action_name,
                "reason": "idempotent request is already pending",
                "idempotency": {
                    "key": idempotency_key,
                    "status": "pending",
                },
            }
        if status == "conflict":
            writer = EventWriter(event_log_from_project(state_dir, config=config))
            writer.emit(
                "runtime.action.rejected",
                actor="web",
                task_id=_task_id_from_payload(payload),
                payload={
                    "action": canonical_action,
                    "requested_action": action_name,
                    "idempotency_key": idempotency_key,
                    "reason": "idempotency key already used with a different action or payload",
                },
            )
            return {
                "_status_code": 409,
                "ok": False,
                "status": "idempotency_key_conflict",
                "action": canonical_action,
                "requested_action": action_name,
                "reason": "idempotency key already used with a different action or payload",
                "idempotency": {
                    "key": idempotency_key,
                    "status": "conflict",
                },
            }

    writer = EventWriter(event_log_from_project(state_dir, config=config))
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        task_id=_task_id_from_payload(payload),
        payload={
            "action": canonical_action,
            "requested_action": action_name,
            "idempotency_key": idempotency_key,
            "project_id": project_id or "",
            "legacy_route": legacy_route,
            "source_session_id": source_session_id,
            "request": redact_obj(request_payload),
        },
    )
    validation_error = _validate_action_payload(
        canonical_action,
        request_payload,
        config=config,
    )
    if validation_error:
        writer.emit(
            "runtime.action.rejected",
            actor="web",
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "action": canonical_action,
                "requested_action": action_name,
                "project_id": project_id or "",
                "legacy_route": legacy_route,
                "reason": validation_error,
            },
        )
        response = {
            "_status_code": 422,
            "ok": False,
            "status": "invalid_payload",
            "action": canonical_action,
            "requested_action": action_name,
            "reason": validation_error,
        }
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    writer.emit(
        "runtime.action.accepted",
        actor="web",
        task_id=_task_id_from_payload(payload),
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "action": canonical_action,
            "requested_action": action_name,
            "idempotency_key": idempotency_key,
            "project_id": project_id or "",
            "legacy_route": legacy_route,
        },
    )

    if canonical_action == "ship-candidate":
        try:
            from zf.runtime.ship import ShipService

            service = ShipService(
                state_dir=state_dir,
                project_root=_resolve_project_root_for_state(state_dir, project_root),
                config=config or ZfConfig(),
                event_log=event_log_from_project(state_dir, config=config),
            )
            result = service.ship(
                target_ref=str(
                    request_payload.get("target_ref")
                    or request_payload.get("candidate_ref")
                    or ""
                ),
                pdd_id=str(payload.get("pdd_id") or payload.get("candidate_id") or ""),
                task_id=str(payload.get("task_id") or ""),
                event_writer=writer,
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
            )
        except Exception as exc:
            writer.emit(
                "runtime.action.failed",
                actor="web",
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
                payload={
                    "action": canonical_action,
                    "requested_action": action_name,
                    "reason": str(exc),
                },
            )
            writer.emit(
                "web.action.failed",
                actor="web",
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
                payload={
                    "action": canonical_action,
                    "requested_action": action_name,
                    "reason": str(exc),
                },
            )
            response = {
                "_status_code": 500,
                "ok": False,
                "status": "failed",
                "action": canonical_action,
                "requested_action": action_name,
                "reason": str(exc),
            }
            _complete_idempotency_key(
                state_dir,
                key=idempotency_key,
                action=canonical_action,
                payload_hash=payload_hash,
                response=response,
            )
            return response
        if result.ok:
            writer.emit(
                "runtime.action.completed",
                actor="web",
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
                payload={
                    "action": canonical_action,
                    "requested_action": action_name,
                    "status": result.status,
                    "result": redact_obj(result.payload),
                },
            )
            writer.emit(
                "web.action.completed",
                actor="web",
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
                payload={
                    "action": canonical_action,
                    "requested_action": action_name,
                    "result": redact_obj(result.payload),
                },
            )
            response = {
                "ok": True,
                "status": result.status,
                "action": canonical_action,
                "requested_action": action_name,
                "result": redact_obj(result.payload),
            }
            _complete_idempotency_key(
                state_dir,
                key=idempotency_key,
                action=canonical_action,
                payload_hash=payload_hash,
                response=response,
            )
            return response
        writer.emit(
            "runtime.action.failed",
            actor="web",
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "action": canonical_action,
                "requested_action": action_name,
                "reason": result.status,
                "result": redact_obj(result.payload),
            },
        )
        writer.emit(
            "web.action.failed",
            actor="web",
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "action": canonical_action,
                "requested_action": action_name,
                "reason": result.status,
                "result": redact_obj(result.payload),
            },
        )
        response = {
            "_status_code": 409,
            "ok": False,
            "status": result.status,
            "action": canonical_action,
            "requested_action": action_name,
            "reason": "ship blocked: " + "; ".join(
                str(blocker)
                for blocker in result.payload.get("blockers", []) or [result.status]
            ),
            "result": redact_obj(result.payload),
            "blockers": list(result.payload.get("blockers", []) or []),
        }
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "chat-orchestrator":
        response = _handle_chat_orchestrator(
            state_dir,
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            project_root=project_root,
            config=config,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "agent-session-cancel":
        response = _handle_agent_session_cancel(
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            project_id=project_id or "",
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "start-collaboration":
        response = _handle_start_collaboration(
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action in {"request-fanout", "replan-approve", "replan-defer", "replan-reject", "plan-approve", "plan-reject"}:
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="kanban-agent",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action in _HUMAN_DECISION_ACTIONS:
        response = _handle_human_decision_action(
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            project_id=project_id or "",
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action in {
        "channel-create",
        "channel-post-message",
        "channel-invite-member",
        "channel-update-member-permission",
        "channel-remove-member",
        "channel-delete",
        "channel-clear-history",
        "channel-synthesis",
        "channel-synthesis-request",
        "channel-drain-replies",
        "channel-mark-read",
        "channel-handoff",
        "channel-discussion-mode",
        "channel-owner-report",
        "workflow-invoke",
    }:
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="channel",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "start-operator-session":
        response = _handle_start_operator_session(
            state_dir,
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            config=config,
            project_root=project_root,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action in {"worker-reply", "worker-respawn", "worker-drain"}:
        response = _handle_worker_runtime_action(
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "assignment-propose":
        response = _handle_assignment_propose(
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            project_id=project_id or "",
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "automation-run":
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="automation",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "maintenance-prepare":
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="maintenance",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action in {
        "attention-ack",
        "attention-snooze",
        "attention-resolve",
        "attention-feedback",
        "attention-escalate",
    }:
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="attention",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action in _PROJECT_OPERATOR_CONTROLLED_ACTIONS:
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="kanban-agent",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "create-task":
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="kanban-agent",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action in ("capture-regression-case", "replay-regression-case"):
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="kanban-agent",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "update-task":
        response = ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="kanban-agent",
            surface="web",
        ).execute(
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
            requested=requested,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "decompose-feature":
        response = _handle_decompose_feature(
            state_dir,
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "link-evidence":
        response = _handle_link_evidence(
            state_dir,
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    if canonical_action == "archive-task":
        response = _handle_archive_task(
            state_dir,
            writer,
            requested=requested,
            action=canonical_action,
            requested_action=action_name,
            payload=request_payload,
        )
        _complete_idempotency_key(
            state_dir,
            key=idempotency_key,
            action=canonical_action,
            payload_hash=payload_hash,
            response=response,
        )
        return response

    writer.emit(
        "runtime.action.failed",
        actor="web",
        task_id=_task_id_from_payload(payload),
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "action": canonical_action,
            "requested_action": action_name,
            "reason": "kernel action path not wired yet",
        },
    )
    writer.emit(
        "web.action.failed",
        actor="web",
        task_id=_task_id_from_payload(payload),
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "action": canonical_action,
            "requested_action": action_name,
            "reason": "kernel action path not wired yet",
        },
    )
    response = {
        "_status_code": 501,
        "ok": False,
        "status": "not_implemented",
        "action": canonical_action,
        "requested_action": action_name,
        "reason": "controlled action accepted at the Web boundary but no deterministic kernel service is wired for it yet",
    }
    _complete_idempotency_key(
        state_dir,
        key=idempotency_key,
        action=canonical_action,
        payload_hash=payload_hash,
        response=response,
    )
    return response


def _canonical_action(action_name: str) -> str:
    return _CANONICAL_ACTIONS.get(action_name, action_name)








def _validate_action_payload(
    action: str,
    payload: dict,
    *,
    config: ZfConfig | None = None,
) -> str:
    if action in {
        "dispatch-task",
        "request-verify",
        "rerun-task",
        "request-review",
        "mark-blocked",
    } and not str(payload.get("task_id") or ""):
        return "task_id is required"
    if action == "chat-orchestrator" and not str(payload.get("message") or "").strip():
        return "message is required"
    if action == "chat-orchestrator":
        backend = str(payload.get("backend") or "").strip()
        if backend:
            allowed = {
                "deterministic",
                "codex",
                "claude",
                "claude-code",
                "claude-headless",
                "claude-code-headless",
                "claude_headless",
                "codex-headless",
                "codex-app-server",
                "codex_headless",
            }
            if backend not in allowed:
                return "backend must be deterministic, codex, claude-code, claude-headless, or codex-headless"
    if action in _HUMAN_DECISION_ACTIONS:
        if not str(payload.get("decision_token") or payload.get("approval_ref") or "").strip():
            return "decision_token is required"
    if action == "agent-session-cancel":
        if not str(payload.get("run_id") or payload.get("turn_id") or "").strip():
            return "run_id is required"
        if not str(payload.get("thread_id") or payload.get("thread_key") or "").strip():
            return "thread_id is required"
    if action == "start-collaboration" and not (
        str(payload.get("intent") or "").strip()
        or str(payload.get("message") or "").strip()
    ):
        return "intent or message is required"
    if action == "request-fanout":
        stage_id = str(payload.get("stage_id") or "")
        if not stage_id:
            return "stage_id is required"
        stage = _workflow_stage(config, stage_id)
        if stage is None:
            return f"fanout stage {stage_id!r} is not declared in zf.yaml"
        if not str(stage.topology or "").startswith("fanout_"):
            return f"workflow stage {stage_id!r} is not a fanout topology"
    if action == "channel-post-message":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("text") or payload.get("message") or "").strip():
            return "text is required"
    if action == "channel-create":
        if not str(payload.get("name") or payload.get("channel_name") or "").strip():
            return "name is required"
    if action == "channel-invite-member":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("member_id") or "").strip():
            return "member_id is required"
        contract_error = validate_channel_member_contract(payload)
        if contract_error:
            return contract_error
    if action == "channel-update-member-permission":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("member_id") or "").strip():
            return "member_id is required"
        if not str(payload.get("permission_profile") or "").strip():
            return "permission_profile is required"
        contract_error = validate_channel_member_contract(payload)
        if contract_error:
            return contract_error
    if action == "channel-remove-member":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("member_id") or "").strip():
            return "member_id is required"
    if action in {"channel-delete", "channel-clear-history", "channel-mark-read"}:
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
    if action == "channel-synthesis":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("decision") or "").strip():
            return "decision is required"
        if not str(payload.get("summary") or "").strip():
            return "summary is required"
    if action == "channel-synthesis-request":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
    if action == "workflow-invoke":
        if not str(payload.get("task_id") or "").strip():
            return "task_id is required"
        pattern_id = str(payload.get("pattern_id") or "").strip()
        if not pattern_id:
            return "pattern_id is required"
        stage = _workflow_stage(config, pattern_id)
        if stage is None:
            return f"execution pattern {pattern_id!r} is not declared in zf.yaml"
    if action == "assignment-propose":
        if not str(payload.get("task_id") or "").strip():
            return "task_id is required"
        assignee_type = str(payload.get("assignee_type") or "").strip().lower()
        if assignee_type and assignee_type not in {"agent", "squad"}:
            return "assignee_type must be agent or squad"
        if not (
            str(payload.get("role") or payload.get("assigned_to") or "").strip()
            or str(payload.get("assignee_id") or "").strip()
            or str(payload.get("backend") or "").strip()
            or str(payload.get("channel_id") or "").strip()
            or str(payload.get("supervisor") or "").strip()
        ):
            return "role, assignee_id, backend, channel_id, or supervisor is required"
    if action == "automation-run":
        automation_id = str(payload.get("automation_id") or payload.get("id") or "").strip()
        if not automation_id:
            return "automation_id is required"
        if automation_id not in AUTOMATIONS:
            return "automation_id must be one of " + ", ".join(AUTOMATIONS)
        trigger = str(payload.get("trigger") or "").strip()
        if trigger and trigger not in {"manual", "schedule", "event-window", "webhook"}:
            return "trigger must be one of event-window, manual, schedule, webhook"
    if action == "maintenance-prepare":
        if not str(payload.get("trigger_id") or payload.get("trigger") or payload.get("proposal_id") or "").strip():
            return "trigger_id is required"
        if (
            payload.get("checkpoint")
            or payload.get("create_checkpoint")
            or payload.get("checkpoint_required")
        ) and not str(payload.get("task_id") or "").strip():
            return "task_id is required when checkpoint is requested"
    if action in {
        "attention-ack",
        "attention-snooze",
        "attention-resolve",
        "attention-feedback",
        "attention-escalate",
    }:
        if not str(payload.get("attention_id") or payload.get("fingerprint") or "").strip():
            return "attention_id or fingerprint is required"
        if action == "attention-snooze" and not str(payload.get("snooze_until") or "").strip():
            return "snooze_until is required"
    if action == "operator-intent-create":
        if not (
            str(payload.get("objective") or "").strip()
            or str(payload.get("message") or "").strip()
            or str(payload.get("text") or "").strip()
        ):
            return "objective or message is required"
    if action in {"operator-intent-approve", "operator-intent-reject"}:
        if not str(payload.get("intent_id") or "").strip():
            return "intent_id is required"
    if action in {"replan-approve", "replan-defer", "replan-reject"}:
        if not str(payload.get("proposal_ref") or payload.get("artifact_ref") or "").strip():
            return "proposal_ref is required"
        if not str(payload.get("eval_ref") or payload.get("eval_id") or "").strip():
            return "eval_ref is required"
    if action == "workflow-batch-resume":
        if not str(payload.get("checkpoint_id") or "").strip():
            return "checkpoint_id is required"
        if not str(payload.get("safe_resume_action") or "").strip():
            return "safe_resume_action is required"
        if (
            str(payload.get("safe_resume_action") or "").strip() == "trigger_rework"
            and not bool(payload.get("mutating_resume_supported"))
        ):
            return "trigger_rework requires explicit mutating_resume_supported"
    if action == "candidate-rework-apply":
        rework_action = str(payload.get("candidate_rework_action") or "").strip()
        if rework_action not in {"retrigger", "replan", "escalate"}:
            return "candidate_rework_action must be retrigger, replan, or escalate"
        if not str(payload.get("checkpoint_id") or "").strip():
            return "checkpoint_id is required"
        if not str(payload.get("pdd_id") or "").strip():
            return "pdd_id is required"
        if not str(payload.get("source_event_id") or "").strip():
            return "source_event_id is required"
        if rework_action == "retrigger":
            for key in ("task_map_ref", "source_commit", "candidate_base_commit"):
                if not str(payload.get(key) or "").strip():
                    return f"{key} is required"
    if action == "idea-to-product":
        if not (
            str(payload.get("objective") or "").strip()
            or str(payload.get("message") or "").strip()
            or str(payload.get("title") or "").strip()
        ):
            return "objective or message is required"
    if action in {"provider-dev-chat-start", "provider-dev-chat-send"}:
        if not (
            str(payload.get("message") or "").strip()
            or str(payload.get("objective") or "").strip()
        ):
            return "message or objective is required"
    if action == "workflow-config-propose":
        if not (
            str(payload.get("objective") or "").strip()
            or str(payload.get("message") or "").strip()
            or str(payload.get("patch_ref") or "").strip()
        ):
            return "objective, message, or patch_ref is required"
    if action == "workflow-config-validate":
        if not (
            str(payload.get("proposal_id") or "").strip()
            or str(payload.get("patch_ref") or "").strip()
        ):
            return "proposal_id or patch_ref is required"
    if action == "workflow-config-apply":
        if not str(payload.get("patch_ref") or "").strip():
            return "patch_ref is required"
        if not str(payload.get("validation_result_ref") or "").strip():
            return "validation_result_ref is required"
    if action == "channel-drain-replies":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
    if action == "channel-handoff":
        for key in ("channel_id", "message_id", "member_id", "target_member_id", "reason"):
            if not str(payload.get(key) or "").strip():
                return f"{key} is required"
    if action == "channel-discussion-mode":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        mode = str(payload.get("mode") or "").strip()
        if mode not in _CHANNEL_DISCUSSION_MODES:
            return "mode must be one of " + ", ".join(sorted(_CHANNEL_DISCUSSION_MODES))
    if action == "channel-owner-report":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("owner_id") or "").strip():
            return "owner_id is required"
    if action == "start-operator-session":
        backend = str(payload.get("backend") or "").strip()
        if backend and backend not in {"deterministic", "codex", "claude", "claude-code"}:
            return "backend must be deterministic, codex, claude, or claude-code"
    if action == "create-task" and not str(payload.get("title") or "").strip():
        return "title is required"
    if action == "update-task" and not str(payload.get("task_id") or "").strip():
        return "task_id is required"
    if action == "archive-task" and not str(payload.get("task_id") or "").strip():
        return "task_id is required"
    if action == "decompose-feature":
        titles = payload.get("tasks") or payload.get("titles")
        if not isinstance(titles, list) or not any(str(item).strip() for item in titles):
            return "tasks must contain at least one title"
    if action == "link-evidence" and not str(payload.get("task_id") or "").strip():
        return "task_id is required"
    if action in {"worker-reply", "worker-respawn", "worker-drain"}:
        instance_id = str(
            payload.get("instance_id")
            or payload.get("worker")
            or payload.get("role")
            or ""
        ).strip()
        if not instance_id:
            return "instance_id is required"
    if action == "worker-reply" and not str(
        payload.get("message") or payload.get("text") or ""
    ).strip():
        return "message is required"
    return ""


def _handle_worker_runtime_action(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
) -> dict:
    task_id = _task_id_from_payload(payload)
    instance_id = str(
        payload.get("instance_id")
        or payload.get("worker")
        or payload.get("role")
        or ""
    ).strip()
    event_type = {
        "worker-reply": "worker.reply.requested",
        "worker-respawn": "worker.respawn.requested",
        "worker-drain": "worker.drain.requested",
    }[action]
    worker_event = writer.emit(
        event_type,
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "instance_id": instance_id,
            "task_id": task_id or "",
            "request": redact_obj(payload),
            **(
                {
                    "message": str(
                        payload.get("message") or payload.get("text") or ""
                    )
                }
                if action == "worker-reply"
                else {}
            ),
            **(
                {"reason": str(payload.get("reason") or "operator_request")}
                if action == "worker-drain"
                else {}
            ),
        },
    )
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=worker_event.id,
        correlation_id=worker_event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "queued",
            "event_type": event_type,
            "event_id": worker_event.id,
            "instance_id": instance_id,
        },
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=worker_event.id,
        correlation_id=worker_event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "queued",
            "event_type": event_type,
            "event_id": worker_event.id,
            "instance_id": instance_id,
        },
    )
    return {
        "_status_code": 202,
        "ok": True,
        "status": "queued",
        "action": action,
        "requested_action": requested_action,
        "event_type": event_type,
        "event_id": worker_event.id,
        "instance_id": instance_id,
    }


def _handle_human_decision_action(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    project_id: str = "",
) -> dict:
    decision_token = _human_decision_token_from_payload(payload)
    decision = _HUMAN_DECISION_BY_ACTION[action]
    task_id = _task_id_from_payload(payload)
    acknowledged = writer.emit(
        "human.escalation.acknowledged",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "schema_version": "human-escalation-acknowledged.v1",
            "decision_token": decision_token,
            "decision": decision,
            "source": str(payload.get("source") or "operator-inbox"),
            "surface": "web",
            "project_id": project_id,
            "approval_ref": str(payload.get("approval_ref") or ""),
            "checkpoint_id": str(payload.get("checkpoint_id") or ""),
            "fingerprint": str(payload.get("fingerprint") or ""),
            "created_event_id": str(payload.get("created_event_id") or ""),
        },
    )
    completion_payload = {
        "action": action,
        "requested_action": requested_action,
        "status": "acknowledged",
        "event_type": "human.escalation.acknowledged",
        "event_id": acknowledged.id,
        "decision_token": decision_token,
        "decision": decision,
        "project_id": project_id,
    }
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=acknowledged.id,
        correlation_id=acknowledged.correlation_id,
        payload=completion_payload,
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=acknowledged.id,
        correlation_id=acknowledged.correlation_id,
        payload=completion_payload,
    )
    return {
        "_status_code": 202,
        "ok": True,
        "status": "acknowledged",
        "action": action,
        "requested_action": requested_action,
        "event_type": "human.escalation.acknowledged",
        "event_id": acknowledged.id,
        "decision_token": decision_token,
        "decision": decision,
    }


def _human_decision_token_from_payload(payload: dict) -> str:
    raw = str(payload.get("decision_token") or payload.get("approval_ref") or "")
    if raw.startswith("human:"):
        raw = raw.removeprefix("human:")
    return raw


def _handle_assignment_propose(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    project_id: str = "",
) -> dict:
    task_id = _task_id_from_payload(payload)
    request = redact_obj(payload)
    role = str(payload.get("role") or payload.get("assigned_to") or "").strip()
    backend = str(payload.get("backend") or "").strip()
    channel_id = str(payload.get("channel_id") or "").strip()
    supervisor = str(payload.get("supervisor") or "").strip()
    assignee_type = str(payload.get("assignee_type") or "").strip().lower()
    if not assignee_type:
        if channel_id:
            assignee_type = "squad"
        elif role or backend:
            assignee_type = "agent"
    assignee_id = str(payload.get("assignee_id") or "").strip()
    if not assignee_id:
        assignee_id = channel_id if assignee_type == "squad" else role
    assignee_label = str(payload.get("assignee_label") or "").strip() or assignee_id
    proposal_seed = {
        "project_id": project_id,
        "task_id": task_id or "",
        "assignee_type": assignee_type,
        "assignee_id": assignee_id,
        "assignee_label": assignee_label,
        "role": role,
        "backend": backend,
        "channel_id": channel_id,
        "supervisor": supervisor,
        "reason": str(payload.get("reason") or ""),
    }
    proposal_id = str(payload.get("proposal_id") or "").strip()
    if not proposal_id:
        digest = hashlib.sha1(
            json.dumps(
                proposal_seed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        ).hexdigest()[:12]
        proposal_id = f"assign-{digest}"
    event = writer.emit(
        "assignment.intent.proposed",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "proposal_id": proposal_id,
            "project_id": project_id,
            "task_id": task_id or "",
            "assignee_type": assignee_type,
            "assignee_id": assignee_id,
            "assignee_label": assignee_label,
            "role": role,
            "backend": backend,
            "channel_id": channel_id,
            "supervisor": supervisor,
            "reason": str(payload.get("reason") or "operator assignment intent"),
            "dispatches": False,
            "request": request,
        },
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=event,
        action=action,
        requested_action=requested_action,
        status="proposed",
        task_id=task_id,
        extra={
            "proposal_id": proposal_id,
            "event_type": "assignment.intent.proposed",
            "event_id": event.id,
        },
    )
    return {
        "_status_code": 202,
        "ok": True,
        "status": "proposed",
        "action": action,
        "requested_action": requested_action,
        "reason": "assignment intent recorded; runtime dispatch is not changed by this proposal",
        "proposal_id": proposal_id,
        "event_id": event.id,
        "task_id": task_id or "",
    }


def _handle_chat_orchestrator(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    project_root: Path | None = None,
    config: ZfConfig | None = None,
) -> dict:
    message = str(payload.get("message") or "").strip()
    task_id = _task_id_from_payload(payload)
    headless_backend = canonical_headless_backend(str(payload.get("backend") or ""))
    user_message = writer.emit(
        "user.message",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        payload={
            "source": "kanban",
            "target": "kanban-agent" if headless_backend else "orchestrator",
            "message": message,
            "runtime_delivery": "headless" if headless_backend else "queued_no_runtime",
            "backend": headless_backend or str(payload.get("backend") or ""),
            "project_id": str(payload.get("project_id") or ""),
            "conversation_id": str(payload.get("conversation_id") or ""),
            "thread_key": str(payload.get("thread_key") or ""),
            "request": redact_obj(payload),
        },
    )
    if headless_backend:
        return _handle_headless_kanban_agent_chat(
            state_dir,
            writer,
            requested=requested,
            user_message=user_message,
            action=action,
            requested_action=requested_action,
            payload=payload,
            message=message,
            backend=headless_backend,
            project_root=_resolve_project_root_for_state(state_dir, project_root),
            config=config,
        )
    if _is_lifecycle_probe_request(payload, message):
        return _handle_kanban_agent_lifecycle_probe(
            state_dir,
            writer,
            requested=requested,
            user_message=user_message,
            action=action,
            requested_action=requested_action,
            payload=payload,
            message=message,
        )
    reply_event: ZfEvent | None = None
    reply = _projection_reply_if_requested(state_dir, payload, message, task_id)
    if reply:
        reply_event = writer.emit(
            "kanban.agent.reply",
            actor="web",
            task_id=task_id,
            causation_id=user_message.id,
            correlation_id=user_message.correlation_id,
            payload=reply,
        )
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id if reply_event is not None else user_message.id,
        correlation_id=user_message.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "queued_no_runtime",
            "message_event_id": user_message.id,
            "reply_event_id": reply_event.id if reply_event is not None else "",
        },
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id if reply_event is not None else user_message.id,
        correlation_id=user_message.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "queued_no_runtime",
            "reply_event_id": reply_event.id if reply_event is not None else "",
        },
    )
    response = {
        "_status_code": 202,
        "ok": True,
        "status": "queued_no_runtime",
        "action": action,
        "requested_action": requested_action,
        "reason": "message recorded for orchestrator; Web does not attach to the tmux runtime",
        "event_id": user_message.id,
        "trace_id": user_message.correlation_id,
    }
    if reply_event is not None:
        response["reply_event_id"] = reply_event.id
        response["reply"] = redact_obj(reply)
    return response


def _handle_agent_session_cancel(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    project_id: str,
) -> dict:
    task_id = _task_id_from_payload(payload)
    run_id = str(payload.get("run_id") or payload.get("turn_id") or "").strip()
    thread_id = str(payload.get("thread_id") or payload.get("thread_key") or "").strip()
    cancel_result = cancel_agent_session_run(run_key(
        run_id=run_id,
        thread_id=thread_id,
        project_id=project_id,
        conversation_id=str(payload.get("conversation_id") or ""),
    ))
    event = writer.emit(
        "agent.session.run.cancelled",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=str(payload.get("conversation_id") or project_id or requested.correlation_id),
        payload={
            "project_id": project_id,
            "conversation_id": str(payload.get("conversation_id") or ""),
            "thread_id": thread_id,
            "run_id": run_id,
            "provider": str(payload.get("backend") or payload.get("provider") or ""),
            "reason": str(payload.get("reason") or "operator cancelled agent session run"),
            "status": cancel_result.status,
            "interrupt_supported": cancel_result.interrupt_supported,
            "process_found": cancel_result.process_found,
            "process_terminated": cancel_result.process_terminated,
            "pid": cancel_result.pid or "",
            "source": "web",
        },
    )
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "cancel_requested",
            "run_id": run_id,
            "thread_id": thread_id,
            "interrupt_status": cancel_result.status,
            "process_terminated": cancel_result.process_terminated,
        },
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "cancel_requested",
            "run_id": run_id,
            "thread_id": thread_id,
            "interrupt_status": cancel_result.status,
            "process_terminated": cancel_result.process_terminated,
        },
    )
    return {
        "ok": True,
        "status": cancel_result.status,
        "action": action,
        "requested_action": requested_action,
        "reason": cancel_result.reason or "agent session cancel request recorded; provider interrupt is best-effort",
        "event_id": event.id,
        "run_id": run_id,
        "thread_id": thread_id,
        "interrupt_supported": cancel_result.interrupt_supported,
        "process_found": cancel_result.process_found,
        "process_terminated": cancel_result.process_terminated,
        "pid": cancel_result.pid,
    }


def _handle_headless_kanban_agent_chat(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    user_message: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    message: str,
    backend: str,
    project_root: Path,
    config: ZfConfig | None = None,
) -> dict:
    task_id = _task_id_from_payload(payload)
    thread_key = str(payload.get("thread_key") or "").strip()
    turn_id = str(payload.get("turn_id") or uuid.uuid4())
    project_id = str(payload.get("project_id") or "")
    conversation_id = str(payload.get("conversation_id") or "")
    run_thread_id = thread_key or str(payload.get("thread_id") or "").strip() or "main"
    begin_agent_session_run(
        run_key(
            run_id=turn_id,
            thread_id=run_thread_id,
            project_id=project_id,
            conversation_id=conversation_id,
        ),
        provider=backend,
    )
    turn_created = writer.emit(
        "kanban.agent.turn.created",
        actor="web",
        task_id=task_id,
        causation_id=user_message.id,
        correlation_id=user_message.correlation_id,
        payload={
            "turn_id": turn_id,
            "thread_key": thread_key,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "backend": backend,
            "scope": str(payload.get("scope") or "project"),
            "message_event_id": user_message.id,
            "requested_action": requested_action,
        },
    )
    turn_started = writer.emit(
        "kanban.agent.turn.started",
        actor="web",
        task_id=task_id,
        causation_id=turn_created.id,
        correlation_id=user_message.correlation_id,
        payload={
            "turn_id": turn_id,
            "thread_key": thread_key,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "backend": backend,
            "message_event_id": user_message.id,
        },
    )
    thinking_level = _headless_thinking_level(payload)
    runner_kwargs = {
        "state_dir": state_dir,
        "writer": writer,
        "user_message": user_message,
        "turn_started": turn_started,
        "action": action,
        "requested_action": requested_action,
        "payload": dict(payload),
        "message": message,
        "backend": backend,
        "project_root": project_root,
        "task_id": task_id,
        "thread_key": thread_key,
        "turn_id": turn_id,
        "run_thread_id": run_thread_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "thinking_level": thinking_level,
    }
    if _headless_chat_sync(payload):
        return _run_headless_kanban_agent_turn(**runner_kwargs)

    def run_background() -> None:
        bg_writer = EventWriter(event_log_from_project(state_dir, config=config))
        _run_headless_kanban_agent_turn(
            **{
                **runner_kwargs,
                "writer": bg_writer,
            }
        )

    threading.Thread(
        target=run_background,
        name=f"zf-kanban-agent-{turn_id[:8]}",
        daemon=True,
    ).start()
    return {
        "_status_code": 202,
        "ok": True,
        "status": "accepted",
        "action": action,
        "requested_action": requested_action,
        "reason": "headless kanban agent turn accepted; stream events carry progress",
        "event_id": user_message.id,
        "turn_event_id": turn_started.id,
        "turn_id": turn_id,
        "thread_key": thread_key,
        "trace_id": user_message.correlation_id,
        "backend": backend,
    }


def _headless_chat_sync(payload: dict) -> bool:
    if payload.get("sync") is True:
        return True
    return os.environ.get("ZF_KANBAN_AGENT_HEADLESS_SYNC", "").strip() in {"1", "true", "yes"}


def _headless_thinking_level(payload: dict) -> str:
    return str(
        payload.get("thinking_level")
        or os.environ.get("ZF_KANBAN_AGENT_HEADLESS_THINKING_LEVEL", "")
    ).strip()


_HEADLESS_STREAM_FLUSH_INTERVAL_S = 0.15


def _headless_stream_flush_interval_s() -> float:
    raw = str(
        os.environ.get("ZF_KANBAN_AGENT_STREAM_FLUSH_INTERVAL_S")
        or os.environ.get("ZF_KANBAN_AGENT_HEADLESS_STREAM_FLUSH_INTERVAL_S")
        or _HEADLESS_STREAM_FLUSH_INTERVAL_S
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        return _HEADLESS_STREAM_FLUSH_INTERVAL_S
    return min(max(value, 0.05), 2.0)


class _HeadlessDeltaEmitter:
    def __init__(
        self,
        *,
        writer: EventWriter,
        task_id: str | None,
        turn_started: ZfEvent,
        user_message: ZfEvent,
        turn_id: str,
        thread_key: str,
        project_id: str,
        conversation_id: str,
        backend: str,
        agent_session_emitter: AgentSessionStreamEmitter | None = None,
        flush_interval_s: float = _HEADLESS_STREAM_FLUSH_INTERVAL_S,
    ) -> None:
        self.writer = writer
        self.task_id = task_id
        self.turn_started = turn_started
        self.user_message = user_message
        self.turn_id = turn_id
        self.thread_key = thread_key
        self.project_id = project_id
        self.conversation_id = conversation_id
        self.backend = backend
        self.agent_session_emitter = agent_session_emitter
        self.flush_interval_s = flush_interval_s
        self.delta_seq = 0
        self._pending_text: list[str] = []
        self._pending_thinking: list[str] = []
        self._last_flush_at = time.monotonic()
        self._content_started = False

    def emit(self, message: HeadlessMessage) -> None:
        if self.agent_session_emitter is not None:
            self.agent_session_emitter.emit_message(message)
        if message.type == "text":
            if message.content:
                self._pending_text.append(message.content)
            self._flush_first_content_or_due()
            return
        if message.type == "thinking":
            if message.content:
                self._pending_thinking.append(message.content)
            self._flush_first_content_or_due()
            return
        self.flush()
        self._emit_one(message)

    def flush(self) -> None:
        if self.agent_session_emitter is not None:
            self.agent_session_emitter.flush()
        if self._pending_thinking:
            content = "".join(self._pending_thinking)
            self._pending_thinking.clear()
            self._emit_one(HeadlessMessage(type="thinking", content=content))
        if self._pending_text:
            content = "".join(self._pending_text)
            self._pending_text.clear()
            self._emit_one(HeadlessMessage(type="text", content=content))
        self._last_flush_at = time.monotonic()

    def _flush_if_due(self) -> None:
        if time.monotonic() - self._last_flush_at >= self.flush_interval_s:
            self.flush()

    def _flush_first_content_or_due(self) -> None:
        if not self._content_started:
            self._content_started = True
            self.flush()
            return
        self._flush_if_due()

    def _emit_one(self, message: HeadlessMessage) -> None:
        self.delta_seq += 1
        payload = {
            "turn_id": self.turn_id,
            "thread_key": self.thread_key,
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "backend": self.backend,
            "seq": self.delta_seq,
            **_headless_message_event_payload(message),
        }
        try:
            from zf.runtime.agent_session_output import apply_agent_output_contract

            event_log_path = getattr(getattr(self.writer, "event_log", None), "path", None)
            if event_log_path is not None:
                payload = apply_agent_output_contract(
                    Path(event_log_path).parent,
                    payload,
                    text_keys=("content", "output"),
                    metadata={
                        "source": "kanban-agent.headless",
                        "producer": "web",
                        "run_id": self.turn_id,
                        "turn_id": self.turn_id,
                        "thread_id": self.thread_key,
                        "part_id": f"kanban-delta-{self.delta_seq:04d}",
                        "message_type": message.type,
                        "seq": self.delta_seq,
                        "project_id": self.project_id,
                        "conversation_id": self.conversation_id,
                        "task_id": self.task_id or "",
                    },
                )
        except Exception:
            pass
        self.writer.emit(
            "kanban.agent.turn.delta",
            actor="web",
            task_id=self.task_id,
            causation_id=self.turn_started.id,
            correlation_id=self.user_message.correlation_id,
            payload=payload,
        )
        self.writer.emit(
            "kanban.agent.message.delta",
            actor="web",
            task_id=self.task_id,
            causation_id=self.turn_started.id,
            correlation_id=self.user_message.correlation_id,
            payload={**payload, "source_event_type": "kanban.agent.turn.delta"},
        )


def _run_headless_kanban_agent_turn(
    *,
    state_dir: Path,
    writer: EventWriter,
    user_message: ZfEvent,
    turn_started: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    message: str,
    backend: str,
    project_root: Path,
    task_id: str | None,
    thread_key: str,
    turn_id: str,
    run_thread_id: str,
    project_id: str,
    conversation_id: str,
    thinking_level: str = "",
) -> dict:
    runtime_snapshot_ref = ""
    try:
        from types import SimpleNamespace

        from zf.core.task.store import TaskStore
        from zf.runtime.runtime_snapshot import (
            RuntimeSnapshotInput,
            build_runtime_snapshot,
            runtime_snapshot_event_payload,
            write_runtime_snapshot,
        )

        task = TaskStore(state_dir / "kanban.json").get(task_id) if task_id else None
        role = SimpleNamespace(
            name="kanban-agent",
            instance_id="kanban-agent",
            role_kind="operator",
            backend=backend,
            publishes=[],
        )
        snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
            state_dir=state_dir,
            project_root=project_root,
            project_id=project_id,
            source="headless_run",
            task=task,
            role=role,
            dispatch_id=turn_id,
            run_id=turn_id,
            trace_id=user_message.correlation_id or "",
            refs={
                "headless_thread_ref": (
                    state_dir
                    / "operator"
                    / "threads"
                    / f"{run_thread_id or thread_key or 'main'}.json"
                ),
            },
            output_contract={
                "expected_event": "kanban.agent.reply",
                "verification_tiers": [],
                "evidence_contract": {},
            },
        ))
        snapshot_result = write_runtime_snapshot(
            snapshot,
            state_dir=state_dir,
            project_root=project_root,
        )
        runtime_snapshot_ref = snapshot_result.snapshot_ref
        writer.append(ZfEvent(
            type="runtime.snapshot.recorded",
            actor="web",
            task_id=task_id,
            payload=runtime_snapshot_event_payload(snapshot_result),
            causation_id=turn_started.id,
            correlation_id=user_message.correlation_id,
        ))
    except Exception as snapshot_exc:
        try:
            writer.append(ZfEvent(
                type="runtime.snapshot.invalid",
                actor="web",
                task_id=task_id,
                payload={
                    "source": "headless_run",
                    "reason": str(snapshot_exc),
                    "run_id": turn_id,
                },
                causation_id=turn_started.id,
                correlation_id=user_message.correlation_id,
            ))
        except Exception:
            pass
    stream_flush_interval_s = _headless_stream_flush_interval_s()
    agent_stream = AgentSessionStreamEmitter(
        writer=writer,
        identity=AgentSessionIdentity(
            run_id=turn_id,
            thread_id=run_thread_id or thread_key or "main",
            source="kanban-agent.headless",
            actor="web",
            task_id=task_id,
            causation_id=turn_started.id,
            correlation_id=user_message.correlation_id,
            project_id=project_id,
            conversation_id=conversation_id,
            message_id=user_message.id,
            member_id="kanban-agent",
            target_member_id="kanban-agent",
            provider=backend,
            backend=backend,
            snapshot_ref=runtime_snapshot_ref,
        ),
        flush_interval_s=stream_flush_interval_s,
    )
    agent_stream.start()
    delta_emitter = _HeadlessDeltaEmitter(
        writer=writer,
        task_id=task_id,
        turn_started=turn_started,
        user_message=user_message,
        turn_id=turn_id,
        thread_key=thread_key,
        project_id=project_id,
        conversation_id=conversation_id,
        backend=backend,
        agent_session_emitter=agent_stream,
        flush_interval_s=stream_flush_interval_s,
    )

    try:
        agent = KanbanHeadlessAgent(
            state_dir=state_dir,
            project_root=project_root,
        )
        result = agent.run_turn(
            backend=backend,
            message=message,
            scope=str(payload.get("scope") or "project"),
            task_id=task_id or "",
            thread_key=thread_key,
            context={
                "trace_id": str(payload.get("trace_id") or ""),
                "pdd_id": str(payload.get("pdd_id") or ""),
                "fanout_id": str(payload.get("fanout_id") or ""),
                "requested_action": requested_action,
                "thread_key": thread_key,
                "turn_id": turn_id,
                "run_thread_id": run_thread_id,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "runtime_snapshot_ref": runtime_snapshot_ref,
            },
            on_message=delta_emitter.emit,
            thinking_level=thinking_level,
        )
        delta_emitter.flush()
    except Exception as exc:
        delta_emitter.flush()
        reason = str(exc)
        agent_stream.fail(reason=reason, status="failed")
        writer.emit(
            "kanban.agent.turn.failed",
            actor="web",
            task_id=task_id,
            causation_id=turn_started.id,
            correlation_id=user_message.correlation_id,
            payload={
                "turn_id": turn_id,
                "thread_key": thread_key,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "backend": backend,
                "status": "failed",
                "reason": reason,
                "delta_count": delta_emitter.delta_seq,
            },
        )
        writer.emit(
            "runtime.action.failed",
            actor="web",
            task_id=task_id,
            causation_id=turn_started.id,
            correlation_id=user_message.correlation_id,
            payload={
                "action": action,
                "requested_action": requested_action,
                "status": "failed",
                "backend": backend,
                "reason": reason,
                "message_event_id": user_message.id,
            },
        )
        writer.emit(
            "web.action.failed",
            actor="web",
            task_id=task_id,
            causation_id=turn_started.id,
            correlation_id=user_message.correlation_id,
            payload={
                "action": action,
                "requested_action": requested_action,
                "status": "failed",
                "backend": backend,
                "reason": reason,
            },
        )
        return {
            "_status_code": 503,
            "ok": False,
            "status": "failed",
            "action": action,
            "requested_action": requested_action,
            "reason": reason,
            "event_id": user_message.id,
            "turn_id": turn_id,
            "thread_key": thread_key,
        }
    action_proposal = _headless_action_proposal(
        result.reply,
        user_message=message,
        proposal_context={
            "project_id": project_id,
            "conversation_id": conversation_id,
            "thread_id": thread_key or result.thread_id,
            "run_id": turn_id,
            "causation_id": user_message.id,
        },
    )
    reply = {
        "source": "kanban-agent.headless",
        "backend": result.backend,
        "answer": result.reply,
        "mutates_task_state": False,
        "turn_id": turn_id,
        "thread_key": thread_key,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "thread_id": result.thread_id,
        "provider_session_id": result.provider_session_id,
        "resumed": result.resumed,
        "fallback_reason": result.fallback_reason,
        "usage": result.usage,
        "status": result.status,
        "error": result.error,
    }
    try:
        from zf.runtime.agent_session_output import apply_agent_output_contract

        reply = apply_agent_output_contract(
            state_dir,
            reply,
            text_keys=("answer", "error"),
            metadata={
                "source": "kanban-agent.headless",
                "producer": "web",
                "run_id": turn_id,
                "turn_id": turn_id,
                "thread_id": thread_key,
                "part_id": "final-reply",
                "message_type": "reply",
                "project_id": project_id,
                "conversation_id": conversation_id,
                "task_id": task_id or "",
            },
        )
    except Exception:
        pass
    if action_proposal is not None:
        reply["action_proposal"] = action_proposal
    reply_event = writer.emit(
        "kanban.agent.reply",
        actor="web",
        task_id=task_id,
        causation_id=user_message.id,
        correlation_id=user_message.correlation_id,
        payload=redact_obj(reply),
    )
    if action_proposal is not None:
        writer.emit(
            "kanban.agent.action.proposed",
            actor="web",
            task_id=task_id,
            causation_id=reply_event.id,
            correlation_id=user_message.correlation_id,
            payload={
                "turn_id": turn_id,
                "thread_key": thread_key,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "reply_event_id": reply_event.id,
                "proposal": redact_obj(action_proposal),
            },
        )
    if not result.ok:
        agent_stream.fail(
            reason=result.error or result.status,
            status=result.status,
            provider_session_id=result.provider_session_id,
            usage=result.usage,
            permission_snapshot=result.permission_snapshot,
            permission_drift=result.permission_drift,
        )
        if result.permission_snapshot:
            emit_provider_permission_snapshot(
                writer,
                task_id=task_id,
                causation_id=reply_event.id,
                correlation_id=user_message.correlation_id,
                actor="web",
                snapshot=result.permission_snapshot,
                drift=result.permission_drift,
            )
        writer.emit(
            "kanban.agent.turn.failed",
            actor="web",
            task_id=task_id,
            causation_id=reply_event.id,
            correlation_id=user_message.correlation_id,
            payload={
                "turn_id": turn_id,
                "thread_key": thread_key,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "backend": backend,
                "thread_id": result.thread_id,
                "provider_session_id": result.provider_session_id,
                "status": result.status,
                "reason": result.error or result.status,
                "delta_count": delta_emitter.delta_seq,
                "reply_event_id": reply_event.id,
            },
        )
        writer.emit(
            "runtime.action.failed",
            actor="web",
            task_id=task_id,
            causation_id=reply_event.id,
            correlation_id=user_message.correlation_id,
            payload={
                "action": action,
                "requested_action": requested_action,
                "status": result.status,
                "backend": backend,
                "reason": result.error or result.status,
                "message_event_id": user_message.id,
                "reply_event_id": reply_event.id,
            },
        )
        writer.emit(
            "web.action.failed",
            actor="web",
            task_id=task_id,
            causation_id=reply_event.id,
            correlation_id=user_message.correlation_id,
            payload={
                "action": action,
                "requested_action": requested_action,
                "status": result.status,
                "backend": backend,
                "reason": result.error or result.status,
                "reply_event_id": reply_event.id,
            },
        )
        return {
            "_status_code": 503,
            "ok": False,
            "status": result.status,
            "action": action,
            "requested_action": requested_action,
            "reason": result.error or result.status,
            "event_id": user_message.id,
            "reply_event_id": reply_event.id,
            "turn_id": turn_id,
            "thread_key": thread_key,
            "reply": redact_obj(reply),
        }
    agent_stream.complete(
        status=result.status,
        reason="headless kanban agent reply completed",
        provider_session_id=result.provider_session_id,
        usage=result.usage,
        permission_snapshot=result.permission_snapshot,
        permission_drift=result.permission_drift,
    )
    if result.permission_snapshot:
        emit_provider_permission_snapshot(
            writer,
            task_id=task_id,
            causation_id=reply_event.id,
            correlation_id=user_message.correlation_id,
            actor="web",
            snapshot=result.permission_snapshot,
            drift=result.permission_drift,
        )
    writer.emit(
        "kanban.agent.turn.completed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id,
        correlation_id=user_message.correlation_id,
        payload={
            "turn_id": turn_id,
            "thread_key": thread_key,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "backend": backend,
            "thread_id": result.thread_id,
            "provider_session_id": result.provider_session_id,
            "status": result.status,
            "delta_count": delta_emitter.delta_seq,
            "reply_event_id": reply_event.id,
            "has_action_proposal": action_proposal is not None,
        },
    )
    writer.emit(
        "kanban.agent.message.completed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id,
        correlation_id=user_message.correlation_id,
        payload={
            "turn_id": turn_id,
            "thread_key": thread_key,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "backend": backend,
            "thread_id": result.thread_id,
            "provider_session_id": result.provider_session_id,
            "status": result.status,
            "reply_event_id": reply_event.id,
            "has_action_proposal": action_proposal is not None,
        },
    )
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id,
        correlation_id=user_message.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "completed",
            "backend": backend,
            "message_event_id": user_message.id,
            "reply_event_id": reply_event.id,
        },
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id,
        correlation_id=user_message.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "completed",
            "backend": backend,
            "reply_event_id": reply_event.id,
        },
    )
    return {
        "_status_code": 200,
        "ok": True,
        "status": "completed",
        "action": action,
        "requested_action": requested_action,
        "reason": "headless kanban agent reply completed",
        "event_id": user_message.id,
        "reply_event_id": reply_event.id,
        "turn_id": turn_id,
        "thread_key": thread_key,
        "trace_id": user_message.correlation_id,
        "reply": redact_obj(reply),
    }


def _headless_message_event_payload(message: HeadlessMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message_type": message.type,
        "content": message.content,
        "session_id": message.session_id,
        "tool": message.tool,
    }
    if message.input is not None:
        payload["input"] = redact_obj(message.input)
    if message.output:
        payload["output"] = message.output
    return payload


def _headless_action_proposal(
    answer: str,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for candidate in _headless_json_candidates(answer):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        proposal = _normalize_headless_action_proposal(
            decoded,
            user_message=user_message,
            proposal_context=proposal_context or {},
        )
        if proposal is not None:
            return proposal
    return None


def _headless_json_candidates(text: str) -> list[str]:
    stripped = str(text or "").strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    for marker in ("```json", "```JSON", "```"):
        start = stripped.find(marker)
        while start >= 0:
            body_start = start + len(marker)
            end = stripped.find("```", body_start)
            if end < 0:
                break
            body = stripped[body_start:end].strip()
            if body:
                candidates.append(body)
            start = stripped.find(marker, end + 3)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start:end + 1])
    return candidates


def _normalize_headless_action_proposal(
    decoded: Any,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(decoded, dict):
        return None
    proposal = decoded.get("action_proposal") or decoded.get("proposal") or decoded
    if not isinstance(proposal, dict):
        return None
    requested_action = str(
        proposal.get("action")
        or proposal.get("requested_action")
        or proposal.get("name")
        or ""
    ).strip()
    if not requested_action:
        return None
    action = _canonical_action(requested_action)
    if action not in KANBAN_AGENT_ALLOWED_ACTIONS:
        return None
    if action in {"chat-orchestrator", "start-operator-session"}:
        return None
    if action == "create-task" and not _message_allows_create_task_proposal(user_message):
        return None
    if action == "idea-to-product" and not _message_allows_idea_to_product_proposal(user_message):
        return None
    payload = proposal.get("payload") or proposal.get("params") or {}
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    for key, value in (proposal_context or {}).items():
        if value and not payload.get(key):
            payload[key] = value
    validation_error = _validate_action_payload(action, payload)
    return {
        "action": action,
        "requested_action": requested_action,
        "payload": redact_obj(payload),
        "reason": str(proposal.get("reason") or proposal.get("summary") or ""),
        "confidence": str(proposal.get("confidence") or ""),
        "valid": not validation_error,
        "validation_error": validation_error,
        "mutates_task_state": action in {
            "create-task",
            "update-task",
            "archive-task",
            "link-evidence",
        },
    }






def _handle_start_collaboration(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
) -> dict:
    task_id = _task_id_from_payload(payload)
    intent = str(payload.get("intent") or payload.get("message") or "").strip()
    event = writer.emit(
        "user.intent.submitted",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        payload={
            "source": "kanban",
            "target": "orchestrator",
            "intent": intent,
            "title": str(payload.get("title") or ""),
            "runtime_delivery": "queued_no_runtime",
            "request": redact_obj(payload),
        },
    )
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "queued_no_runtime",
            "intent_event_id": event.id,
        },
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "queued_no_runtime",
        },
    )
    return {
        "_status_code": 202,
        "ok": True,
        "status": "queued_no_runtime",
        "action": action,
        "requested_action": requested_action,
        "reason": "collaboration intent recorded; orchestrator runtime owns execution",
        "event_id": event.id,
        "trace_id": event.correlation_id,
    }




def _handle_kanban_agent_lifecycle_probe(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    user_message: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    message: str,
) -> dict:
    title = _lifecycle_probe_title(payload, message)
    create_payload = {
        "title": title,
        "source": "kanban-agent",
        "contract": {
            "behavior": "Probe Kanban Agent task creation and lifecycle status management.",
            "verification": "Created and moved through backlog -> in_progress -> done by controlled Web actions.",
            "acceptance": "snapshot shows final done/archive projection",
        },
    }
    create_response = _run_kanban_agent_child_action(
        state_dir,
        writer,
        user_message=user_message,
        action="create-task",
        payload=create_payload,
        task_id=None,
    )
    if not create_response.get("ok"):
        return _lifecycle_probe_failed_response(
            writer,
            requested=requested,
            user_message=user_message,
            action=action,
            requested_action=requested_action,
            reason=str(create_response.get("reason") or "create-task failed"),
            step="create-task",
        )

    task_id = str(create_response.get("task_id") or "")
    steps: list[dict[str, str]] = [
        {
            "action": "create-task",
            "task_id": task_id,
            "status": "backlog",
            "result": str(create_response.get("status") or "completed"),
        },
    ]
    for status in ("in_progress", "done"):
        update_payload = {
            "task_id": task_id,
            "status": status,
            "source": "kanban-agent",
        }
        update_response = _run_kanban_agent_child_action(
            state_dir,
            writer,
            user_message=user_message,
            action="update-task",
            payload=update_payload,
            task_id=task_id,
        )
        if not update_response.get("ok"):
            return _lifecycle_probe_failed_response(
                writer,
                requested=requested,
                user_message=user_message,
                action=action,
                requested_action=requested_action,
                reason=str(update_response.get("reason") or f"update-task {status} failed"),
                step=f"update-task:{status}",
                task_id=task_id,
            )
        steps.append({
            "action": "update-task",
            "task_id": task_id,
            "status": status,
            "result": str(update_response.get("status") or "completed"),
        })

    answer = (
        f"Created {task_id} as backlog for {title}. "
        f"Then moved {task_id} to in_progress and finally to done. "
        "Board truth was updated through controlled create-task/update-task actions; "
        "the final task is available from the Done/archive projection."
    )
    reply = {
        "source": "deterministic_lifecycle_probe",
        "scope": "project",
        "task_id": task_id,
        "title": title,
        "answer": answer,
        "mutates_task_state": True,
        "runtime_followup": "completed",
        "status_sequence": ["backlog", "in_progress", "done"],
        "actions": steps,
        "evidence_refs": [{"kind": "task", "id": task_id}],
    }
    reply_event = writer.emit(
        "kanban.agent.reply",
        actor="web",
        task_id=task_id,
        causation_id=user_message.id,
        correlation_id=user_message.correlation_id,
        payload=reply,
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=reply_event,
        action=action,
        requested_action=requested_action,
        status="completed",
        task_id=task_id,
        extra={
            "message_event_id": user_message.id,
            "reply_event_id": reply_event.id,
            "task_id": task_id,
            "status_sequence": ["backlog", "in_progress", "done"],
        },
    )
    return {
        "_status_code": 200,
        "ok": True,
        "status": "completed",
        "action": action,
        "requested_action": requested_action,
        "reason": "Kanban Agent lifecycle probe completed through controlled task actions",
        "event_id": user_message.id,
        "reply_event_id": reply_event.id,
        "task_id": task_id,
        "reply": redact_obj(reply),
    }


def _run_kanban_agent_child_action(
    state_dir: Path,
    writer: EventWriter,
    *,
    user_message: ZfEvent,
    action: str,
    payload: dict,
    task_id: str | None,
) -> dict:
    requested = writer.emit(
        "web.action.requested",
        actor="kanban-agent",
        task_id=task_id,
        causation_id=user_message.id,
        correlation_id=user_message.correlation_id,
        payload={
            "action": action,
            "requested_action": action,
            "request": redact_obj(payload),
            "source": "kanban-agent.lifecycle_probe",
        },
    )
    writer.emit(
        "runtime.action.accepted",
        actor="kanban-agent",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "action": action,
            "requested_action": action,
            "source": "kanban-agent.lifecycle_probe",
        },
    )
    if action == "create-task":
        return _handle_create_task(
            state_dir,
            writer,
            requested=requested,
            action=action,
            requested_action=action,
            payload=payload,
        )
    if action == "update-task":
        return _handle_update_task(
            state_dir,
            writer,
            requested=requested,
            action=action,
            requested_action=action,
            payload=payload,
        )
    return {
        "ok": False,
        "status": "unsupported_action",
        "reason": f"unsupported lifecycle probe action: {action}",
    }


def _lifecycle_probe_failed_response(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    user_message: ZfEvent,
    action: str,
    requested_action: str,
    reason: str,
    step: str,
    task_id: str | None = None,
) -> dict:
    reply = {
        "source": "deterministic_lifecycle_probe",
        "scope": "project",
        "task_id": task_id or "",
        "answer": f"Kanban Agent lifecycle probe stopped at {step}: {reason}",
        "mutates_task_state": True,
        "runtime_followup": "failed",
        "failed_step": step,
    }
    reply_event = writer.emit(
        "kanban.agent.reply",
        actor="web",
        task_id=task_id,
        causation_id=user_message.id,
        correlation_id=user_message.correlation_id,
        payload=reply,
    )
    writer.emit(
        "runtime.action.failed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id,
        correlation_id=reply_event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "reason": reason,
            "failed_step": step,
        },
    )
    writer.emit(
        "web.action.failed",
        actor="web",
        task_id=task_id,
        causation_id=reply_event.id,
        correlation_id=reply_event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "reason": reason,
            "failed_step": step,
        },
    )
    return {
        "_status_code": 409,
        "ok": False,
        "status": "failed",
        "action": action,
        "requested_action": requested_action,
        "reason": reason,
        "event_id": requested.id,
        "reply_event_id": reply_event.id,
        "reply": redact_obj(reply),
    }


def _lifecycle_probe_title(payload: dict, message: str) -> str:
    explicit = str(payload.get("probe_title") or payload.get("title") or "").strip()
    if explicit:
        return explicit
    for marker in ("测试任务:", "测试任务：", "task:", "Task:", "任务:", "任务："):
        if marker not in message:
            continue
        tail = message.split(marker, 1)[1].strip()
        for separator in ("\n", "要求", "然后", "先放", "。"):
            if separator in tail:
                tail = tail.split(separator, 1)[0].strip()
        tail = tail.strip(" .。,:，")
        if tail:
            return tail
    suffix = hashlib.sha1(message.encode("utf-8")).hexdigest()[:8]
    return f"Kanban Agent lifecycle probe {suffix}"


def _handle_request_fanout(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    config: ZfConfig | None = None,
) -> dict:
    stage_id = str(payload.get("stage_id") or "")
    stage = _workflow_stage(config, stage_id)
    topology = str(getattr(stage, "topology", "") or "")
    target_ref = str(payload.get("target_ref") or getattr(stage, "target_ref", "") or "")
    fanout_id = str(payload.get("fanout_id") or "") or _requested_fanout_id(stage_id, payload)
    task_id = _task_id_from_payload(payload)
    event = writer.emit(
        "fanout.requested",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "fanout_id": fanout_id,
            "stage_id": stage_id,
            "topology": topology,
            "target_ref": target_ref,
            "pdd_id": str(payload.get("pdd_id") or ""),
            "trace_id": str(payload.get("trace_id") or requested.correlation_id or ""),
            "requested_by": str(payload.get("requested_by") or "kanban"),
            "reason": str(payload.get("reason") or ""),
            "runtime_delivery": "queued_no_runtime",
            "request": redact_obj(payload),
        },
    )
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "requested",
            "fanout_id": fanout_id,
            "stage_id": stage_id,
        },
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload={
            "action": action,
            "requested_action": requested_action,
            "status": "requested",
            "fanout_id": fanout_id,
        },
    )
    return {
        "_status_code": 202,
        "ok": True,
        "status": "requested",
        "action": action,
        "requested_action": requested_action,
        "reason": "fanout request recorded; orchestrator runtime owns child dispatch",
        "fanout_id": fanout_id,
        "event_id": event.id,
    }


def _handle_start_operator_session(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict:
    backend = str(payload.get("backend") or "").strip() or str(
        os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
        or getattr(getattr(config, "orchestrator", None), "backend", "")
        or "deterministic"
    )
    backend = _canonical_operator_backend(backend) or _default_operator_backend(
        _canonical_operator_backend(
            os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
            or getattr(getattr(config, "orchestrator", None), "backend", "")
        )
    )
    task_id = _task_id_from_payload(payload) or ""
    manager = _operator_session_manager(
        state_dir,
        project_root=_resolve_project_root_for_state(state_dir, project_root),
    )
    start = manager.start(
        backend=backend,
        scope="project",
        task_id=task_id,
        force=_truthy(payload.get("force")),
        cols=_positive_int(payload.get("cols"), default=120, minimum=20, maximum=1000),
        rows=_positive_int(payload.get("rows"), default=30, minimum=5, maximum=1000),
        skills_available=_operator_skills_available(
            state_dir,
            config=config,
            project_root=project_root,
        ),
    )
    session = redact_obj(start.session)
    session_path = state_dir / "operator" / "kanban-agent.json"
    atomic_write_text(
        session_path,
        json.dumps(session, ensure_ascii=False, indent=2) + "\n",
    )
    if not start.ok:
        event = writer.emit(
            "operator.session.failed",
            actor="web",
            task_id=task_id or None,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                **session,
                "reason": start.reason,
                "request": redact_obj(payload),
            },
        )
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id or None,
            reason=start.reason,
            status_code=503,
            status="runtime_failed",
        ) | {"event_id": event.id, "result": session}

    event = writer.emit(
        "operator.session.started",
        actor="web",
        task_id=task_id or None,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            **session,
            "start_status": start.status,
            "request": redact_obj(payload),
        },
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=event,
        action=action,
        requested_action=requested_action,
        status="runtime_accepted",
        task_id=task_id or None,
        extra={
            "session_id": session.get("session_id", "kanban-agent:project"),
            "backend": backend,
            "scope": "project",
            "context_task_id": task_id,
            "terminal_status": start.status,
            "output_seq": session.get("output_seq", 0),
        },
    )
    return {
        "_status_code": 202,
        "ok": True,
        "status": "runtime_accepted",
        "action": action,
        "requested_action": requested_action,
        "reason": start.reason,
        "event_id": event.id,
        "result": session,
    }


def _handle_create_task(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
) -> dict:
    store = TaskStore(state_dir / "kanban.json")
    task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
    if task_id and store.get(task_id) is not None:
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=f"task {task_id!r} already exists",
            status_code=409,
            status="conflict",
        )
    task = Task(
        id=task_id or Task().id,
        title=str(payload.get("title") or "").strip(),
        key=str(payload.get("key") or payload.get("feature_id") or ""),
        priority=_task_priority(payload.get("priority")),
        assigned_to=_optional_str(payload.get("assigned_to") or payload.get("owner")),
        skills_required=_string_list(payload.get("skills_required") or payload.get("skills")),
        blocked_by=_string_list(payload.get("blocked_by")),
        contract=_task_contract_from_payload(payload.get("contract")),
    )
    try:
        created = store.add(task)
    except Exception as exc:
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task.id,
            reason=str(exc),
            status_code=409,
        )
    event = writer.emit(
        "task.created",
        actor="web",
        task_id=created.id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "source": "kanban-agent",
            "task": redact_obj(asdict(created)),
            "request": redact_obj(payload),
        },
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=event,
        action=action,
        requested_action=requested_action,
        status="completed",
        task_id=created.id,
        extra={"task_id": created.id},
    )
    task_payload = redact_obj(asdict(created))
    return {
        "_status_code": 201,
        "ok": True,
        "status": "completed",
        "action": action,
        "requested_action": requested_action,
        "reason": f"task {created.id} created through controlled Kanban action",
        "event_id": event.id,
        "task_id": created.id,
        "result": {"task": task_payload},
    }


def _handle_update_task(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
) -> dict:
    store = TaskStore(state_dir / "kanban.json")
    task_id = str(payload.get("task_id") or "").strip()
    task = store.get(task_id)
    if task is None:
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=f"task {task_id!r} not found",
            status_code=404,
            status="not_found",
        )
    updates = _task_updates_from_payload(task, payload)
    if not updates:
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason="no supported task fields to update",
            status_code=422,
            status="invalid_payload",
        )
    updated = store.update(task_id, **updates)
    if updated is None:
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=f"task {task_id!r} not found",
            status_code=404,
            status="not_found",
        )
    event = writer.emit(
        "task.updated",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "source": "kanban-agent",
            "updates": redact_obj(updates),
            "unsupported_metadata": redact_obj(_task_metadata_payload(payload)),
            "task": redact_obj(asdict(updated)),
        },
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=event,
        action=action,
        requested_action=requested_action,
        status="completed",
        task_id=task_id,
        extra={"task_id": task_id},
    )
    return {
        "ok": True,
        "status": "completed",
        "action": action,
        "requested_action": requested_action,
        "reason": f"task {task_id} updated through controlled Kanban action",
        "event_id": event.id,
        "task_id": task_id,
        "result": {"task": redact_obj(asdict(updated))},
    }


def _handle_decompose_feature(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
) -> dict:
    store = TaskStore(state_dir / "kanban.json")
    feature_id = str(payload.get("feature_id") or payload.get("pdd_id") or "").strip()
    raw_items = payload.get("tasks") or payload.get("titles") or []
    created_tasks = []
    for index, item in enumerate(raw_items, start=1):
        item_payload = item if isinstance(item, dict) else {"title": str(item)}
        title = str(item_payload.get("title") or "").strip()
        if not title:
            continue
        task = Task(
            title=title,
            key=str(item_payload.get("key") or (f"{feature_id}:{index}" if feature_id else "")),
            priority=_task_priority(item_payload.get("priority")),
            assigned_to=_optional_str(item_payload.get("assigned_to") or item_payload.get("owner")),
            skills_required=_string_list(item_payload.get("skills_required") or item_payload.get("skills")),
            blocked_by=_string_list(item_payload.get("blocked_by")),
            contract=_task_contract_from_payload(item_payload.get("contract")),
        )
        created = store.add(task)
        created_tasks.append(created)
        writer.emit(
            "task.created",
            actor="web",
            task_id=created.id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "source": "kanban-agent",
                "feature_id": feature_id,
                "task": redact_obj(asdict(created)),
                "request": redact_obj(item_payload),
            },
        )
    event = writer.emit(
        "feature.decomposed",
        actor="web",
        task_id=None,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "source": "kanban-agent",
            "feature_id": feature_id,
            "task_ids": [task.id for task in created_tasks],
            "request": redact_obj(payload),
        },
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=event,
        action=action,
        requested_action=requested_action,
        status="completed",
        task_id=None,
        extra={"task_ids": [task.id for task in created_tasks]},
    )
    return {
        "_status_code": 201,
        "ok": True,
        "status": "completed",
        "action": action,
        "requested_action": requested_action,
        "reason": f"{len(created_tasks)} tasks created from feature decomposition",
        "event_id": event.id,
        "result": {"tasks": [redact_obj(asdict(task)) for task in created_tasks]},
    }


def _handle_link_evidence(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
) -> dict:
    task_id = str(payload.get("task_id") or "").strip()
    store = TaskStore(state_dir / "kanban.json")
    task = store.get(task_id)
    if task is None:
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=f"task {task_id!r} not found",
            status_code=404,
            status="not_found",
        )
    evidence = _task_evidence_from_payload(task, payload.get("evidence") or payload)
    updated = store.update(task_id, evidence=evidence) if evidence is not None else task
    event = writer.emit(
        "task.evidence_linked",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "source": "kanban-agent",
            "links": redact_obj({
                key: payload.get(key)
                for key in ("pdd_id", "tdd_id", "trace_id", "fanout_id", "run_id", "workdir", "task_ref")
                if payload.get(key)
            }),
            "evidence": redact_obj(asdict(evidence)) if evidence is not None else {},
        },
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=event,
        action=action,
        requested_action=requested_action,
        status="completed",
        task_id=task_id,
        extra={"task_id": task_id},
    )
    return {
        "ok": True,
        "status": "completed",
        "action": action,
        "requested_action": requested_action,
        "reason": f"evidence linked to task {task_id}",
        "event_id": event.id,
        "task_id": task_id,
        "result": {"task": redact_obj(asdict(updated)) if updated else {}},
    }


def _handle_archive_task(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
) -> dict:
    task_id = str(payload.get("task_id") or "").strip()
    terminal_status = str(payload.get("status") or "cancelled").strip()
    if terminal_status not in {"done", "cancelled"}:
        terminal_status = "cancelled"
    store = TaskStore(state_dir / "kanban.json")
    task = store.get(task_id)
    if task is None:
        return _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=f"task {task_id!r} not found",
            status_code=404,
            status="not_found",
        )
    archived = store.update(task_id, status=terminal_status)
    event = writer.emit(
        "task.archived",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "source": "kanban-agent",
            "status": terminal_status,
            "reason": str(payload.get("reason") or ""),
        },
    )
    _emit_action_completed(
        writer,
        requested=requested,
        event=event,
        action=action,
        requested_action=requested_action,
        status="completed",
        task_id=task_id,
        extra={"task_id": task_id, "terminal_status": terminal_status},
    )
    return {
        "ok": True,
        "status": "completed",
        "action": action,
        "requested_action": requested_action,
        "reason": f"task {task_id} archived as {terminal_status}",
        "event_id": event.id,
        "task_id": task_id,
        "result": {"task": redact_obj(asdict(archived)) if archived else {}},
    }








































































































def _web_action_token_configured() -> bool:
    return bool(os.environ.get("ZF_WEB_ACTION_TOKEN", ""))




def _web_session_ttl_seconds() -> int:
    raw = os.environ.get("ZF_WEB_SESSION_TTL_SECONDS", "").strip()
    try:
        value = int(raw) if raw else 12 * 60 * 60
    except ValueError:
        value = 12 * 60 * 60
    return max(60, min(value, 7 * 24 * 60 * 60))


def _web_session_cookie(request: Request) -> str | None:
    return request.cookies.get(_WEB_SESSION_COOKIE)


def _operator_websocket_authorized(websocket: WebSocket) -> bool:
    configured_token = os.environ.get("ZF_WEB_ACTION_TOKEN", "")
    supplied = (
        websocket.headers.get("x-zf-web-token")
        or websocket.query_params.get("token")
        or _bearer_token(websocket.headers.get("authorization"))
    )
    return (
        _web_trusted_session_enabled()
        or _web_session_token_valid(websocket.cookies.get(_WEB_SESSION_COOKIE))
        or bool(configured_token and supplied == configured_token)
    )






def _web_unlock_retry_after(client_id: str) -> int | None:
    limit, window = _web_unlock_rate_limit()
    now = time.time()
    attempts = [
        ts for ts in _WEB_UNLOCK_FAILURES.get(client_id, [])
        if now - ts < window
    ]
    _WEB_UNLOCK_FAILURES[client_id] = attempts
    if len(attempts) < limit:
        return None
    oldest = min(attempts)
    return max(1, int(window - (now - oldest)))


def _record_web_unlock_failure(client_id: str) -> None:
    _WEB_UNLOCK_FAILURES.setdefault(client_id, []).append(time.time())


def _unlock_web_session(passcode: str, *, client_id: str = "unknown") -> dict:
    configured = os.environ.get("ZF_WEB_PASSCODE", "")
    if not configured:
        return {
            "_status_code": 403,
            "ok": False,
            "status": "disabled",
            "reason": "remote passcode unlock disabled; set ZF_WEB_PASSCODE to enable it",
        }
    retry_after = _web_unlock_retry_after(client_id)
    if retry_after is not None:
        return {
            "_status_code": 429,
            "ok": False,
            "status": "rate_limited",
            "reason": f"too many failed passcode attempts; retry after {retry_after}s",
            "retry_after_seconds": retry_after,
        }
    if not secrets.compare_digest(passcode, configured):
        _record_web_unlock_failure(client_id)
        return {
            "_status_code": 403,
            "ok": False,
            "status": "unauthorized",
            "reason": "missing or invalid web passcode",
        }
    token = secrets.token_urlsafe(32)
    expires = time.time() + _web_session_ttl_seconds()
    _WEB_SESSIONS[token] = expires
    _WEB_UNLOCK_FAILURES.pop(client_id, None)
    return {
        "ok": True,
        "status": "unlocked",
        "_session_token": token,
        "session": _web_session_projection(token),
    }


def _web_session_token_valid(web_session_token: str | None) -> bool:
    return _web_session_expires_at(web_session_token) is not None


def _web_session_expires_at(web_session_token: str | None) -> str | None:
    if not web_session_token:
        return None
    now = time.time()
    for token, expires in list(_WEB_SESSIONS.items()):
        if expires <= now:
            _WEB_SESSIONS.pop(token, None)
    expires = _WEB_SESSIONS.get(web_session_token)
    if expires is None or expires <= now:
        _WEB_SESSIONS.pop(web_session_token, None)
        return None
    return datetime.fromtimestamp(expires, timezone.utc).isoformat()


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0"} - {"0.0.0.0"})


def _is_loopback_host(host: str) -> bool:
    """Treat IPv4/IPv6 loopback and explicit `localhost` as loopback."""
    return host.strip().lower() in _LOOPBACK_HOSTS






def validate_trusted_session_host(host: str) -> None:
    """Refuse to enable trusted-session mutations on a non-loopback host.

    `ZF_WEB_TRUSTED_SESSION` bypasses token/passcode for every mutation.
    On a non-loopback bind that turns the dashboard into an unauthenticated
    mutation surface for any host on the network. Require a separate
    explicit override (`ZF_WEB_TRUSTED_SESSION_ALLOW_NONLOOPBACK=1`) for
    the rare deployments that intentionally combine the two.
    """
    if not _web_trusted_session_enabled():
        return
    if _is_loopback_host(host):
        return
    if _web_trusted_session_nonloopback_override():
        print(
            f"warning: ZF_WEB_TRUSTED_SESSION is enabled on non-loopback host {host}; "
            "any client on this network can mutate runtime state.",
            file=sys.stderr,
        )
        return
    raise RuntimeError(
        f"ZF_WEB_TRUSTED_SESSION is enabled but --host is {host!r}, "
        "which is not a loopback address. Either bind to 127.0.0.1, unset "
        "ZF_WEB_TRUSTED_SESSION, or set "
        "ZF_WEB_TRUSTED_SESSION_ALLOW_NONLOOPBACK=1 to explicitly allow this."
    )


def _web_mutation_mode() -> str:
    if _web_action_token_configured():
        return "token"
    if _web_passcode_configured():
        return "passcode"
    if _web_trusted_session_enabled():
        return "trusted_local"
    return "disabled"


def _web_action_authorization_available() -> bool:
    return (
        _web_action_token_configured()
        or _web_trusted_session_enabled()
        or _web_passcode_configured()
    )


def _web_mutation_auth_error(
    action_name: str,
    *,
    authorization: str | None,
    x_zf_web_token: str | None,
    web_session_token: str | None = None,
) -> dict | None:
    configured_token = os.environ.get("ZF_WEB_ACTION_TOKEN", "")
    trusted_session = _web_trusted_session_enabled()
    passcode_session = _web_session_token_valid(web_session_token)
    if not configured_token and not trusted_session and not _web_passcode_configured():
        return {
            "_status_code": 403,
            "ok": False,
            "status": "disabled",
            "action": action_name,
            "reason": "mutation disabled; set ZF_WEB_ACTION_TOKEN, ZF_WEB_PASSCODE, or ZF_WEB_TRUSTED_SESSION=1 to enable controlled actions",
        }
    supplied = x_zf_web_token or _bearer_token(authorization)
    token_ok = bool(configured_token and supplied == configured_token)
    if not trusted_session and not passcode_session and not token_ok:
        return {
            "_status_code": 403,
            "ok": False,
            "status": "unauthorized",
            "action": action_name,
            "reason": "missing or invalid web action token/session",
        }
    return None


async def _operator_io_socket(
    websocket: WebSocket,
    *,
    state_dir: Path,
    project_root: Path,
) -> None:
    if not _operator_websocket_authorized(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager = _operator_session_manager(state_dir, project_root=project_root)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)

    def enqueue(data: bytes) -> None:
        def put() -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

        loop.call_soon_threadsafe(put)

    detach = manager.attach_raw_output(enqueue)

    async def send_output() -> None:
        for chunk in manager.raw_output_since(0, 400):
            await websocket.send_bytes(chunk)
        while True:
            chunk = await queue.get()
            await websocket.send_bytes(chunk)

    async def receive_input() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            data = message.get("bytes")
            if data is None:
                text = message.get("text")
                if text is None:
                    continue
                data = text.encode("utf-8", errors="replace")
            manager.write_raw(data)

    sender = asyncio.create_task(send_output())
    receiver = asyncio.create_task(receive_input())
    try:
        done, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        detach()
        sender.cancel()
        receiver.cancel()


async def _operator_control_socket(
    websocket: WebSocket,
    *,
    state_dir: Path,
    project_root: Path,
) -> None:
    if not _operator_websocket_authorized(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager = _operator_session_manager(state_dir, project_root=project_root)

    async def send_state() -> None:
        session = _operator_session_status(state_dir, project_root=project_root)
        await websocket.send_json({"type": "state", "session": session})

    try:
        session = _operator_session_status(state_dir, project_root=project_root)
        await websocket.send_json({
            "type": "restore",
            "snapshot": "",
            "cols": session.get("cols") or 120,
            "rows": session.get("rows") or 30,
        })
        await send_state()
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "invalid terminal control payload",
                })
                continue
            message_type = str(payload.get("type") or "")
            if message_type == "resize":
                result = manager.resize(
                    cols=int(payload.get("cols") or 120),
                    rows=int(payload.get("rows") or 30),
                )
                if not result.get("ok"):
                    await websocket.send_json({
                        "type": "error",
                        "message": result.get("reason") or result.get("status"),
                    })
                await send_state()
                continue
            if message_type == "stop":
                manager.stop(reason="web terminal stop requested")
                await websocket.send_json({"type": "exit", "code": None})
                await send_state()
                continue
            if message_type == "restore_complete":
                continue
            await websocket.send_json({
                "type": "error",
                "message": f"unknown terminal control message: {message_type}",
            })
    except WebSocketDisconnect:
        return
























async def _tail_operator_output(
    state_dir: Path,
    *,
    project_root: Path,
    request: Request,
    cursor: int = 0,
) -> AsyncIterator[bytes]:
    """SSE generator for ephemeral Kanban operator PTY output."""
    manager = _operator_session_manager(state_dir, project_root=project_root)
    last_seq = max(cursor, 0)
    yield b": connected\n\n"
    while True:
        if await request.is_disconnected():
            return
        page = manager.output_since(cursor=last_seq, limit=200)
        chunks = page.get("chunks", []) if isinstance(page, dict) else []
        if chunks:
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                seq = int(chunk.get("seq") or 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                safe_chunk = redact_obj(chunk)
                yield (
                    f"id: {seq}\n"
                    f"data: {json.dumps(safe_chunk, ensure_ascii=False)}\n\n"
                ).encode("utf-8")
        else:
            yield b": ping\n\n"
        await asyncio.sleep(0.25)












__all__ = ["create_app"]
