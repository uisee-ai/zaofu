"""Shared runtime tick services for ``zf start`` and diagnostic watchers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent


@dataclass
class TickServiceState:
    last_heartbeat_sweep_at: float = 0.0
    last_bug_scan_at: float = 0.0
    last_supervisor_inspection_at: float = 0.0


@dataclass(frozen=True)
class TickServiceIntervals:
    heartbeat_sweep_s: float = 60.0
    bug_scan_s: float = 300.0
    supervisor_inspection_s: float = 300.0
    stale_supervisor_projection_s: float = 300.0


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

    if now - state.last_heartbeat_sweep_at >= intervals.heartbeat_sweep_s:
        state.last_heartbeat_sweep_at = now
        _safe_housekeeping(orchestrator, "heartbeat_sweep", "_run_heartbeat_sweep")
        _safe_housekeeping(orchestrator, "dispatch_sweep", "_run_dispatch_sweep")
        heartbeat_sweep = True
        owner_visible_delivery = _deliver_owner_visible(state_dir, config)

    if now - state.last_bug_scan_at >= intervals.bug_scan_s:
        state.last_bug_scan_at = now
        bug_scan = _call_noarg(orchestrator, "_run_zaofu_bug_scan")

    if now - state.last_supervisor_inspection_at >= intervals.supervisor_inspection_s:
        state.last_supervisor_inspection_at = now
        stale_supervisor_projection = emit_stale_supervisor_projection_if_needed(
            state_dir=state_dir,
            writer=event_writer,
            max_stale_seconds=intervals.stale_supervisor_projection_s,
            event_log=event_log,
        )
        supervisor_inspection = _run_supervisor(
            state_dir=state_dir,
            config=config,
            project_root=project_root,
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
    )


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


def _deliver_run_manager_cards(state_dir: Path, config: Any) -> bool:
    try:
        from zf.integrations.feishu.run_manager_card import push_run_manager_cards_once
        from zf.integrations.feishu.transport import FeishuHttpTransport
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
        result = push_run_manager_cards_once(
            state_dir,
            FeishuHttpTransport(),
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
