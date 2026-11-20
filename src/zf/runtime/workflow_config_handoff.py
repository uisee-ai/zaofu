"""Quiescent, project-scoped policy handoff before publishing a new invoke."""
from __future__ import annotations

from functools import wraps
from collections.abc import Callable
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any

from zf.core.config.loader import load_config
from zf.core.state.locks import locked_path
from zf.core.task.store import TaskStore
from zf.runtime.workflow_config_consumer import approved_config_consumer_error, consumer_policy_digest
from zf.runtime.workflow_runtime_start import _read, _runtime_owner, ensure_project_runtime


def serialized_workflow_submit(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(function)
    def submit(**kwargs: Any) -> dict[str, Any]:
        from zf.runtime.workflow_paths import bind_project_paths
        path, _, _ = bind_project_paths(kwargs["config_path"],
            kwargs.get("intake_path", kwargs["config_path"]), kwargs.get("project_root"))
        config = load_config(path)
        state = (path.parent / config.project.state_dir).resolve()
        with locked_path(state / "locks" / "workflow-submit", timeout_seconds=90):
            return function(**kwargs)
    return submit


def handoff_blockers(state_dir: Path, events: list) -> list[str]:
    from zf.runtime.run_admission import build_run_admission_projection
    from zf.runtime.run_scope import event_run_id, run_aliases
    from zf.runtime.workflow_operation import reduce_workflow_operations, TERMINAL_OPERATION_STATUSES
    from zf.runtime.channel_reply_remediation import fold_channel_reply_states

    runs = build_run_admission_projection(events)
    # Blocked Runs can resume; their policy must survive a controller restart.
    blockers = [f"Run {r.run_id} ({r.status})" for r in runs.runs.values()
                if not r.terminal or r.terminal_type == "run.goal.blocked"]
    aliases = run_aliases(events)
    settled = {r.run_id for r in runs.runs.values() if r.terminal and r.terminal_type != "run.goal.blocked"}
    answered = {str(e.causation_id or e.payload.get("source_event_id") or "") for e in events
                if e.type in {"workflow.invoke.accepted", "workflow.invoke.rejected"}}
    blockers += [f"pending invoke {e.id}" for e in events
                 if e.type == "workflow.invoke.requested" and e.id not in answered
                 and event_run_id(e, aliases=aliases) not in settled]
    blockers += [f"operation {key} ({op['status']})" for key, op in reduce_workflow_operations(events).items()
                 if op["status"] not in TERMINAL_OPERATION_STATUSES]
    blockers += [f"Channel reply {row['request_id']}" for row in fold_channel_reply_states(events).values()
                 if row["status"] in {"pending", "queued", "running"} or row["failure_status"] == "delivery_unknown"]
    blockers += [f"Task {task.id} ({task.status})" for task in TaskStore(state_dir / "kanban.json").list_all()
                 if task.status in {"in_progress", "review", "blocked"} or task.active_dispatch_id]
    return blockers


def current_handoff_blockers(state_dir: Path) -> list[str]:
    from zf.core.events import EventLog
    log = EventLog(state_dir / "events.jsonl")
    try:
        return handoff_blockers(state_dir, log.read_all())
    finally:
        log.close()


def prepare_workflow_consumer(*, config_path: Path, state_dir: Path, writer: Any,
                              payload: dict) -> dict[str, Any]:
    """Never restart active work, change its policy, or submit to a stale owner.

    Called after proposal validation, before submit/Run/invoke facts. The shared
    submit lock serializes concurrent UI/CLI submissions. Admission is fenced
    while checking quiescence and stopping the old owner; startup happens after
    releasing that fence so catch-up can acquire its normal admission lock.
    """
    if not payload.get("workflow_proposal_ref"):
        return {}
    config = load_config(config_path)
    if error := approved_config_consumer_error(state_dir, config, payload):
        return _blocked(error)
    with locked_path(state_dir / "locks" / "run-admission", timeout_seconds=5):
        owner = _runtime_owner(state_dir)
        if not owner:
            return {}  # Normal cold start remains owned by the submission path.
        ready = _read(state_dir / "processes" / "runtime-ready.json")
        if not ready:
            return _blocked("Runtime 缺少配置回执；请先通过 zf restart 完成受控重启。")
        if ready.get("epoch") != owner.get("epoch") or ready.get("owner_pid") != owner.get("owner_pid"):
            return _blocked("Runtime 正在启动；请等待就绪后重试此提案。")
        expected = consumer_policy_digest(config)
        if ready.get("execution_policy_digest") == expected:
            return {}
        if ready.get("tmux_session") != config.session.tmux_session:
            return _blocked("Runtime 缺少同项目配置回执或 session 已变更；请先通过 zf restart 完成受控重启。")
        if blockers := handoff_blockers(state_dir, writer.event_log.read_all()):
            return _blocked("配置已保存，现有工作仍使用原策略。等待这些工作结束后重试此提案：" + "; ".join(blockers[:8]))
        previous_owner_pid = owner["owner_pid"]
        from zf.runtime.cli_command import default_zf_cli_cmd
        env = {key: value for key, value in os.environ.items() if key not in {
            "ZF_WORKFLOW_ACTION_TOKEN", "ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE", "ZF_WEB_TRUSTED_SESSION", "ZF_TMUX_SESSION"}}
        env.update(ZF_PROJECT_ROOT=str(config_path.parent.resolve()), ZF_STATE_DIR=str(state_dir.resolve()),
                   ZF_CLI_CMD=default_zf_cli_cmd())
        try:
            stopped = subprocess.run([*shlex.split(default_zf_cli_cmd()), "stop", "--fast", "--include-run-manager"],
                cwd=config_path.parent, env=env, capture_output=True, text=True, timeout=45)
            if stopped.returncode:
                return _blocked("Runtime 配置交接停机失败；检查项目日志后重试。")
            deadline = time.monotonic() + 10
            while _runtime_owner(state_dir):
                if time.monotonic() >= deadline:
                    return _blocked("原 Runtime 尚未退出；未提交新 Workflow，请稍后重试。")
                time.sleep(0.1)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _blocked(f"Runtime 配置交接失败：{exc}")
    started = ensure_project_runtime(project_root=config_path.parent, state_dir=state_dir, writer=writer)
    if not started.get("ok"):
        return _blocked(str(started.get("reason") or "Runtime 正在重启；请等待后重试此提案。"))
    ready = _read(state_dir / "processes" / "runtime-ready.json")
    owner = _runtime_owner(state_dir)
    if (ready.get("execution_policy_digest") != expected or ready.get("epoch") != owner.get("epoch")
            or ready.get("owner_pid") != owner.get("owner_pid")):
        return _blocked("新 Runtime 未确认已审批配置；未提交 Workflow，请检查配置后重试。")
    return {"runtime_start": {**started, "config_handoff": True, "previous_owner_pid": previous_owner_pid}}


def _blocked(reason: str) -> dict[str, Any]:
    return {"ok": False, "status": "STOP", "_status_code": 409, "reason": reason,
            "workflow_invoke_status": "not_requested", "next_action": reason,
            "blockers": [{"severity": "STOP", "kind": "workflow_config_handoff", "message": reason}]}
