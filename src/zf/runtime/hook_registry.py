"""Read-only hook registry projection.

The registry describes hook surfaces that ZaoFu knows about. It is deliberately
not a second control plane: enabled/configured state is derived from zf.yaml,
rendered hook files, wake-pattern wiring, and append-only events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.log import EventLog
from zf.runtime.codex_hooks import CODEX_HOOK_EVENTS, codex_hook_hash
from zf.runtime.wake_patterns import compute_effective_wake_patterns, rate_limits_for_config


_CLAUDE_HOOK_RECV_EVENTS: tuple[tuple[str, str, bool], ...] = (
    ("Stop", "provider.stop.check", True),
    ("Stop", "orchestrator.round.complete", False),
    ("PreCompact", "worker.context.precompact", False),
)

_TASK_LIFECYCLE_PHASES: tuple[str, ...] = (
    "after_create",
    "after_start",
    "after_finish",
    "after_archive",
)

_HOOK_FAILURE_EVENTS = frozenset({
    "hook.write_failed",
    "hook.orphan_event",
    "event.malformed",
})


def project_hook_registry(
    state_dir: Path,
    *,
    config: Any | None = None,
    project_root: Path | None = None,
    events: Iterable[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an effective read-only hook registry for one project."""

    now = now or datetime.now(timezone.utc)
    state_dir = Path(state_dir)
    project_root = Path(project_root or state_dir.parent)
    event_list = list(events) if events is not None else _read_events(state_dir)
    last_by_type = _last_event_by_type(event_list)
    hooks: list[dict[str, Any]] = []

    codex_settings = project_root / ".codex" / "hooks.json"
    codex_status = "configured" if codex_settings.exists() else "renderable"
    for engine_event, zf_event in CODEX_HOOK_EVENTS:
        row = _hook_row(
            hook_id=f"codex.{zf_event.rsplit('.', 1)[-1]}",
            source="codex_hooks",
            provider="codex",
            event_type=zf_event,
            blocking=(engine_event == "Stop"),
            status=codex_status,
            last_by_type=last_by_type,
        )
        row.update({
            "engine_event": engine_event,
            "settings_path": str(codex_settings),
            "trust_hash": codex_hook_hash(state_dir, engine_event, zf_event),
            "failure_mode": "audit_event_only",
        })
        hooks.append(row)

    claude_settings = state_dir / "hooks" / "settings.json"
    claude_status = "configured" if claude_settings.exists() else "renderable"
    for engine_event, zf_event, blocking in _CLAUDE_HOOK_RECV_EVENTS:
        row = _hook_row(
            hook_id=f"hook-recv.{zf_event}",
            source="hook_recv",
            provider="claude",
            event_type=zf_event,
            blocking=blocking,
            status=claude_status,
            last_by_type=last_by_type,
        )
        row.update({
            "engine_event": engine_event,
            "settings_path": str(claude_settings),
            "failure_mode": "audit_event_only",
        })
        hooks.append(row)

    effective_wakes = compute_effective_wake_patterns(config) if config is not None else set()
    limits = rate_limits_for_config(config) if config is not None else {}
    for event_type in sorted(_hook_related_wake_events(effective_wakes)):
        row = _hook_row(
            hook_id=f"wake.{event_type}",
            source="wake_patterns",
            provider="kernel",
            event_type=event_type,
            blocking=False,
            status="wired",
            last_by_type=last_by_type,
        )
        if event_type in limits:
            row["rate_limit_per_minute"] = limits[event_type]
        hooks.append(row)

    for phase in _TASK_LIFECYCLE_PHASES:
        row = _hook_row(
            hook_id=f"task-lifecycle.{phase}",
            source="task_lifecycle_hooks",
            provider="kernel",
            event_type=f"task.lifecycle.{phase}",
            blocking=False,
            status="experimental_unwired",
            last_by_type=last_by_type,
        )
        row.update({
            "phase": phase,
            "enabled": False,
            "reason": (
                "task_lifecycle_hooks.py exists, but workflow.task_hooks is not "
                "loaded by the current config schema and no runtime caller invokes it"
            ),
            "failure_mode": "audit_event_only_when_wired",
        })
        hooks.append(row)

    failure_events = [
        _event_summary(event) for event in event_list if getattr(event, "type", "") in _HOOK_FAILURE_EVENTS
    ]
    return {
        "schema_version": "hook-registry.v1",
        "generated_at": now.isoformat(),
        "project_root": str(project_root),
        "state_dir": str(state_dir),
        "summary": _summary(hooks, failure_events),
        "hooks": hooks,
        "recent_failures": failure_events[-10:],
    }


def _hook_related_wake_events(events: set[str]) -> set[str]:
    result: set[str] = set()
    for event_type in events:
        if (
            event_type.startswith("codex.hook.")
            or event_type.startswith("hook.")
            or event_type in {"provider.stop.check", "worker.context.precompact"}
        ):
            result.add(event_type)
    return result


def _hook_row(
    *,
    hook_id: str,
    source: str,
    provider: str,
    event_type: str,
    blocking: bool,
    status: str,
    last_by_type: dict[str, Any],
) -> dict[str, Any]:
    last = last_by_type.get(event_type)
    failure = last_by_type.get("hook.write_failed")
    return {
        "id": hook_id,
        "source": source,
        "provider": provider,
        "event_type": event_type,
        "blocking": blocking,
        "status": status,
        "enabled": status in {"configured", "wired"},
        "last_event_id": getattr(last, "id", "") if last is not None else "",
        "last_event_ts": getattr(last, "ts", "") if last is not None else "",
        "last_error": _extract_error(failure) if failure is not None else "",
    }


def _summary(hooks: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "hooks": len(hooks),
        "configured": sum(1 for row in hooks if row.get("status") == "configured"),
        "renderable": sum(1 for row in hooks if row.get("status") == "renderable"),
        "wired": sum(1 for row in hooks if row.get("status") == "wired"),
        "experimental_unwired": sum(
            1 for row in hooks if row.get("status") == "experimental_unwired"
        ),
        "blocking": sum(1 for row in hooks if row.get("blocking")),
        "recent_failures": len(failures[-10:]),
        "failure_mode": "audit_event_only",
    }


def _read_events(state_dir: Path) -> list[Any]:
    try:
        return EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        return []


def _last_event_by_type(events: Iterable[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for event in events:
        result[getattr(event, "type", "")] = event
    return result


def _event_summary(event: Any) -> dict[str, Any]:
    return {
        "id": getattr(event, "id", ""),
        "type": getattr(event, "type", ""),
        "task_id": getattr(event, "task_id", ""),
        "ts": getattr(event, "ts", ""),
        "error": _extract_error(event),
    }


def _extract_error(event: Any) -> str:
    payload = getattr(event, "payload", {}) or {}
    for key in ("error", "reason", "stderr", "message"):
        value = payload.get(key)
        if value:
            return str(value)[:500]
    return ""
