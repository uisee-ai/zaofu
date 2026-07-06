"""Projections layer: agents (moved verbatim from web/server.py)."""
from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workdirs import WorkdirManager
from zf.web.projections.common import _age_seconds, _attention_from_provider_stop_reason, _attention_state_needs_operator, _briefing_paths_for_instance, _clear_context_attention, _cost_by_instance, _display_path, _empty_cost_summary, _empty_queue_role_summary, _git, _instance_origin, _parent_role_from_instance, _payload_context_ratio, _payload_first_string, _read_json_file, _resolve_project_root_for_state
from zf.web.projections.events import _active_task_by_instance, _event_signal_summary, _events_with_seq, _last_event_by_actor
from zf.web.projections.runs import _runtime_instance_retired


_NEXT_EVENT_BY_TYPE = {
    "task.assigned": "task.dispatched",
    "task.dispatched": "worker.progress",
    "arch.proposal.done": "design.critique.done",
    "design.critique.done": "task.assigned",
    "dev.build.done": "static_gate.passed",
    "static_gate.passed": "review.approved",
    "static_gate.skipped": "review.approved",
    "review.approved": "test.passed",
    "test.passed": "judge.passed",
    "judge.passed": "task.done",
}


_ORCHESTRATOR_ATTENTION_EVENTS = frozenset({
    "clarification.needed",
    "dev.blocked",
    "review.rejected",
    "test.failed",
    "judge.failed",
    "task.contract.invalid",
    "dispatch.silent_stall",
    "worker.stuck",
    "static_gate.failed",
})


def _workers(state_dir: Path, config: ZfConfig | None = None) -> list[dict]:
    """Read role_sessions.yaml + last worker.state.changed event."""
    meta = _role_session_meta(state_dir)
    # Last worker.state.changed per actor for current state
    state_by_actor: dict[str, str] = {}
    events_path = state_dir / "events.jsonl"
    if events_path.exists():
        try:
            from zf.web.projections.events import events_read_days

            for e in events_read_days(state_dir, 1, config=config):
                if e.type == "worker.state.changed" and isinstance(
                    e.payload, dict,
                ):
                    to = e.payload.get("to")
                    if to and e.actor:
                        state_by_actor[e.actor] = to
        except Exception:
            pass
    out = []
    for instance_id, m in sorted(meta.items()):
        out.append({
            "instance_id": instance_id,
            "backend": m.get("backend", ""),
            "spawned_at": m.get("spawned_at", ""),
            "state": state_by_actor.get(instance_id, "unknown"),
        })
    return out


def _agent_view_queue_projection(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    workers: list[dict],
) -> dict:
    """Read-only queue / waiting projection for Agent View.

    This does not write task truth. It explains why a task is waiting and
    which role/operator should own the next move.
    """
    path = state_dir / "kanban.json"
    role_capacity = _agent_view_role_capacity(workers)
    if not path.exists():
        return {
            "schema_version": "agent-queue.v1",
            "tasks": [],
            "by_role": role_capacity,
            "needs_attention": [],
            "summary": {
                "waiting_tasks": 0,
                "needs_attention": 0,
            },
        }
    try:
        tasks = TaskStore(path).list_all()
    except Exception:
        tasks = []
    events_by_task: dict[str, list[tuple[int, ZfEvent]]] = {}
    for seq, event in _events_with_seq(state_dir, config=config):
        task_id = str(getattr(event, "task_id", "") or "")
        if not task_id:
            payload = getattr(event, "payload", {}) or {}
            if isinstance(payload, dict):
                task_id = str(payload.get("task_id") or "")
        if not task_id:
            continue
        events_by_task.setdefault(task_id, []).append((seq, event))

    rows: list[dict] = []
    attention_rows: list[dict] = []
    now = datetime.now(timezone.utc)
    for task in tasks:
        if task.status in {"done", "cancelled"}:
            continue
        task_events = events_by_task.get(task.id, [])
        latest_seq, latest_event = task_events[-1] if task_events else (0, None)
        latest_type = str(getattr(latest_event, "type", "") or "")
        waiting_role = _agent_view_waiting_role(task, latest_type)
        next_expected = _agent_view_next_expected_event(task, latest_type)
        needs_attention = latest_type in _ORCHESTRATOR_ATTENTION_EVENTS or (
            task.status == "blocked"
        )
        if needs_attention:
            waiting_role = "orchestrator"
        started_at = (
            getattr(latest_event, "ts", "")
            if latest_event is not None
            else task.started_at
            or task.dispatched_at
            or task.created_at
        )
        age = _age_seconds(started_at, now=now)
        blocking_event = latest_type if needs_attention else ""
        row = {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "assigned_to": task.assigned_to or "",
            "waiting_role": waiting_role,
            "queue_age_seconds": age,
            "blocked_reason": task.blocked_reason,
            "blocking_event": blocking_event,
            "next_expected_event": next_expected,
            "last_event_type": latest_type,
            "last_event_seq": latest_seq,
            "needs_attention": needs_attention,
            "ready": task.status in {"backlog", "ready"} and not task.blocked_by,
        }
        rows.append(row)
        summary = role_capacity.setdefault(
            waiting_role,
            _empty_queue_role_summary(waiting_role),
        )
        summary["waiting_task_count"] += 1
        if row["ready"]:
            summary["ready_task_count"] += 1
        if needs_attention:
            summary["needs_attention_count"] += 1
            attention_rows.append(row)
        current_oldest = summary.get("oldest_ready_age_seconds")
        if row["ready"] and (current_oldest is None or age > current_oldest):
            summary["oldest_ready_age_seconds"] = age
        if not summary.get("next_expected_event") and next_expected:
            summary["next_expected_event"] = next_expected

    return {
        "schema_version": "agent-queue.v1",
        "tasks": rows,
        "by_role": role_capacity,
        "needs_attention": attention_rows,
        "summary": {
            "waiting_tasks": len(rows),
            "needs_attention": len(attention_rows),
        },
    }


def _agent_view_role_capacity(workers: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for worker in workers:
        role = str(worker.get("parent_role") or worker.get("role_type") or "")
        if not role:
            continue
        summary = out.setdefault(role, _empty_queue_role_summary(role))
        summary["effective_workers"] += 1
        lifecycle = str(worker.get("lifecycle_state") or "")
        attention = str(worker.get("attention_state") or "")
        if lifecycle in {"idle", "healthy"} and not _attention_state_needs_operator(attention):
            summary["available_capacity"] += 1
    return out


def _agent_view_waiting_role(task: Task, latest_type: str) -> str:
    if latest_type in _ORCHESTRATOR_ATTENTION_EVENTS:
        return "orchestrator"
    assigned = task.assigned_to or ""
    if assigned:
        return _parent_role_from_instance(assigned)
    owner = getattr(task.contract, "owner_role", "") or ""
    if owner:
        return owner
    return "dev"


def _agent_view_next_expected_event(task: Task, latest_type: str) -> str:
    if latest_type in _ORCHESTRATOR_ATTENTION_EVENTS:
        return "orchestrator.decision"
    if latest_type:
        return _NEXT_EVENT_BY_TYPE.get(latest_type, "")
    assigned = task.assigned_to or ""
    if assigned:
        return "task.dispatched"
    owner = getattr(task.contract, "owner_role", "") or ""
    if owner:
        return "task.assigned"
    return ""


def _roles(state_dir: Path, config: ZfConfig | None = None) -> list[dict]:
    meta = _role_session_meta(state_dir)
    sessions = _role_session_ids(state_dir)
    state_by_actor = _worker_states(state_dir, config=config)
    active_task = _active_task_by_instance(state_dir, config=config)
    heartbeat = _last_event_by_actor(state_dir, config=config)
    cost_by_instance = _cost_by_instance(state_dir)

    roles = list(config.roles) if config is not None else []
    if not roles:
        out = []
        for instance_id, m in sorted(meta.items()):
            if _runtime_instance_retired(m):
                continue
            cost = cost_by_instance.get(instance_id, _empty_cost_summary())
            parent_role = str(m.get("parent_role") or _parent_role_from_instance(instance_id))
            out.append({
                "instance_id": instance_id,
                "name": parent_role,
                "parent_role": parent_role,
                "origin": _instance_origin(instance_id, m, configured=False),
                "role_kind": str(m.get("role_kind") or "unknown"),
                "backend": m.get("backend", ""),
                "model": "",
                "transport": m.get("transport", ""),
                "skills": [],
                "state": state_by_actor.get(instance_id, "unknown"),
                "active_task": active_task.get(instance_id, ""),
                "session_id": sessions.get(instance_id, ""),
                "session_path": m.get("session_path") or "",
                "spawned_at": m.get("spawned_at", ""),
                "last_heartbeat": heartbeat.get(instance_id, ""),
                "cost": cost,
            })
        return out

    out = []
    configured_ids: set[str] = set()
    for role in roles:
        instance_id = role.instance_id or role.name
        configured_ids.add(instance_id)
        m = meta.get(instance_id, {})
        cost = cost_by_instance.get(instance_id, _empty_cost_summary())
        out.append({
            "instance_id": instance_id,
            "name": role.name,
            "parent_role": role.name,
            "origin": _instance_origin(instance_id, m, configured=True),
            "role_kind": role.role_kind,
            "backend": role.backend,
            "model": role.model,
            "transport": role.transport,
            "skills": list(role.skills),
            "plugins": list(role.plugins),
            "agent": role.agent,
            "state": state_by_actor.get(instance_id, "unknown"),
            "active_task": active_task.get(instance_id, ""),
            "session_id": sessions.get(instance_id, ""),
            "session_path": m.get("session_path") or "",
            "spawned_at": m.get("spawned_at", ""),
            "last_heartbeat": heartbeat.get(instance_id, ""),
            "cost": cost,
        })
    for instance_id, m in sorted(meta.items()):
        if instance_id in configured_ids:
            continue
        if _runtime_instance_retired(m):
            continue
        parent_role = str(m.get("parent_role") or _parent_role_from_instance(instance_id))
        cost = cost_by_instance.get(instance_id, _empty_cost_summary())
        out.append({
            "instance_id": instance_id,
            "name": parent_role,
            "parent_role": parent_role,
            "origin": _instance_origin(instance_id, m, configured=False),
            "role_kind": str(m.get("role_kind") or "unknown"),
            "backend": m.get("backend", ""),
            "model": str(m.get("model") or ""),
            "transport": str(m.get("transport") or ""),
            "skills": list(m.get("skills", []) or []),
            "plugins": list(m.get("plugins", []) or []),
            "agent": str(m.get("agent") or ""),
            "state": state_by_actor.get(instance_id, "unknown"),
            "active_task": active_task.get(instance_id, ""),
            "session_id": sessions.get(instance_id, ""),
            "session_path": m.get("session_path") or "",
            "spawned_at": m.get("spawned_at", ""),
            "last_heartbeat": heartbeat.get(instance_id, ""),
            "cost": cost,
        })
    return out


def _agent_classification(role_type: str, role_kind: str) -> tuple[str, str, str]:
    normalized = role_type.lower()
    kind = role_kind.lower()
    if normalized in {"orchestrator", "orch"} or kind == "control":
        return "control", "layer2_brain", "planning_dispatch_recovery"
    if normalized in {"arch", "architect", "critic"}:
        return "planner", "layer2_planning", "pdd_tdd_plan"
    if kind == "writer" or normalized in {"dev", "writer"}:
        return "writer", "layer1_execution", "task_implementation"
    if kind == "reader" or normalized in {"review", "verify", "test", "judge"}:
        return "reader", "layer1_verification", "evidence_review_verify"
    return "worker", "layer1_worker", "task_runtime"


def _worker_signal_index(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for seq, event in _events_with_seq(state_dir, config=config):
        actor = getattr(event, "actor", None)
        if not actor:
            continue
        event_type = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        record = out.setdefault(str(actor), {})
        record["last_event_seq"] = seq
        record["last_event_type"] = event_type
        record["last_activity_at"] = getattr(event, "ts", "")
        summary = _event_signal_summary(event_type, payload)
        if summary:
            record["last_output_summary"] = summary

        stop_reason = _payload_first_string(payload, [
            "provider_stop_reason",
            "stop_reason",
            "reason",
        ])
        if stop_reason:
            record["provider_stop_reason"] = stop_reason

        context_ratio = _payload_context_ratio(payload)
        if context_ratio is not None:
            record["context_usage_ratio"] = context_ratio

        if event_type == "worker.context.critical":
            record["attention_state"] = "context_critical"
            record["needs_input_reason"] = summary or "context usage is critical"
            continue
        if event_type == "worker.context.warning":
            if record.get("attention_state") != "context_critical":
                record["attention_state"] = "context_warning"
                record["needs_input_reason"] = summary or "context usage is high"
            continue
        if event_type == "worker.recycled":
            _clear_context_attention(record)
            continue
        if event_type == "agent.timeout":
            record["attention_state"] = "failed_resumable"
            record["needs_input_reason"] = summary or "agent timeout"
            continue
        if event_type == "agent.api_blocked":
            attention = _attention_from_provider_stop_reason(stop_reason)
            record["attention_state"] = attention
            record["needs_input_reason"] = summary or stop_reason or "provider blocked"
            continue
        if event_type == "worker.state.changed":
            to_state = str(payload.get("to") or "")
            from_state = str(payload.get("from") or "")
            if to_state in {"blocked", "waiting", "needs_input", "input_required"}:
                record["attention_state"] = "needs_input"
                record["needs_input_reason"] = summary or to_state
            elif to_state in {"crashed", "failed"}:
                record["attention_state"] = "failed_resumable"
                record["needs_input_reason"] = summary or to_state
            elif to_state in {"stopped", "stopping"}:
                record["attention_state"] = "stopped_resumable"
                record["needs_input_reason"] = summary or to_state
            elif (
                to_state in {"idle", "healthy"}
                and (
                    from_state == "recycling"
                    or "recycle complete" in str(payload.get("reason") or "").lower()
                )
            ):
                _clear_context_attention(record)
    return out


def _agent_debug_projection(
    state_dir: Path,
    *,
    instance_id: str,
    transport: str,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict:
    tmux_session = getattr(config.session, "tmux_session", "") if config is not None else ""
    log_path = state_dir / "logs" / f"{instance_id}.log"
    return {
        "transport": transport,
        "log_path": (
            _display_path(state_dir, log_path, project_root=project_root)
            if log_path.exists()
            else ""
        ),
        "briefing_paths": [
            _display_path(state_dir, path, project_root=project_root)
            for path in _briefing_paths_for_instance(state_dir, instance_id)
        ],
        "attach_hint": f"zf attach {instance_id}" if transport == "tmux" else "",
        "tmux_session": tmux_session if transport == "tmux" else "",
        "tmux_target": f"{tmux_session}:{instance_id}" if transport == "tmux" and tmux_session else "",
        "state_inference": "debug_only_not_truth",
    }


def _workdirs(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> list[dict]:
    project_root = _resolve_project_root_for_state(state_dir, project_root)
    roles = list(config.roles) if config is not None else []
    meta = _role_session_meta(state_dir)
    if not roles:
        return [
            _workdir_projection(
                state_dir,
                instance_id=instance_id,
                role_name=instance_id.split("-")[0],
                role_kind="unknown",
                backend=str(m.get("backend", "")),
                workdir=state_dir / "workdirs" / instance_id,
                project_path=state_dir / "workdirs" / instance_id / "project",
                branch_or_ref="",
                mode="unknown",
                enabled=False,
                config=config,
            )
            for instance_id, m in sorted(meta.items())
        ]

    plans = []
    active_tasks = _active_task_by_instance(state_dir, config=config)
    manager = None
    try:
        manager = WorkdirManager(
            state_dir=state_dir,
            project_root=project_root,
            config=config,
        )
    except Exception:
        manager = None

    planned_ids: set[str] = set()
    for role in roles:
        planned_ids.add(role.instance_id)
        if manager is not None:
            plan = manager.plan(role)
            plans.append(_workdir_projection(
                state_dir,
                instance_id=plan.instance_id,
                role_name=plan.role_name,
                role_kind=plan.role_kind,
                backend=plan.backend,
                workdir=Path(plan.workdir),
                project_path=Path(plan.project_path),
                branch_or_ref=plan.branch_or_ref,
                mode=plan.mode,
                enabled=plan.enabled,
                config=config,
                active_tasks=active_tasks,
            ))
        else:
            workdir = state_dir / "workdirs" / role.instance_id
            plans.append(_workdir_projection(
                state_dir,
                instance_id=role.instance_id,
                role_name=role.name,
                role_kind=role.role_kind,
                backend=role.backend,
                workdir=workdir,
                project_path=workdir / "project",
                branch_or_ref="",
                mode="unknown",
                enabled=False,
                config=config,
                active_tasks=active_tasks,
            ))
    for instance_id, m in sorted(meta.items()):
        if instance_id in planned_ids:
            continue
        if _runtime_instance_retired(m):
            continue
        workdir = state_dir / "workdirs" / instance_id
        plans.append(_workdir_projection(
            state_dir,
            instance_id=instance_id,
            role_name=str(m.get("parent_role") or _parent_role_from_instance(instance_id)),
            role_kind=str(m.get("role_kind") or "unknown"),
            backend=str(m.get("backend") or ""),
            workdir=workdir,
            project_path=workdir / "project",
            branch_or_ref=str(m.get("branch_or_ref") or ""),
            mode=str(m.get("workdir_mode") or "runtime"),
            enabled=bool(m.get("workdir_enabled", True)),
            config=config,
            active_tasks=active_tasks,
        ))
    return plans


def _role_sessions_data(state_dir: Path) -> dict:
    import yaml

    path = state_dir / "role_sessions.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _role_session_meta(state_dir: Path) -> dict[str, dict]:
    data = _role_sessions_data(state_dir)
    meta = data.get("instance_meta", {}) or {}
    if not isinstance(meta, dict):
        return {}
    return {
        str(k): dict(v) if isinstance(v, dict) else {}
        for k, v in meta.items()
    }


def _role_session_ids(state_dir: Path) -> dict[str, str]:
    data = _role_sessions_data(state_dir)
    roles = data.get("roles", {}) or {}
    if not isinstance(roles, dict):
        return {}
    return {str(k): str(v) for k, v in roles.items()}


def _worker_states(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> dict[str, str]:
    states: dict[str, str] = {}
    for _, event in _events_with_seq(state_dir, config=config):
        if getattr(event, "type", "") != "worker.state.changed":
            continue
        payload = getattr(event, "payload", {}) or {}
        to = payload.get("to") if isinstance(payload, dict) else None
        actor = getattr(event, "actor", None)
        if to and actor:
            states[str(actor)] = str(to)
    return states


def _workdir_for_instance(
    state_dir: Path,
    instance_id: str,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict:
    if not instance_id:
        return {}
    for item in _workdirs(
        state_dir,
        config=config,
        project_root=project_root,
    ):
        if item.get("instance_id") == instance_id:
            return item
    return {}


def _workdir_projection(
    state_dir: Path,
    *,
    instance_id: str,
    role_name: str,
    role_kind: str,
    backend: str,
    workdir: Path,
    project_path: Path,
    branch_or_ref: str,
    mode: str,
    enabled: bool,
    config: ZfConfig | None = None,
    active_tasks: dict[str, str] | None = None,
) -> dict:
    git_cwd = project_path if (project_path / ".git").exists() else None
    branch = branch_or_ref
    commit = ""
    status = ""
    dirty = False
    git_error = ""
    if git_cwd is not None:
        branch_result = _git(git_cwd, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=1)
        commit_result = _git(git_cwd, ["rev-parse", "--short", "HEAD"], timeout=1)
        status_result = _git(
            git_cwd,
            ["status", "--porcelain", "-uno"],
            max_bytes=12_000,
            timeout=1,
        )
        branch = branch_result.text.strip() or branch_or_ref
        commit = commit_result.text.strip()
        status = status_result.text
        dirty = bool(status.strip())
        git_error = branch_result.error or commit_result.error or status_result.error

    marker = _read_json_file(workdir / ".zf-workdir-owner.json")
    if active_tasks is None:
        active_tasks = _active_task_by_instance(state_dir, config=config)
    active_task = active_tasks.get(instance_id, "")
    return {
        "instance_id": instance_id,
        "role_name": role_name,
        "role_kind": role_kind,
        "backend": backend,
        "workdir": str(workdir),
        "project_path": str(project_path),
        "branch_or_ref": branch_or_ref,
        "branch": branch,
        "commit": commit,
        "exists": workdir.exists(),
        "project_exists": project_path.exists(),
        "mode": mode,
        "enabled": enabled,
        "dirty": dirty,
        "status_lines": [line for line in status.splitlines() if line][:80],
        "owner": redact_obj(marker) if marker else {},
        "active_task": active_task,
        "error": git_error,
    }
