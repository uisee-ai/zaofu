"""Deterministic health checks for Run Manager itself.

The resident Run Manager can supervise workflow progress, but it must not be
its own only safety net. This module is deliberately small: it observes events
and projections, emits health/restart/source-repair requests, and never mutates
task truth or restarts a workflow directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter

RUN_MANAGER_UNHEALTHY = "run.manager.unhealthy"
RUN_MANAGER_RESIDENT_RESTART_REQUESTED = "run.manager.resident.restart_requested"
RUN_MANAGER_SOURCE_REPAIR_DISPATCH_REQUESTED = "run.manager.source_repair.dispatch_requested"
RUN_MANAGER_TICK_STARTED = "run.manager.tick.started"
RUN_MANAGER_TICK_COMPLETED = "run.manager.tick.completed"
RUN_MANAGER_TICK_FAILED = "run.manager.tick.failed"


@dataclass(frozen=True)
class RunManagerWatchdogResult:
    unhealthy_emitted: int = 0
    resident_restart_requested: int = 0
    source_repair_requested: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.unhealthy_emitted
            or self.resident_restart_requested
            or self.source_repair_requested
        )


def run_manager_watchdog_tick(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: Any,
    event_log: EventLog | None = None,
    now: datetime | None = None,
    max_tick_seconds: int = 300,
    max_projection_stale_seconds: int = 600,
    tick_failure_threshold: int = 3,
    resident_probe: Callable[[], dict[str, Any]] | None = None,
) -> RunManagerWatchdogResult:
    state_dir = Path(state_dir)
    now = now or datetime.now(timezone.utc)
    events = _read_events(state_dir, event_log=event_log)
    emitted_unhealthy = 0
    emitted_restart = 0
    emitted_source_repair = 0

    for reason, detail in _health_findings(
        state_dir=state_dir,
        events=events,
        now=now,
        max_tick_seconds=max_tick_seconds,
        max_projection_stale_seconds=max_projection_stale_seconds,
        tick_failure_threshold=tick_failure_threshold,
        resident_probe=resident_probe,
    ):
        fingerprint = f"run-manager:{reason}:{detail.get('fingerprint_hint', '')}"
        if _fingerprint_seen(events, fingerprint):
            continue
        unhealthy = writer.emit(
            RUN_MANAGER_UNHEALTHY,
            actor="run-manager-watchdog",
            payload={
                "schema_version": "run-manager.watchdog.v1",
                "fingerprint": fingerprint,
                "reason": reason,
                "detail": detail,
                "state_dir": str(state_dir),
                "severity": _severity_for_reason(reason),
                "source": "watchdog",
                "handled_by": "watchdog",
            },
        )
        emitted_unhealthy += 1
        if reason in {"resident_pane_dead", "resident_pane_unhealthy"}:
            writer.emit(
                RUN_MANAGER_RESIDENT_RESTART_REQUESTED,
                actor="run-manager-watchdog",
                causation_id=unhealthy.id,
                payload={
                    "schema_version": "run-manager.resident-restart.v1",
                    "fingerprint": fingerprint,
                    "reason": reason,
                    "state_dir": str(state_dir),
                    "session_mode": _resident_session_mode(config),
                    "tmux_session": _resident_tmux_session(config),
                    "instance_id": _resident_instance_id(config),
                    "restart_scope": "resident_only",
                    "first_tick_mode": "observe_only",
                },
            )
            emitted_restart += 1
        if reason in {
            "tick_failure_threshold",
            "projection_unreadable",
            "projection_stale",
        } and _source_repair_enabled(config):
            writer.emit(
                RUN_MANAGER_SOURCE_REPAIR_DISPATCH_REQUESTED,
                actor="run-manager-watchdog",
                causation_id=unhealthy.id,
                payload=_source_repair_payload(
                    config,
                    fingerprint=fingerprint,
                    reason=reason,
                    state_dir=state_dir,
                ),
            )
            emitted_source_repair += 1

    return RunManagerWatchdogResult(
        unhealthy_emitted=emitted_unhealthy,
        resident_restart_requested=emitted_restart,
        source_repair_requested=emitted_source_repair,
    )


def _health_findings(
    *,
    state_dir: Path,
    events: list[ZfEvent],
    now: datetime,
    max_tick_seconds: int,
    max_projection_stale_seconds: int,
    tick_failure_threshold: int,
    resident_probe: Callable[[], dict[str, Any]] | None,
) -> list[tuple[str, dict[str, Any]]]:
    findings: list[tuple[str, dict[str, Any]]] = []
    latest_started = _latest(events, RUN_MANAGER_TICK_STARTED)
    latest_completed = _latest(events, RUN_MANAGER_TICK_COMPLETED)
    if latest_started is not None and _event_after(latest_started, latest_completed):
        ts = _parse_ts(latest_started.ts)
        if ts is not None:
            age = int((now - ts).total_seconds())
            if age > max_tick_seconds:
                findings.append(("tick_started_timeout", {
                    "event_id": latest_started.id,
                    "age_seconds": age,
                    "max_tick_seconds": max_tick_seconds,
                    "fingerprint_hint": latest_started.id,
                }))

    failures = _consecutive_tail(events, RUN_MANAGER_TICK_FAILED)
    if len(failures) >= tick_failure_threshold:
        findings.append(("tick_failure_threshold", {
            "count": len(failures),
            "threshold": tick_failure_threshold,
            "last_event_id": failures[-1].id,
            "fingerprint_hint": failures[-1].id,
        }))

    projection_path = state_dir / "projections" / "run_manager.json"
    if projection_path.exists():
        try:
            json.loads(projection_path.read_text(encoding="utf-8"))
        except Exception as exc:
            findings.append(("projection_unreadable", {
                "path": str(projection_path),
                "error": str(exc),
                "fingerprint_hint": str(projection_path),
            }))
        else:
            mtime = datetime.fromtimestamp(
                projection_path.stat().st_mtime,
                tz=timezone.utc,
            )
            age = int((now - mtime).total_seconds())
            if age > max_projection_stale_seconds:
                findings.append(("projection_stale", {
                    "path": str(projection_path),
                    "age_seconds": age,
                    "max_stale_seconds": max_projection_stale_seconds,
                    "fingerprint_hint": str(projection_path),
                }))

    if resident_probe is not None:
        try:
            probe = resident_probe()
        except Exception as exc:
            probe = {"ok": False, "reason": str(exc)}
        if not bool(probe.get("ok", False)):
            findings.append(("resident_pane_dead", {
                "probe": probe,
                "fingerprint_hint": str(probe.get("target") or probe.get("reason") or "resident"),
            }))
    return findings


def _source_repair_payload(
    config: Any,
    *,
    fingerprint: str,
    reason: str,
    state_dir: Path,
) -> dict[str, Any]:
    source_repair = _source_repair_config(config)
    scope = list(getattr(source_repair, "allow_paths", []) or ["src/zf/**", "tests/**"])
    return {
        "schema_version": "run-manager.source-repair-request.v1",
        "fingerprint": fingerprint,
        "attempt": 0,
        "candidate_id": "run-manager-watchdog",
        "candidate_path": "projections/run_manager.json",
        "reason": reason,
        "source": "run_manager_watchdog",
        "source_repair": {
            "enabled": True,
            "backend": str(getattr(source_repair, "backend", "") or ""),
            "mode": str(getattr(source_repair, "mode", "isolated_worktree") or "isolated_worktree"),
            "apply_policy": str(getattr(source_repair, "apply_policy", "proposal_only") or "proposal_only"),
            "restart_policy": str(
                getattr(source_repair, "restart_policy", "never_during_active_run")
                or "never_during_active_run"
            ),
            "restart_boundary": str(
                getattr(
                    source_repair,
                    "restart_boundary",
                    "terminal_or_operator_approved_checkpoint",
                )
                or "terminal_or_operator_approved_checkpoint"
            ),
            "replay_before_restart": bool(getattr(source_repair, "replay_before_restart", True)),
            "state_dir": str(state_dir),
        },
        "repair_task_payload": {
            "title": f"Repair Run Manager watchdog finding: {reason}",
            "contract": {
                "schema_version": "task-contract.v1",
                "phase": "zaofu_self_repair",
                "behavior": f"Run Manager watchdog detected {reason}",
                "verification": "PYTEST_ADDOPTS=--no-cov uv run pytest tests/test_run_manager.py tests/test_run_manager_watchdog.py tests/test_tick_services.py -q",
                "verification_tiers": ["static", "runtime"],
                "scope": scope,
                "acceptance": "focused verification passes and runtime truth is not edited directly",
                "owner_role": "run-manager-repair-worker",
                "complexity": "complex",
                "evidence_contract": {
                    "source": "run_manager_watchdog",
                    "fingerprint": fingerprint,
                    "state_dir": str(state_dir),
                },
            },
        },
    }


def _read_events(state_dir: Path, *, event_log: EventLog | None) -> list[ZfEvent]:
    if event_log is not None:
        try:
            return list(event_log.read_all())
        except Exception:
            pass
    try:
        return EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        return []


def _latest(events: list[ZfEvent], event_type: str) -> ZfEvent | None:
    for event in reversed(events):
        if event.type == event_type:
            return event
    return None


def _event_after(left: ZfEvent, right: ZfEvent | None) -> bool:
    if right is None:
        return True
    left_ts = _parse_ts(left.ts)
    right_ts = _parse_ts(right.ts)
    if left_ts is None or right_ts is None:
        return True
    return left_ts > right_ts


def _consecutive_tail(events: list[ZfEvent], event_type: str) -> list[ZfEvent]:
    out: list[ZfEvent] = []
    for event in reversed(events):
        if event.type == event_type:
            out.append(event)
            continue
        if event.type in {RUN_MANAGER_TICK_COMPLETED, RUN_MANAGER_TICK_STARTED}:
            break
    return list(reversed(out))


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


def _fingerprint_seen(events: list[ZfEvent], fingerprint: str) -> bool:
    for event in reversed(events[-200:]):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("fingerprint") or "") != fingerprint:
            continue
        if event.type in {
            RUN_MANAGER_UNHEALTHY,
            RUN_MANAGER_RESIDENT_RESTART_REQUESTED,
            RUN_MANAGER_SOURCE_REPAIR_DISPATCH_REQUESTED,
        }:
            return True
    return False


def _severity_for_reason(reason: str) -> str:
    if reason in {"tick_failure_threshold", "projection_unreadable"}:
        return "high"
    return "medium"


def _source_repair_config(config: Any) -> Any:
    return getattr(getattr(getattr(config, "runtime", None), "run_manager", None), "source_repair", None)


def _source_repair_enabled(config: Any) -> bool:
    return bool(getattr(_source_repair_config(config), "enabled", False))


def _resident_session_mode(config: Any) -> str:
    resident = getattr(getattr(getattr(config, "runtime", None), "run_manager", None), "resident_agent", None)
    return str(getattr(resident, "session_mode", "shared") or "shared")


def _resident_instance_id(config: Any) -> str:
    resident = getattr(getattr(getattr(config, "runtime", None), "run_manager", None), "resident_agent", None)
    return str(getattr(resident, "instance_id", "run-manager") or "run-manager")


def _resident_tmux_session(config: Any) -> str:
    try:
        from zf.runtime.run_manager_resident import resident_run_manager_tmux_session

        return resident_run_manager_tmux_session(config)
    except Exception:
        return ""


__all__ = [
    "RUN_MANAGER_RESIDENT_RESTART_REQUESTED",
    "RUN_MANAGER_SOURCE_REPAIR_DISPATCH_REQUESTED",
    "RUN_MANAGER_UNHEALTHY",
    "RunManagerWatchdogResult",
    "run_manager_watchdog_tick",
]
