"""Shared runtime tick services for ``zf start`` and diagnostic watchers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent


_SUPERVISOR_STALENESS_OBSERVER_EVENTS = frozenset({
    "run.manager.tick.completed",
    "run.manager.resident.prompted",
    "run.manager.agent.observation",
    "run.manager.agent.recommendation.consumed",
    "agent.usage",
    "worker.heartbeat",
})


@dataclass
class TickServiceState:
    last_heartbeat_sweep_at: float = 0.0
    last_bug_scan_at: float = 0.0
    last_supervisor_inspection_at: float = 0.0
    last_spine_projection_at: float = 0.0
    last_blackout_check_at: float = 0.0
    last_blackout_emit_at: float = 0.0
    last_blocked_burn_emit_at: float = 0.0
    # G1 idle 驱动器计数(goal.enabled 灰度)
    goal_idle_ticks: int = 0
    goal_last_progress_event_id: str = ""


@dataclass(frozen=True)
class TickServiceIntervals:
    heartbeat_sweep_s: float = 60.0
    bug_scan_s: float = 300.0
    supervisor_inspection_s: float = 300.0
    stale_supervisor_projection_s: float = 300.0
    # 131-P0 shadow spine:游标增量折叠,单轮成本 O(新事件数)
    spine_projection_s: float = 30.0
    # P0-8(审计 D9 G1):采集盲区看门狗——dispatch 活跃而 usage 停更
    cost_blackout_check_s: float = 300.0
    cost_blackout_stale_s: float = 900.0
    cost_blackout_cooldown_s: float = 1800.0
    cost_blackout_startup_grace_s: float = 60.0
    # blocked 角色烧钱看门狗(r5:dev-flow blocked_human 冷却期烧 30M)
    blocked_burn_tokens: int = 250_000
    blocked_burn_cooldown_s: float = 1800.0


@dataclass(frozen=True)
class TickServiceResult:
    heartbeat_sweep: bool = False
    bug_scan: bool = False
    supervisor_inspection: bool = False
    autoresearch_trigger_scan: int = 0
    stall_recovery: bool = False
    remediation_shadow: bool = False
    replan_redrive: bool = False
    self_repair_consumer: bool = False
    run_manager: bool = False
    run_manager_watchdog: bool = False
    run_manager_resident_restart: bool = False
    run_manager_card_delivery: bool = False
    owner_visible_delivery: bool = False
    stale_supervisor_projection: bool = False
    failure_candidates_materialized: int = 0
    failure_closeout_materialized: int = 0
    channel_discussion_sweep: int = 0
    invoke_backlog_replayed: int = 0
    runtime_liveness_reconciled: bool = False


def run_autoresearch_trigger_scan(
    state_dir: Path,
    config: object,
    *,
    event_writer: Any = None,
) -> int:
    from zf.autoresearch.triggers import (
        scan_trigger_decisions,
        trigger_policy_from_config,
        write_trigger_decision,
    )

    policy = trigger_policy_from_config(config)
    if policy.mode != "continuous":
        return 0
    accepted = 0
    for decision in scan_trigger_decisions(state_dir, policy=policy):
        if decision.decision != "accepted":
            continue
        write_trigger_decision(
            state_dir,
            decision,
            event_writer=event_writer,
        )
        accepted += 1
    return accepted


def run_standard_tick_services(
    orchestrator: Any,
    *,
    state: TickServiceState,
    now: float,
    intervals: TickServiceIntervals | None = None,
) -> TickServiceResult:
    """Run the side-effecting services normally driven by watcher ticks.

    This function is intentionally hosted in ``runtime`` instead of
    ``cli.start`` so diagnostic watchers can call the same supervisor /
    autoresearch / self-repair path as ``zf start``.
    """

    intervals = intervals or TickServiceIntervals()
    event_log = orchestrator.event_log
    event_writer = orchestrator.event_writer
    state_dir = Path(orchestrator.state_dir)
    config = orchestrator.config
    project_root = Path(orchestrator.project_root)

    # U3/G3(灰度,goal.enabled 默认关):终局 escalate 未获处置时全体
    # tick 服务静默——escalate = 干净地等人,不是每 5s 空烧(r6.1 4h
    # 6.4M 实弹)。唤醒(操作员动作/新进展)后自动恢复。
    try:
        import time as _qtime

        from zf.runtime.quiescent import mark_quiescent_transition, quiescent_now

        _q_events = event_log.read_all()
        _q_status = quiescent_now(
            _q_events, config=config, now_epoch=_qtime.time(),
        )
        mark_quiescent_transition(event_writer, _q_events, status=_q_status)
        if _q_status.quiescent:
            return TickServiceResult()
    except Exception:
        pass

    heartbeat_sweep = False
    bug_scan = False
    supervisor_inspection = False
    autoresearch_trigger_scan = 0
    stall_recovery = False
    remediation_shadow = False
    replan_redrive = False
    self_repair_consumer = False
    run_manager_watchdog = False
    run_manager_resident_restart = False
    run_manager_card_delivery = False
    owner_visible_delivery = False
    stale_supervisor_projection = False
    failure_candidates_materialized = 0
    failure_closeout_materialized = 0

    if now - state.last_heartbeat_sweep_at >= intervals.heartbeat_sweep_s:
        state.last_heartbeat_sweep_at = now
        runtime_liveness_reconciled = _emit_runtime_liveness_stale_if_needed(
            state_dir=state_dir,
            event_log=event_log,
            writer=event_writer,
        )
        _safe_housekeeping(orchestrator, "heartbeat_sweep", "_run_heartbeat_sweep")
        _safe_housekeeping(orchestrator, "dispatch_sweep", "_run_dispatch_sweep")
        try:
            # G1(灰度):goal active 且空转 N 个心跳 tick → 有界 rescan
            from zf.runtime.goal_idle_driver import maybe_emit_goal_idle_rescan

            maybe_emit_goal_idle_rescan(
                event_log.read_all(),
                config=config,
                state=state,
                event_writer=event_writer,
            )
        except Exception:
            pass
        heartbeat_sweep = True
        owner_visible_delivery = _deliver_owner_visible(state_dir, config)

    if now - state.last_bug_scan_at >= intervals.bug_scan_s:
        state.last_bug_scan_at = now
        bug_scan = _call_noarg(orchestrator, "_run_zaofu_bug_scan")

    if now - state.last_spine_projection_at >= intervals.spine_projection_s:
        state.last_spine_projection_at = now
        # 131-P0 shadow spine:只读派生投影,失败绝不阻塞主循环。
        try:
            from zf.runtime.workflow_spine_projection import (
                refresh_spine_projections,
            )

            refresh_spine_projections(state_dir, event_log)
        except Exception:
            pass

    if now - state.last_blackout_check_at >= intervals.cost_blackout_check_s:
        state.last_blackout_check_at = now
        # P0-8(审计 D9 G1,cangjie r5 实证 11h 盲区零告警):dispatch
        # 持续而 agent.usage 停更 = 采集死了没人知道,预算门按旧值放行。
        try:
            _emit_cost_blackout_if_needed(
                event_log=event_log,
                event_writer=event_writer,
                state_dir=state_dir,
                state=state,
                intervals=intervals,
            )
        except Exception:
            pass
        try:
            _emit_blocked_role_burn_if_needed(
                event_log=event_log,
                event_writer=event_writer,
                state_dir=state_dir,
                state=state,
                intervals=intervals,
            )
        except Exception:
            pass

    if now - state.last_supervisor_inspection_at >= intervals.supervisor_inspection_s:
        state.last_supervisor_inspection_at = now
        supervisor_inspection = _run_supervisor(
            state_dir=state_dir,
            config=config,
            project_root=project_root,
        )
        stale_supervisor_projection = emit_stale_supervisor_projection_if_needed(
            state_dir=state_dir,
            writer=event_writer,
            max_stale_seconds=intervals.stale_supervisor_projection_s,
            event_log=event_log,
        )
        autoresearch_trigger_scan = run_autoresearch_trigger_scan(
            state_dir,
            config,
            event_writer=event_writer,
        )
        stall_recovery = _emit_stall_recoveries(
            event_log=event_log,
            writer=event_writer,
            state_dir=state_dir,
            config=config,
        )
        remediation_shadow = _run_remediation_shadow(
            orchestrator=orchestrator,
            event_log=event_log,
            writer=event_writer,
            state_dir=state_dir,
        )
        replan_redrive = _redrive_replan_adoptions(orchestrator)
        watchdog_result = _run_run_manager_watchdog(
            orchestrator=orchestrator,
            event_log=event_log,
            writer=event_writer,
            state_dir=state_dir,
            config=config,
        )
        run_manager_watchdog = bool(getattr(watchdog_result, "changed", False))
        run_manager_resident_restart = _apply_run_manager_resident_restarts(
            orchestrator=orchestrator,
            event_log=event_log,
            writer=event_writer,
            state_dir=state_dir,
            config=config,
            project_root=project_root,
        )
        run_manager_result = _run_run_manager(
            orchestrator=orchestrator,
            event_log=event_log,
            writer=event_writer,
            state_dir=state_dir,
            config=config,
            project_root=project_root,
        )
        run_manager_card_delivery = _deliver_run_manager_cards(state_dir, config)
        failure_candidates_materialized = _materialize_failure_candidates(
            event_log=event_log,
            writer=event_writer,
            state_dir=state_dir,
        )
        failure_closeout_materialized = failure_candidates_materialized
        invoke_backlog_replayed = _replay_unconsumed_invokes(
            orchestrator, event_log=event_log, now=now,
        )
        channel_discussion_sweep = _sweep_channel_discussions(
            state_dir=state_dir,
            writer=event_writer,
            config=config,
            project_root=project_root,
        )
        source_repair_dispatched = _consume_run_manager_source_repairs(
            event_log=event_log,
            writer=event_writer,
            state_dir=state_dir,
            config=config,
        )
        self_repair_consumer = bool(
            run_manager_result.repairs_dispatched > 0
            or source_repair_dispatched
        )

    return TickServiceResult(
        heartbeat_sweep=heartbeat_sweep,
        bug_scan=bug_scan,
        supervisor_inspection=supervisor_inspection,
        autoresearch_trigger_scan=autoresearch_trigger_scan,
        stall_recovery=stall_recovery,
        remediation_shadow=remediation_shadow,
        replan_redrive=replan_redrive,
        self_repair_consumer=self_repair_consumer,
        run_manager=run_manager_result.changed if "run_manager_result" in locals() else False,
        run_manager_watchdog=run_manager_watchdog,
        run_manager_resident_restart=run_manager_resident_restart,
        run_manager_card_delivery=run_manager_card_delivery,
        owner_visible_delivery=owner_visible_delivery,
        stale_supervisor_projection=stale_supervisor_projection,
        failure_candidates_materialized=failure_candidates_materialized,
        failure_closeout_materialized=failure_closeout_materialized,
        channel_discussion_sweep=channel_discussion_sweep if "channel_discussion_sweep" in locals() else 0,
        invoke_backlog_replayed=invoke_backlog_replayed if "invoke_backlog_replayed" in locals() else 0,
        runtime_liveness_reconciled=runtime_liveness_reconciled if "runtime_liveness_reconciled" in locals() else False,
    )


def _emit_runtime_liveness_stale_if_needed(
    *,
    state_dir: Path,
    event_log: Any,
    writer: Any,
) -> bool:
    try:
        import json
        import yaml

        session_path = state_dir / "session.yaml"
        data = yaml.safe_load(session_path.read_text(encoding="utf-8")) or {}
        if str(data.get("runtime_state") or "") != "active":
            return False
        lock_path = state_dir / "processes" / "watcher.pid.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        owner_pid = int(lock.get("owner_pid") or 0)
        if owner_pid > 1 and _pid_alive(owner_pid):
            return False
        events = event_log.read_all()
        fingerprint = f"runtime-liveness-stale:{lock_path}:{owner_pid}"
        for event in reversed(events[-200:]):
            if event.type != "runtime.liveness.stale":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fingerprint") or "") == fingerprint:
                return False
        writer.append(ZfEvent(
            type="runtime.liveness.stale",
            actor="zf-runtime",
            payload={
                "schema_version": "runtime.liveness-stale.v1",
                "fingerprint": fingerprint,
                "runtime_state": "active",
                "lock_path": str(lock_path),
                "owner_pid": owner_pid,
                "reason": "active_session_watcher_pid_not_alive",
                "recommended_route": "run_manager",
            },
        ))
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def emit_stale_supervisor_projection_if_needed(
    *,
    state_dir: Path,
    writer: Any,
    max_stale_seconds: float = 300.0,
    event_log: Any = None,
) -> bool:
    events = _read_events(state_dir, event_log=event_log)
    if not events:
        return False
    latest_event = events[-1]
    latest_event_ts = _parse_ts(getattr(latest_event, "ts", ""))
    if latest_event_ts is None:
        return False
    snapshot_path = state_dir / "projections" / "supervisor" / "snapshot.json"
    if not snapshot_path.exists():
        return False
    try:
        import json

        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    generated_at = _parse_ts(str(snapshot.get("generated_at") or ""))
    if generated_at is None:
        return False
    age = (latest_event_ts - generated_at).total_seconds()
    if age <= max_stale_seconds:
        return False
    if _only_observer_events_since_snapshot(events, generated_at):
        return False
    fingerprint = f"supervisor_projection_stale:{snapshot_path}"
    if _has_open_stale_projection_event(events, fingerprint=fingerprint):
        return False
    writer.append(ZfEvent(
        type="supervisor.projection.stale",
        actor="zf-supervisor",
        causation_id=getattr(latest_event, "id", None),
        payload={
            "schema_version": "supervisor.projection.stale.v0",
            "fingerprint": fingerprint,
            "snapshot_path": str(snapshot_path),
            "snapshot_generated_at": generated_at.isoformat(),
            "latest_event_id": getattr(latest_event, "id", ""),
            "latest_event_type": getattr(latest_event, "type", ""),
            "latest_event_ts": latest_event_ts.isoformat(),
            "age_seconds": int(age),
            "max_stale_seconds": int(max_stale_seconds),
        },
    ))
    return True


def _only_observer_events_since_snapshot(
    events: list[ZfEvent],
    generated_at,
) -> bool:
    seen_later = False
    for event in events:
        ts = _parse_ts(getattr(event, "ts", ""))
        if ts is None or ts <= generated_at:
            continue
        seen_later = True
        if getattr(event, "type", "") not in _SUPERVISOR_STALENESS_OBSERVER_EVENTS:
            return False
    return seen_later


def _safe_housekeeping(orchestrator: Any, label: str, method_name: str) -> None:
    method = getattr(orchestrator, method_name, None)
    if not callable(method):
        return
    safe = getattr(orchestrator, "_safe_housekeeping", None)
    if callable(safe):
        safe(label, method)
        return
    method()


def _call_noarg(obj: Any, method_name: str) -> bool:
    method = getattr(obj, method_name, None)
    if not callable(method):
        return False
    try:
        method()
        return True
    except Exception:
        return False


def _deliver_owner_visible(state_dir: Path, config: Any) -> bool:
    try:
        from zf.runtime.owner_visible_autodeliver import deliver_owner_visible_to_feishu

        return deliver_owner_visible_to_feishu(state_dir=state_dir, config=config) is not None
    except Exception:
        return False


INVOKE_REPLAY_GRACE_SECONDS = 60.0


def _replay_unconsumed_invokes(
    orchestrator: Any,
    *,
    event_log: Any,
    now: float,
) -> int:
    """Feed verdict-less workflow.invoke.requested events back to the reactor.

    An invoke emitted before the orchestrator was ready (or missed by the
    watcher) otherwise becomes permanent backlog: the watcher only tails new
    events (observed live in the doc 122 approve->fanout e2e). The verdict
    fold (accepted/rejected by source_event_id) is the idempotency guard; the
    grace period keeps this from racing the live watcher path.
    """
    try:
        events = event_log.read_all()
    except Exception:
        return 0
    verdicts = {
        str((event.payload or {}).get("source_event_id") or "")
        for event in events
        if event.type in {"workflow.invoke.accepted", "workflow.invoke.rejected"}
    }
    replayed = 0
    for event in events:
        if event.type != "workflow.invoke.requested":
            continue
        if event.id in verdicts:
            continue
        try:
            from datetime import datetime

            age = now - datetime.fromisoformat(str(event.ts)).timestamp()
        except (TypeError, ValueError):
            continue
        if age < INVOKE_REPLAY_GRACE_SECONDS:
            continue
        handler = getattr(orchestrator, "_on_workflow_invoke_requested", None)
        if handler is None:
            return 0
        try:
            handler(event)
            replayed += 1
        except Exception:
            continue
    return replayed


def _sweep_channel_discussions(
    *,
    state_dir: Path,
    writer: Any,
    config: Any,
    project_root: Path | None,
) -> int:
    try:
        from zf.runtime.channel_discussion import sweep_discussion_deadlines

        return sweep_discussion_deadlines(
            Path(state_dir), writer, config=config, project_root=project_root,
        )
    except Exception:
        return 0


def _deliver_run_manager_cards(state_dir: Path, config: Any) -> bool:
    try:
        from zf.integrations.feishu.run_manager_card import push_run_manager_cards_once
        from zf.integrations.feishu.bot_credentials import transport_for_purpose
        from zf.runtime.owner_visible_autodeliver import _owner_visible_routing_from_env

        routing = _owner_visible_routing_from_env(os.environ)
        if routing is None:
            return False
        receive_role = "approval"
        receive_id = routing.channels.get(receive_role) or routing.channels.get("owner")
        if not receive_id:
            for role, candidate in routing.channels.items():
                if candidate:
                    receive_role = role
                    receive_id = candidate
                    break
        if not receive_id:
            return False
        identity = getattr(getattr(config, "integrations", None), "feishu_identity", None)
        action_secret = None
        action_ttl_seconds = 86400
        action_key_version = "1"
        if identity is not None and bool(getattr(identity, "enabled", False)):
            secret = os.environ.get(str(getattr(identity, "action_token_secret_env", "") or ""))
            action_secret = secret.encode("utf-8") if secret else None
            action_ttl_seconds = int(getattr(identity, "action_token_ttl_seconds", 86400) or 86400)
            action_key_version = str(getattr(identity, "action_token_key_version", "1") or "1")
        transport = transport_for_purpose("run_manager")
        if transport is None:
            return False
        result = push_run_manager_cards_once(
            state_dir,
            transport,
            receive_id=str(receive_id),
            receive_id_type=routing.receive_id_type_for(receive_role),
            action_secret=action_secret,
            action_ttl_seconds=action_ttl_seconds,
            action_key_version=action_key_version,
        )
        return bool(
            result.get("status_sent")
            or result.get("status_updated")
            or result.get("escalation_sent")
            or result.get("escalation_updated")
        )
    except Exception:
        return False


def _materialize_failure_candidates(
    *,
    event_log: Any,
    writer: Any,
    state_dir: Path,
) -> int:
    try:
        events = _read_events(state_dir, event_log=event_log)
        from zf.runtime.failure_to_eval import (
            materialize_failure_candidates_from_events,
            materialize_failure_closeout,
        )

        refs = materialize_failure_candidates_from_events(
            state_dir,
            events,
            source="runtime_tick",
        )
        if refs:
            closeout = materialize_failure_closeout(
                state_dir,
                output_root=state_dir / "failure-closeout",
                kinds=("backlog", "eval", "skill"),
                candidate_refs=refs,
            )
            writer.append(ZfEvent(
                type="failure.candidates.materialized",
                actor="zf-runtime",
                payload={
                    "schema_version": "failure-candidates.materialized.v1",
                    "count": len(refs),
                    "candidate_refs": [str(path) for path in refs],
                },
            ))
            writer.append(ZfEvent(
                type="failure.closeout.materialized",
                actor="zf-runtime",
                payload={
                    "schema_version": "failure-closeout.event.v1",
                    "manifest_ref": closeout.get("manifest_ref"),
                    "materialized_count": closeout.get("materialized_count", 0),
                    "candidate_count": closeout.get("candidate_count", 0),
                    "requested_kinds": closeout.get("requested_kinds", []),
                    "source": "runtime_tick",
                },
            ))
        return len(refs)
    except Exception as exc:
        try:
            writer.append(ZfEvent(
                type="failure.candidates.materialize_failed",
                actor="zf-runtime",
                payload={
                    "schema_version": "failure-candidates.materialize-failed.v1",
                    "reason": "failure_candidate_projection_failed",
                    "error": str(exc)[:400],
                },
            ))
        except Exception:
            pass
        return 0


def _run_supervisor(*, state_dir: Path, config: Any, project_root: Path) -> bool:
    try:
        from zf.runtime.supervisor_inspection import run_supervisor_inspection

        run_supervisor_inspection(
            state_dir,
            config=config,
            project_root=project_root,
            emit_attention_events=True,
        )
        return True
    except Exception:
        return False


def _emit_stall_recoveries(
    *,
    event_log: Any,
    writer: Any,
    state_dir: Path,
    config: Any,
) -> bool:
    try:
        from zf.runtime.event_window import read_runtime_events
        from zf.runtime.stall_detector import emit_stall_recoveries, stages_from_config

        stages = stages_from_config(config)
        if not stages:
            return False
        emit_stall_recoveries(
            read_runtime_events(event_log, state_dir),
            writer,
            stages=stages,
        )
        return True
    except Exception:
        return False


def _run_remediation_shadow(
    *,
    orchestrator: Any,
    event_log: Any,
    writer: Any,
    state_dir: Path,
) -> bool:
    try:
        from zf.runtime.event_window import read_runtime_events
        from zf.runtime.remediation_pipeline import (
            remediation_sm_shadow_enabled,
            run_remediation_sm_shadow,
        )
        from zf.runtime.repair_authorization import auto_repair_authorized

        if not remediation_sm_shadow_enabled():
            return False
        liveness = False
        checker = getattr(orchestrator, "_operator_channel_live", None)
        if callable(checker):
            liveness = bool(checker())
        run_remediation_sm_shadow(
            read_runtime_events(event_log, state_dir),
            writer,
            liveness=liveness,
            authorized=auto_repair_authorized(),
        )
        return True
    except Exception:
        return False


def _redrive_replan_adoptions(orchestrator: Any) -> bool:
    try:
        from zf.runtime.replan_adoption_redrive import redrive_owner_approved_adoptions

        redrive_owner_approved_adoptions(
            orchestrator.state_dir,
            project_root=orchestrator.project_root,
            writer=orchestrator.event_writer,
        )
        return True
    except Exception:
        return False


def _consume_self_repairs(
    *,
    orchestrator: Any,
    event_log: Any,
    writer: Any,
    state_dir: Path,
    config: Any,
) -> bool:
    try:
        from zf.runtime.event_window import read_runtime_events
        from zf.runtime.repair_authorization import (
            auto_repair_consumer_enabled,
            configured_self_repair_backend,
        )
        from zf.runtime.self_repair_runner import (
            dispatch_pending_self_repairs,
            emit_self_repair_closeouts,
        )

        if not auto_repair_consumer_enabled(config):
            return False
        events = read_runtime_events(event_log, state_dir)
        dispatched = dispatch_pending_self_repairs(
            events,
            writer,
            spawn=True,
            backend=configured_self_repair_backend(config),
        )
        closeouts = emit_self_repair_closeouts(events, writer)
        return bool(dispatched or closeouts)
    except Exception:
        return False


def _run_run_manager(
    *,
    orchestrator: Any,
    event_log: Any,
    writer: Any,
    state_dir: Path,
    config: Any,
    project_root: Path,
):
    try:
        from zf.runtime.run_manager import RunManagerTickResult, run_manager_tick

        return run_manager_tick(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            event_log=event_log,
            auto_execute=True,
            spawn_repairs=True,
            repair_backend=_configured_run_manager_backend(config),
        )
    except Exception as exc:
        try:
            writer.emit(
                "run.manager.tick.failed",
                actor="run-manager",
                payload={
                    "schema_version": "run-manager.tick.v1",
                    "reason": "run_manager_tick_exception",
                    "error": str(exc),
                },
            )
        except Exception:
            pass
        try:
            from zf.runtime.run_manager import RunManagerTickResult

            return RunManagerTickResult()
        except Exception:
            class _Empty:
                changed = False
                repairs_dispatched = 0

            return _Empty()


def _run_run_manager_watchdog(
    *,
    orchestrator: Any,
    event_log: Any,
    writer: Any,
    state_dir: Path,
    config: Any,
):
    try:
        from zf.runtime.run_manager_watchdog import (
            RunManagerWatchdogResult,
            run_manager_watchdog_tick,
        )

        return run_manager_watchdog_tick(
            state_dir=state_dir,
            writer=writer,
            config=config,
            event_log=event_log,
            resident_probe=_resident_probe(orchestrator, config),
        )
    except Exception:
        try:
            from zf.runtime.run_manager_watchdog import RunManagerWatchdogResult

            return RunManagerWatchdogResult()
        except Exception:
            class _Empty:
                changed = False

            return _Empty()


def _apply_run_manager_resident_restarts(
    *,
    orchestrator: Any,
    event_log: Any,
    writer: Any,
    state_dir: Path,
    config: Any,
    project_root: Path,
) -> bool:
    try:
        from zf.runtime.run_manager_resident import (
            apply_pending_resident_restart_requests,
        )

        coordinator = _spawn_coordinator(orchestrator)
        transport = getattr(orchestrator, "transport", None)
        if coordinator is None or transport is None:
            return False
        applied = apply_pending_resident_restart_requests(
            config=config,
            state_dir=state_dir,
            project_root=project_root,
            writer=writer,
            event_log=event_log,
            coordinator=coordinator,
            transport=transport,
            instructions_dir=state_dir / "instructions",
        )
        return applied > 0
    except Exception:
        return False


def _spawn_coordinator(orchestrator: Any) -> Any:
    getter = getattr(orchestrator, "_get_spawn_coordinator", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return getattr(orchestrator, "spawn_coordinator", None)


def _resident_probe(orchestrator: Any, config: Any):
    try:
        resident = getattr(
            getattr(getattr(config, "runtime", None), "run_manager", None),
            "resident_agent",
            None,
        )
        if not bool(getattr(resident, "enabled", False)):
            return None
        from zf.runtime.run_manager_resident import resident_run_manager_tmux_session

        target = (
            f"{resident_run_manager_tmux_session(config)}:"
            f"{getattr(resident, 'instance_id', 'run-manager') or 'run-manager'}"
        )
        transport = getattr(orchestrator, "transport", None)

        def _probe() -> dict[str, Any]:
            pane_display = getattr(transport, "pane_display", None)
            if callable(pane_display):
                result = pane_display(target)
                if isinstance(result, dict):
                    return result
            return {"ok": True, "target": target, "reason": "probe_unavailable"}

        return _probe
    except Exception:
        return None


def _consume_run_manager_source_repairs(
    *,
    event_log: Any,
    writer: Any,
    state_dir: Path,
    config: Any,
) -> int:
    try:
        source_repair = getattr(
            getattr(getattr(config, "runtime", None), "run_manager", None),
            "source_repair",
            None,
        )
        if not bool(getattr(source_repair, "enabled", False)):
            return 0
        from zf.runtime.event_window import read_runtime_events
        from zf.runtime.self_repair_runner import dispatch_pending_self_repairs

        backend = str(getattr(source_repair, "backend", "") or "").strip()
        if not backend:
            backend = _configured_run_manager_backend(config)
        return dispatch_pending_self_repairs(
            read_runtime_events(event_log, state_dir),
            writer,
            request_types=("run.manager.source_repair.dispatch_requested",),
            dispatch_actor="run-manager-watchdog",
            spawn=True,
            backend=backend,
        )
    except Exception:
        return 0


def _configured_run_manager_backend(config: Any) -> str:
    run_manager = getattr(getattr(config, "runtime", None), "run_manager", None)
    backend = str(getattr(run_manager, "backend", "") or "").strip()
    if backend:
        return backend
    from zf.runtime.repair_authorization import configured_self_repair_backend

    return configured_self_repair_backend(config)


def _read_events(state_dir: Path, *, event_log: Any = None) -> list[ZfEvent]:
    if event_log is not None:
        try:
            return list(event_log.read_all())
        except Exception:
            pass
    try:
        from zf.core.events.log import EventLog

        return EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        return []


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_open_stale_projection_event(events: list[ZfEvent], *, fingerprint: str) -> bool:
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("fingerprint") or "") != fingerprint:
            continue
        if event.type == "runtime.attention.resolved":
            return False
        if event.type == "supervisor.projection.stale":
            return True
    return False


__all__ = [
    "TickServiceIntervals",
    "TickServiceResult",
    "TickServiceState",
    "emit_stale_supervisor_projection_if_needed",
    "run_autoresearch_trigger_scan",
    "run_standard_tick_services",
]


def _emit_blocked_role_burn_if_needed(
    *,
    event_log,
    event_writer,
    state_dir,
    state: TickServiceState,
    intervals: TickServiceIntervals,
) -> bool:
    """blocked 角色烧钱看门狗(avbs-r5:dev-flow blocked_human 冷却期
    烧 30M token 零产出)。角色进入 blocked_* 后 agent.usage 仍累积超
    阈值 → cost.blocked_role_burn;按实例以事件去重 + 全局冷却。"""
    import time as _time

    from zf.core.events.model import ZfEvent
    from zf.runtime.event_window import read_runtime_events

    now_wall = _time.time()
    if now_wall - state.last_blocked_burn_emit_at < intervals.blocked_burn_cooldown_s:
        return False
    events = read_runtime_events(event_log, state_dir)
    blocked_since: dict[str, int] = {}
    burn_tokens: dict[str, int] = {}
    burned_reported: dict[str, int] = {}
    for idx, event in enumerate(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "worker.state.changed":
            instance = str(payload.get("instance_id") or "")
            if not instance:
                continue
            if str(payload.get("state") or "").startswith("blocked"):
                blocked_since.setdefault(instance, idx)
            else:
                blocked_since.pop(instance, None)
                burn_tokens.pop(instance, None)
        elif event.type == "agent.usage":
            instance = str(
                payload.get("instance_id")
                or str(event.actor or "").split(":")[-1]
                or ""
            )
            if instance in blocked_since:
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                burn_tokens[instance] = burn_tokens.get(instance, 0) + int(
                    usage.get("input_tokens") or 0
                ) + int(usage.get("output_tokens") or 0)
        elif event.type == "cost.blocked_role_burn":
            instance = str(payload.get("instance_id") or "")
            if instance:
                burned_reported[instance] = idx
    emitted = False
    for instance, since_idx in blocked_since.items():
        tokens = burn_tokens.get(instance, 0)
        if tokens < intervals.blocked_burn_tokens:
            continue
        if burned_reported.get(instance, -1) > since_idx:
            continue  # 本次 blocked 段已报过
        event_writer.append(ZfEvent(
            type="cost.blocked_role_burn",
            actor="zf-cli",
            payload={
                "instance_id": instance,
                "tokens_since_blocked": tokens,
                "threshold": intervals.blocked_burn_tokens,
                "reason": (
                    "role is in a blocked_* state but keeps consuming "
                    "tokens; frozen roles should not run agentic turns"
                ),
            },
        ))
        state.last_blocked_burn_emit_at = now_wall
        emitted = True
    return emitted


def _emit_cost_blackout_if_needed(
    *,
    event_log,
    event_writer,
    state_dir,
    state: TickServiceState,
    intervals: TickServiceIntervals,
) -> bool:
    """P0-8: dispatch 活跃但 usage 停更超过阈值 → cost.usage.blackout。

    判定用事件时间戳(非墙钟差),避免 run 暂停期误报;带冷却去重。
    """
    import time as _time
    from datetime import datetime

    from zf.core.events.model import ZfEvent
    from zf.runtime.event_window import read_runtime_events

    now_wall = _time.time()
    if now_wall - state.last_blackout_emit_at < intervals.cost_blackout_cooldown_s:
        return False
    events = read_runtime_events(event_log, state_dir)
    last_dispatch_ts = None
    last_usage_ts = None
    for event in events:
        if event.type in ("task.dispatched", "fanout.child.dispatched"):
            last_dispatch_ts = event.ts
        elif event.type == "agent.usage":
            last_usage_ts = event.ts

    def _epoch(ts: str | None) -> float | None:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            return None

    dispatch_epoch = _epoch(last_dispatch_ts)
    usage_epoch = _epoch(last_usage_ts)
    if dispatch_epoch is None:
        return False  # 本窗无派发,无成本可盲
    if (
        usage_epoch is None
        and now_wall - dispatch_epoch < intervals.cost_blackout_startup_grace_s
    ):
        return False  # 新 worker 启动早期 session/usage 文件可能尚未落盘
    stale_s = intervals.cost_blackout_stale_s
    if now_wall - dispatch_epoch > stale_s * 2:
        return False  # 派发本身已冷,run 空闲期不报
    usage_stale = (
        usage_epoch is None or now_wall - usage_epoch >= stale_s
    )
    if not usage_stale:
        return False
    state.last_blackout_emit_at = now_wall
    event_writer.append(ZfEvent(
        type="cost.usage.blackout",
        actor="zf-cli",
        payload={
            "last_dispatch_ts": last_dispatch_ts,
            "last_usage_ts": last_usage_ts,
            "stale_threshold_s": stale_s,
            "problem_class": "harness",
            "reason": (
                "dispatch active but agent.usage stopped updating; budget "
                "gate is deciding on a frozen total (D9: r5 ran 11h blind)"
            ),
        },
    ))
    return True
