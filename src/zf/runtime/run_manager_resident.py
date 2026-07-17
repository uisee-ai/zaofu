"""Resident Run Manager agent spawn support.

The deterministic Run Manager tick remains the owner of state transitions.
This module only starts an optional long-lived tmux agent that observes the
run and emits bounded observation/recommendation events through ``zf emit``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from zf.core.config.schema import (
    ConstraintsConfig,
    RoleConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.backend import get_adapter
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.injection import generate_role_instructions
from zf.runtime.transport import DispatchContext


RESIDENT_RUN_MANAGER_ROLE_NAME = "run-manager"
RUN_MANAGER_RESIDENT_RESTART_REQUESTED = "run.manager.resident.restart_requested"
RUN_MANAGER_RESIDENT_RESTARTED = "run.manager.resident.restarted"
RUN_MANAGER_RESIDENT_RESTART_FAILED = "run.manager.resident.restart_failed"
RUN_MANAGER_RESIDENT_PRESERVED = "run.manager.resident.preserved"
RUN_MANAGER_RESIDENT_REBOUND = "run.manager.resident.rebound"
_PRESERVED_MARKER = "run-manager-resident-preserved.json"


def resident_run_manager_enabled(config: ZfConfig) -> bool:
    resident = config.runtime.run_manager.resident_agent
    return bool(resident.enabled)


def resident_run_manager_session_mode(config: ZfConfig) -> str:
    resident = config.runtime.run_manager.resident_agent
    mode = str(getattr(resident, "session_mode", "shared") or "shared").strip()
    return mode if mode in {"shared", "dedicated"} else "shared"


def resident_run_manager_tmux_session(config: ZfConfig) -> str:
    """Return the tmux session that should host the resident agent."""
    resident = config.runtime.run_manager.resident_agent
    if resident_run_manager_session_mode(config) != "dedicated":
        return config.session.tmux_session
    configured = str(getattr(resident, "tmux_session", "") or "").strip()
    if configured:
        return configured
    return f"{config.session.tmux_session}-run-manager"


def build_resident_run_manager_role(config: ZfConfig) -> RoleConfig | None:
    if not resident_run_manager_enabled(config):
        return None
    run_manager = config.runtime.run_manager
    resident = run_manager.resident_agent
    return RoleConfig(
        name=RESIDENT_RUN_MANAGER_ROLE_NAME,
        backend=run_manager.backend,
        role_kind="reader",
        permission_mode="bypass",
        transport=resident.transport,
        instance_id=resident.instance_id,
        stuck_threshold_seconds=600.0,
        constraints=ConstraintsConfig(
            allowed_paths=[
                "${ZF_PROJECT_ROOT}",
                "${ZF_STATE_DIR}",
            ],
        ),
        stages=["run-monitor"],
        triggers=[
            "run.manager.tick.completed",
            "run.manager.transition",
            "supervisor.decision.recorded",
            "autoresearch.loop.completed",
            "human.escalate",
        ],
        publishes=[
            "run.manager.agent.observation",
            "run.manager.agent.recommendation",
            "orchestrator.rework.triage.recorded",
            "human.escalate",
        ],
    )


def dedicated_resident_run_manager_role(config: ZfConfig) -> RoleConfig | None:
    """Return the resident role only when it is hosted in a dedicated session."""
    if not resident_run_manager_enabled(config):
        return None
    if resident_run_manager_session_mode(config) != "dedicated":
        return None
    return build_resident_run_manager_role(config)


def resident_preserve_marker_path(state_dir: Path) -> Path:
    return Path(state_dir) / _PRESERVED_MARKER


def build_resident_preserve_payload(
    *,
    config: ZfConfig,
    state_dir: Path,
    reason: str,
) -> dict[str, Any] | None:
    role = dedicated_resident_run_manager_role(config)
    if role is None:
        return None
    return {
        "schema_version": "run-manager.resident-preserve.v1",
        "instance_id": role.instance_id,
        "role_name": role.name,
        "session_mode": "dedicated",
        "workflow_tmux_session": config.session.tmux_session,
        "tmux_session": resident_run_manager_tmux_session(config),
        "state_dir": str(state_dir),
        "reason": reason,
        "shutdown_scope": "workflow_only",
        "restart_strategy": "control_plane_restart_preserve_run_manager",
    }


def write_resident_preserve_marker(
    *,
    state_dir: Path,
    payload: dict[str, Any],
) -> Path:
    marker = resident_preserve_marker_path(state_dir)
    marker.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return marker


def read_resident_preserve_marker(state_dir: Path) -> dict[str, Any] | None:
    marker = resident_preserve_marker_path(state_dir)
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def clear_resident_preserve_marker(state_dir: Path) -> None:
    resident_preserve_marker_path(state_dir).unlink(missing_ok=True)


def build_resident_run_manager_briefing(
    *,
    project_root: Path,
    state_dir: Path,
    role: RoleConfig,
) -> str:
    payload_example = json.dumps(
        {
            "summary": "one-line observation",
            "status": "watching",
            "evidence_refs": ["projections/run_manager.json"],
            "recommended_route": "wait | controlled_action | autoresearch | repair | reflect | human",
            "repair_task_payload": {
                "title": "optional bounded repair task title",
                "contract": {
                    "scope": ["src/zf/**", "tests/**"],
                    "verification": "focused pytest command",
                },
            },
        },
        ensure_ascii=False,
    )
    return "\n".join([
        "# Resident Run Manager",
        "",
        f"Role instance: `{role.instance_id}`",
        f"Project root: `{project_root}`",
        f"State dir: `{state_dir}`",
        "",
        "你是 ZaoFu 的常驻 Run Manager 监工 agent。你的工作是持续观察当前 run,",
        "汇总状态,指出卡点,并在已有受控入口可用时建议或触发恢复动作。",
        "你不是 monitor-only:当证据显示需要 harness 修复、候选门修复或恢复动作时,",
        "通过 `run.manager.agent.recommendation` 请求 Run Manager 拉起 bounded repair worker、",
        "Autoresearch、reflect、controlled action 或 human decision。",
        "",
        "## Hard Boundaries",
        "",
        "- 不直接编辑 `events.jsonl`、`kanban.json`、`feature_list.json`、"
        "`session.yaml`、`role_sessions.yaml`。",
        "- 默认不直接修改业务代码或 harness 代码;若后续收到明确的 repair task briefing,",
        "  你可以作为 Run Manager 分派的 bounded repair worker 执行该修复。",
        "- 不自动 merge、不删除分支、不杀 tmux session、不重置 git。",
        "- 不替代 deterministic Run Manager tick、Supervisor、Autoresearch 或 Orchestrator。",
        "- 需要记录观察时,只通过 `zf emit` 写事件。",
        "",
        "## Observe Loop",
        "",
        "每 60-120 秒执行一轮:",
        "",
        f"1. 运行 `ZF_STATE_DIR={state_dir} zf status`。",
        "2. 读取最近事件: `tail -n 120 \"$ZF_STATE_DIR/events.jsonl\"`。",
        "3. 若存在,读取 `projections/run_manager.json`、"
        "`projections/run_status_explain.json`、`projections/supervisor/snapshot.json`。",
        "4. 判断是否只是正常等待、是否有确定性 action、是否需要 repair worker、"
        "autoresearch 深诊断、reflect 或人工介入。",
        "5. 写一条观察事件,例如:",
        "",
        "```bash",
        f"ZF_STATE_DIR={state_dir} zf emit run.manager.agent.observation "
        f"--actor {role.instance_id} --state-dir {state_dir} "
        f"--payload '{payload_example}'",
        "```",
        "",
        "如果 `projections/run_manager.json` 或 `zf status` 显示 run 已经 passed/complete,",
        "且之后没有 `verify.failed`、`judge.failed`、`run.failed`、新的",
        "`candidate.ready` 或人工 resume/reopen 信号,停止常规 proof-of-life 写入;",
        "只在发现 reopen/maintenance 需要动作时再写 observation 或 recommendation。",
        "",
        "## Decision Discipline",
        "",
        "- 如果只是 worker 正在正常执行,结论写 `status=watching`,不要打断。",
        "- 建议 resume/rework 前必须依次核对三点(任一命中 → 建议 wait 并写明依据):",
        "  ① 该任务是否已有完成证据(task.done/test.passed/judge.passed 等 closeout 事件,"
        "不论新旧——完成证据早于 stall 信号仍然算完成);",
        "  ② 所在 fanout 聚合是否未收拢、是否有兄弟 child 在飞——等待中的流不是停滞;",
        "  ③ 同一 checkpoint 是否已有 workflow.resume.gate_unroutable 前科——"
        "unroutable 是确定性事实,重复建议只会产生 no-op。",
        "- 如果发现已知 deterministic recovery 可以走,写"
        " `run.manager.agent.recommendation`,说明 action 名、证据 refs、预期 downstream event。",
        "- 如果是 harness bug、candidate hygiene/gate bug、projection/action bug,优先写"
        " `run.manager.agent.recommendation` 且 `recommended_route=repair`,附带 bounded"
        " `repair_task_payload`、scope、verification 和 evidence refs。",
        "- 如果诊断证据不足,写 `recommended_route=autoresearch` 或 `recommended_route=reflect`;不要空转。",
        "- 如果安全边界不清、需要 merge/restart/删除状态,发 `human.escalate`。",
        "- 每次建议必须引用 event id、projection ref 或命令输出摘要,不要只写主观判断。",
        "",
        "现在开始观察当前 run。先执行一次 `zf status` 和事件/projection 快照读取。",
    ])


def spawn_resident_run_manager(
    *,
    config: ZfConfig,
    state_dir: Path,
    project_root: Path,
    coordinator: Any,
    transport: Any,
    event_log: Any,
    instructions_dir: Path,
    dry_run: bool = False,
) -> RoleConfig | None:
    role = build_resident_run_manager_role(config)
    if role is None:
        return None
    if any(existing.instance_id == role.instance_id for existing in config.roles):
        return None

    registrar = getattr(transport, "register_role", None)
    if callable(registrar):
        registrar(role)

    coordinator.spawn(role, cwd=project_root)
    ready = True
    if not dry_run:
        adapter = get_adapter(role.backend)
        ready = transport.wait_ready(
            role.instance_id,
            adapter.ready_pattern,
            timeout=60.0,
        )

    instructions_dir.mkdir(parents=True, exist_ok=True)
    (instructions_dir / f"{role.instance_id}.md").write_text(
        generate_role_instructions(
            config,
            role,
            state_dir_ref=state_dir,
            project_root=project_root,
        ),
        encoding="utf-8",
    )

    session_mode = resident_run_manager_session_mode(config)
    tmux_session = resident_run_manager_tmux_session(config)
    event_log.append(ZfEvent(
        type="run.manager.resident.spawned",
        actor="zf-cli",
        payload={
            "schema_version": "run-manager.resident.v1",
            "role": asdict(role),
            "backend": role.backend,
            "transport": role.transport,
            "ready": ready,
            "session_mode": session_mode,
            "tmux_session": tmux_session,
        },
    ))

    briefing_path = state_dir / "briefings" / "run-manager-resident.md"
    briefing_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_resident_run_manager_briefing(
        project_root=project_root,
        state_dir=state_dir,
        role=role,
    )
    briefing_path.write_text(prompt, encoding="utf-8")

    if config.runtime.run_manager.resident_agent.prompt_on_start and ready and not dry_run:
        transport.send_task(
            role.instance_id,
            briefing_path,
            prompt,
            context=DispatchContext(
                role_name=role.name,
                instance_id=role.instance_id,
                backend=role.backend,
                briefing_path=briefing_path,
            ),
        )
        prompted = True
    else:
        prompted = False

    event_log.append(ZfEvent(
        type="run.manager.resident.prompted",
        actor="zf-cli",
        payload={
            "schema_version": "run-manager.resident.v1",
            "instance_id": role.instance_id,
            "briefing_path": str(briefing_path),
            "prompted": prompted,
            "zf_cli_cmd": zf_cli_cmd(),
        },
    ))
    return role


def rebind_preserved_resident_run_manager(
    *,
    config: ZfConfig,
    state_dir: Path,
    project_root: Path,
    transport: Any,
    event_log: Any,
    instructions_dir: Path,
    marker_payload: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> RoleConfig | None:
    """Reprompt a preserved dedicated resident agent after workflow restart.

    This does not spawn or kill tmux panes. It only verifies the existing
    resident role is routable, rewrites current instructions/briefing, and
    sends a fresh observation prompt so the resident monitor re-anchors on the
    new control-plane process.
    """

    role = dedicated_resident_run_manager_role(config)
    if role is None:
        return None
    running = True
    try:
        running = bool(transport.for_role(role.instance_id).is_session_running())
    except Exception:
        running = False
    if not running and not dry_run:
        return None

    registrar = getattr(transport, "register_role", None)
    if callable(registrar):
        try:
            registrar(role)
        except Exception:
            pass

    instructions_dir.mkdir(parents=True, exist_ok=True)
    (instructions_dir / f"{role.instance_id}.md").write_text(
        generate_role_instructions(
            config,
            role,
            state_dir_ref=state_dir,
            project_root=project_root,
        ),
        encoding="utf-8",
    )

    briefing_path = state_dir / "briefings" / "run-manager-resident.md"
    briefing_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_resident_run_manager_briefing(
        project_root=project_root,
        state_dir=state_dir,
        role=role,
    ).rstrip()
    prompt += (
        "\n\n## Control-Plane Rebind\n\n"
        "ZF workflow/control-plane restarted while this dedicated Run Manager "
        "session was preserved. First tick mode is observe-only: reread "
        "`zf status`, recent events, and projections before recommending any "
        "mutating action.\n"
    )
    briefing_path.write_text(prompt, encoding="utf-8")

    tmux_session = resident_run_manager_tmux_session(config)
    event_log.append(ZfEvent(
        type=RUN_MANAGER_RESIDENT_REBOUND,
        actor="zf-cli",
        payload={
            "schema_version": "run-manager.resident-rebind.v1",
            "instance_id": role.instance_id,
            "backend": role.backend,
            "transport": role.transport,
            "session_mode": "dedicated",
            "tmux_session": tmux_session,
            "workflow_restart": True,
            "marker": marker_payload or {},
        },
    ))

    prompted = False
    if config.runtime.run_manager.resident_agent.prompt_on_start and not dry_run:
        try:
            transport.send_task(
                role.instance_id,
                briefing_path,
                prompt,
                context=DispatchContext(
                    role_name=role.name,
                    instance_id=role.instance_id,
                    backend=role.backend,
                    briefing_path=briefing_path,
                ),
            )
            prompted = True
        except Exception:
            prompted = False
    elif dry_run:
        prompted = bool(config.runtime.run_manager.resident_agent.prompt_on_start)

    event_log.append(ZfEvent(
        type="run.manager.resident.prompted",
        actor="zf-cli",
        payload={
            "schema_version": "run-manager.resident.v1",
            "instance_id": role.instance_id,
            "briefing_path": str(briefing_path),
            "prompted": prompted,
            "rebound": True,
            "tmux_session": tmux_session,
            "zf_cli_cmd": zf_cli_cmd(),
        },
    ))
    return role


def apply_pending_resident_restart_requests(
    *,
    config: ZfConfig,
    state_dir: Path,
    project_root: Path,
    writer: Any,
    event_log: Any,
    coordinator: Any,
    transport: Any,
    instructions_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Consume one pending resident restart request.

    This is the L0 watchdog actuator for the resident Run Manager only. It
    never restarts the workflow tmux session and never mutates task truth.
    """

    if not resident_run_manager_enabled(config):
        return 0
    try:
        events = list(event_log.read_all())
    except Exception:
        events = []
    consumed = _consumed_restart_request_ids(events)
    for event in events:
        if event.type != RUN_MANAGER_RESIDENT_RESTART_REQUESTED:
            continue
        if event.id in consumed:
            continue
        _apply_resident_restart_request(
            event=event,
            config=config,
            state_dir=Path(state_dir),
            project_root=Path(project_root),
            writer=writer,
            coordinator=coordinator,
            transport=transport,
            instructions_dir=instructions_dir or Path(state_dir) / "instructions",
            dry_run=dry_run,
        )
        return 1
    return 0


def _apply_resident_restart_request(
    *,
    event: ZfEvent,
    config: ZfConfig,
    state_dir: Path,
    project_root: Path,
    writer: Any,
    coordinator: Any,
    transport: Any,
    instructions_dir: Path,
    dry_run: bool,
) -> None:
    role = build_resident_run_manager_role(config)
    if role is None:
        _emit_restart_failed(
            writer,
            event=event,
            reason="resident run manager role is disabled",
        )
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    requested_instance = str(payload.get("instance_id") or role.instance_id or "").strip()
    if requested_instance and requested_instance != role.instance_id:
        _emit_restart_failed(
            writer,
            event=event,
            reason=(
                "resident restart request instance mismatch "
                f"(requested={requested_instance}, configured={role.instance_id})"
            ),
        )
        return
    session_mode = resident_run_manager_session_mode(config)
    tmux_session = str(payload.get("tmux_session") or resident_run_manager_tmux_session(config))

    registrar = getattr(transport, "register_role", None)
    if callable(registrar):
        try:
            registrar(role)
        except Exception:
            pass

    init_attempted = False
    terminate_error = ""
    try:
        role_transport = _transport_for_role(transport, role.instance_id)
        if session_mode == "dedicated":
            running = True
            is_running = getattr(role_transport, "is_session_running", None)
            if callable(is_running):
                running = bool(is_running())
            if not running:
                init = getattr(role_transport, "init", None)
                if callable(init):
                    init()
                    init_attempted = True
    except Exception as exc:
        terminate_error = str(exc)

    terminator = getattr(transport, "terminate", None)
    if callable(terminator):
        try:
            terminator(role.instance_id)
        except Exception as exc:
            terminate_error = str(exc)

    try:
        coordinator.spawn(role, cwd=project_root)
    except Exception as exc:
        _emit_restart_failed(
            writer,
            event=event,
            reason=f"resident spawn failed: {exc}",
            extra={
                "terminated_old_slot": bool(not terminate_error),
                "terminate_error": terminate_error,
                "tmux_session": tmux_session,
                "session_mode": session_mode,
            },
        )
        return

    ready = True
    if not dry_run:
        try:
            adapter = get_adapter(role.backend)
            ready = bool(transport.wait_ready(role.instance_id, adapter.ready_pattern, timeout=60.0))
        except Exception:
            ready = False

    instructions_dir.mkdir(parents=True, exist_ok=True)
    (instructions_dir / f"{role.instance_id}.md").write_text(
        generate_role_instructions(
            config,
            role,
            state_dir_ref=state_dir,
            project_root=project_root,
        ),
        encoding="utf-8",
    )
    briefing_path = state_dir / "briefings" / "run-manager-resident.md"
    briefing_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_resident_run_manager_briefing(
        project_root=project_root,
        state_dir=state_dir,
        role=role,
    ).rstrip()
    prompt += (
        "\n\n## Restart Recovery\n\n"
        "This resident agent was restarted by the L0 watchdog. "
        "First tick mode is observe-only: emit an observation before any "
        "mutating recommendation.\n"
    )
    briefing_path.write_text(prompt, encoding="utf-8")

    spawned = writer.emit(
        "run.manager.resident.spawned",
        actor="run-manager-watchdog",
        causation_id=event.id,
        payload={
            "schema_version": "run-manager.resident.v1",
            "role": asdict(role),
            "backend": role.backend,
            "transport": role.transport,
            "ready": ready,
            "session_mode": session_mode,
            "tmux_session": tmux_session,
            "restart": True,
            "source_restart_request_id": event.id,
            "terminated_old_slot": bool(not terminate_error),
            "terminate_error": terminate_error,
            "dedicated_session_init_attempted": init_attempted,
        },
    )
    prompted = False
    if config.runtime.run_manager.resident_agent.prompt_on_start and ready and not dry_run:
        try:
            transport.send_task(
                role.instance_id,
                briefing_path,
                prompt,
                context=DispatchContext(
                    role_name=role.name,
                    instance_id=role.instance_id,
                    backend=role.backend,
                    briefing_path=briefing_path,
                ),
            )
            prompted = True
        except Exception:
            prompted = False
    prompt_event = writer.emit(
        "run.manager.resident.prompted",
        actor="run-manager-watchdog",
        causation_id=spawned.id,
        payload={
            "schema_version": "run-manager.resident.v1",
            "instance_id": role.instance_id,
            "briefing_path": str(briefing_path),
            "prompted": prompted,
            "restart": True,
            "tmux_session": tmux_session,
            "target": f"{tmux_session}:{role.instance_id}",
            "first_tick_mode": "observe_only",
            "source_restart_request_id": event.id,
            "zf_cli_cmd": zf_cli_cmd(),
        },
    )
    writer.emit(
        RUN_MANAGER_RESIDENT_RESTARTED,
        actor="run-manager-watchdog",
        causation_id=event.id,
        payload={
            "schema_version": "run-manager.resident-restart.v1",
            "status": "restarted",
            "source_restart_request_id": event.id,
            "instance_id": role.instance_id,
            "tmux_session": tmux_session,
            "session_mode": session_mode,
            "restart_scope": "resident_only",
            "first_tick_mode": "observe_only",
            "workflow_restart": False,
            "state_dir": str(state_dir),
            "briefing_path": str(briefing_path),
            "spawned_event_id": spawned.id,
            "prompted_event_id": prompt_event.id,
            "ready": ready,
            "prompted": prompted,
            "terminated_old_slot": bool(not terminate_error),
            "terminate_error": terminate_error,
        },
    )


def _consumed_restart_request_ids(events: list[ZfEvent]) -> set[str]:
    consumed: set[str] = set()
    for event in events:
        if event.type not in {RUN_MANAGER_RESIDENT_RESTARTED, RUN_MANAGER_RESIDENT_RESTART_FAILED}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request_id = str(payload.get("source_restart_request_id") or event.causation_id or "").strip()
        if request_id:
            consumed.add(request_id)
    return consumed


def _transport_for_role(transport: Any, instance_id: str) -> Any:
    getter = getattr(transport, "for_role", None)
    if callable(getter):
        return getter(instance_id)
    return transport


def _emit_restart_failed(
    writer: Any,
    *,
    event: ZfEvent,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    writer.emit(
        RUN_MANAGER_RESIDENT_RESTART_FAILED,
        actor="run-manager-watchdog",
        causation_id=event.id,
        payload={
            "schema_version": "run-manager.resident-restart.v1",
            "status": "failed",
            "source_restart_request_id": event.id,
            "reason": reason,
            "instance_id": str(payload.get("instance_id") or "run-manager"),
            "tmux_session": str(payload.get("tmux_session") or ""),
            "restart_scope": "resident_only",
            "workflow_restart": False,
            **(extra or {}),
        },
    )
