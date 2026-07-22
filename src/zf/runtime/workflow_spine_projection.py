"""Shadow spine 只读投影(131-P0,E2)。

从 events.jsonl 派生三份 run 级解释投影,回答监工最常问的三个问题:
「这个 task 现在第几次 attempt、谁持有、终态是什么」(task_attempts)、
「这个 stage 转到第几轮了」(stage_spine)、「run 整体健康吗」
(workflow_health)。

硬前置(131-P0 验收条款,watcher O(n²) 教训):**禁止逐事件全量
read_all**——用 EventLog.read_from_offset 游标增量消费,游标与聚合态
一起持久化在投影文件内;游标缺失/损坏时才整体重建一次。只读派生,
不构成第二控制面;写入走原子写。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.event_problem_registry import spec_for_event
from zf.runtime.terminal_events import (
    is_task_attempt_terminal_event,
    task_attempt_terminal_state,
)

SPINE_SCHEMA_VERSION = "shadow-spine.v1"

_ATTEMPT_START_EVENTS = frozenset({
    "task.dispatched",
    "fanout.child.dispatched",
    "task.attempt.started",
})
_ATTEMPT_HEARTBEAT_EVENTS = frozenset({
    "task.attempt.heartbeat",
})
_ATTEMPT_ACTIVITY_EVENTS = frozenset({
    "agent.usage",
    "agent.text",
    "agent.tool.use",
    "worker.heartbeat",
    "worker.progress",
    "phase.progressed",
    "claude.hook.pre_tool_use",
    "claude.hook.post_tool_use",
    "codex.hook.pre_tool_use",
    "codex.hook.post_tool_use",
})
_RUN_MILESTONE_EVENTS = frozenset({
    "refactor.scan.requested", "refactor.scan.ready",
    "task_map.ready", "candidate.ready",
    "review.approved", "review.rejected",
    "verify.passed", "verify.failed",
    "module.parity.closed", "judge.passed", "judge.failed",
    "human.escalate", "zaofu.refactor.plan.blocked",
})

_HEALTH_COUNTER_EVENTS = frozenset({
    "human.escalate",
    "candidate.rework.quarantined",
    "runtime.watcher.lag_warning",
    "fanout.duplicate_suppressed",
    "rework.routing.scope_mismatch",
    "integration.failed",
    "review.rejected",
    "verify.failed",
})


def _payload(event: ZfEvent) -> dict:
    return event.payload if isinstance(event.payload, dict) else {}


def _event_task_id(event: ZfEvent) -> str:
    payload = _payload(event)
    return str(
        event.task_id
        or payload.get("task_id")
        or payload.get("upstream_task_id")
        or ""
    )


def _event_role(payload: dict[str, Any]) -> str:
    return str(
        payload.get("role")
        or payload.get("role_instance")
        or payload.get("assigned_to")
        or payload.get("worker_id")
        or ""
    )


def _attempt_key(task_id: str, event: ZfEvent, payload: dict[str, Any], ordinal: int) -> str:
    explicit = str(
        payload.get("attempt_key")
        or payload.get("attempt_id")
        or payload.get("lease_token")
        or payload.get("dispatch_id")
        or payload.get("run_id")
        or ""
    ).strip()
    if explicit:
        return explicit
    event_id = str(event.id or "").strip()
    if event_id:
        return f"{task_id}:{event_id}"
    return f"{task_id}:attempt-{ordinal}"


def _attempt_identity(payload: dict[str, Any]) -> set[str]:
    return {
        str(value).strip()
        for value in (
            payload.get("attempt_key"),
            payload.get("attempt_id"),
            payload.get("lease_token"),
            payload.get("dispatch_id"),
            payload.get("run_id"),
        )
        if str(value or "").strip()
    }


def _attempt_matches_payload(attempt: dict[str, Any], payload: dict[str, Any]) -> bool:
    identities = _attempt_identity(payload)
    attempt_identities = {
        str(value).strip()
        for value in (
            attempt.get("attempt_key"),
            attempt.get("lease_token"),
            attempt.get("dispatch_id"),
            attempt.get("run_id"),
        )
        if str(value or "").strip()
    }
    if identities and identities & attempt_identities:
        return True
    fanout_id = str(payload.get("fanout_id") or "")
    child_id = str(payload.get("child_id") or payload.get("child_run") or "")
    return bool(
        fanout_id
        and child_id
        and fanout_id == str(attempt.get("fanout_id") or "")
        and child_id == str(attempt.get("child_id") or "")
    )


def _matching_open_attempt(
    tasks: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    task_id = _event_task_id(event)
    entries: list[dict[str, Any]] = []
    if task_id and isinstance(tasks.get(task_id), dict):
        entries = [tasks[task_id]]
    elif event.actor:
        entries = [
            entry
            for entry in tasks.values()
            if isinstance(entry, dict)
            and any(
                isinstance(attempt, dict)
                and attempt.get("terminal") is None
                and str(attempt.get("role") or "") == event.actor
                for attempt in entry.get("attempts") or []
            )
        ]
        if len(entries) != 1:
            return None
    for entry in entries:
        open_attempts = [
            attempt
            for attempt in entry.get("attempts") or []
            if isinstance(attempt, dict) and attempt.get("terminal") is None
        ]
        for attempt in reversed(open_attempts):
            if _attempt_matches_payload(attempt, payload):
                return entry, attempt
        if _attempt_identity(payload) or (
            payload.get("fanout_id") and payload.get("child_id")
        ):
            continue
        if len(open_attempts) == 1:
            return entry, open_attempts[0]
    return None


def _supersede_open_attempts(
    entry: dict[str, Any],
    *,
    event: ZfEvent,
    next_attempt_key: str,
) -> None:
    for attempt in entry.get("attempts") or []:
        if not isinstance(attempt, dict) or attempt.get("terminal") is not None:
            continue
        if str(attempt.get("attempt_key") or "") == next_attempt_key:
            continue
        attempt["terminal"] = {
            "type": "task.attempt.superseded",
            "event_id": str(event.id or ""),
            "ts": event.ts,
            "reason": f"superseded by {next_attempt_key}",
        }
        attempt["state"] = "superseded"
        attempt["lease_state"] = "released"
        attempt["counted"] = False
        attempt["retryable"] = False


def _terminal_state(event_type: str) -> str:
    return task_attempt_terminal_state(event_type)


def _failure_signature(event: ZfEvent) -> str:
    spec = spec_for_event(event.type)
    if spec is not None and spec.failure_class:
        return spec.failure_class
    return event.type.replace(".", "_")


def _retryable_terminal(event: ZfEvent, payload: dict[str, Any]) -> bool:
    if event.type == "task.attempt.deadlettered":
        return False
    if _terminal_state(event.type) != "failed":
        return False
    if str(payload.get("retryable") or "").lower() in {"false", "0", "no"}:
        return False
    spec = spec_for_event(event.type)
    return not (spec is not None and spec.problem_class == "environment")


def _counted_terminal(
    entry: dict[str, Any],
    attempt: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
    superseded_fanouts: set[str],
) -> bool:
    if _terminal_state(event.type) == "succeeded":
        return True
    fanout_id = str(payload.get("fanout_id") or attempt.get("fanout_id") or "")
    if fanout_id and fanout_id in superseded_fanouts:
        return False
    for previous in entry.get("attempts") or []:
        if previous is attempt:
            break
        terminal = previous.get("terminal")
        if not isinstance(terminal, dict):
            continue
        if (
            fanout_id
            and str(previous.get("fanout_id") or "") == fanout_id
            and str(terminal.get("type") or "") == event.type
        ):
            return False
    return True


def _summarize_task_attempt_entry(entry: dict[str, Any]) -> None:
    attempts = entry.get("attempts")
    attempts = attempts if isinstance(attempts, list) else []
    entry["attempt_count"] = len(attempts)
    if attempts:
        latest = attempts[-1]
        entry["current_owner"] = str(latest.get("role") or "")
        entry["latest_attempt_key"] = str(latest.get("attempt_key") or "")
        entry["latest_state"] = str(latest.get("state") or "unknown")
        entry["lease_state"] = str(latest.get("lease_state") or "")
    else:
        entry["current_owner"] = ""
        entry["latest_attempt_key"] = ""
        entry["latest_state"] = "none"
        entry["lease_state"] = ""
    terminal = None
    for attempt in reversed(attempts):
        candidate = attempt.get("terminal")
        if isinstance(candidate, dict):
            terminal = candidate
            break
    entry["last_terminal"] = str(terminal.get("type") or "") if terminal else ""
    entry["open_attempts"] = sum(
        1 for attempt in attempts
        if isinstance(attempt, dict) and not isinstance(attempt.get("terminal"), dict)
    )
    entry["counted_failures"] = sum(
        1 for attempt in attempts
        if isinstance(attempt, dict)
        and attempt.get("counted") is not False
        and str(attempt.get("state") or "") in {"failed", "deadlettered"}
    )


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema_version") == SPINE_SCHEMA_VERSION:
            return data
    except (OSError, ValueError):
        pass
    return {"schema_version": SPINE_SCHEMA_VERSION, "cursor_offset": 0}


def read_spine_explain(state_dir: Path, *, task_id: str = "") -> dict[str, Any]:
    """四投影的只读解释视图(CLI `zf runs explain` 与 Web 共用,不刷新)。"""
    projections_dir = Path(state_dir) / "projections"
    out: dict[str, Any] = {}
    for key, name in (
        ("runs", "workflow_spine.json"),
        ("stages", "stage_spine.json"),
        ("health", "workflow_health.json"),
        ("tasks", "task_attempts.json"),
    ):
        try:
            data = json.loads((projections_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[key] = {"error": f"{name} unavailable"}
            continue
        if key == "runs":
            out[key] = data.get("runs") or {}
        elif key == "stages":
            out[key] = data.get("stages") or {}
        elif key == "health":
            out[key] = {
                "counters": data.get("counters") or {},
                "last_event_ts": data.get("last_event_ts") or "",
            }
        else:
            tasks = data.get("tasks") or {}
            if task_id:
                tasks = {task_id: tasks.get(task_id) or {}}
            out[key] = {
                tid: {
                    "attempt_count": entry.get("attempt_count", 0),
                    "current_owner": entry.get("current_owner", ""),
                    "last_terminal": entry.get("last_terminal", ""),
                    "latest_state": entry.get("latest_state", ""),
                    "lease_state": entry.get("lease_state", ""),
                    "latest_attempt_key": entry.get("latest_attempt_key", ""),
                }
                for tid, entry in tasks.items()
            }
    return out


def refresh_spine_projections(state_dir: Path, event_log) -> dict[str, Any]:
    """增量折叠新事件进三份投影;返回本轮统计(供 tick 结果观测)。"""
    projections_dir = Path(state_dir) / "projections"
    projections_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = projections_dir / "task_attempts.json"
    stages_path = projections_dir / "stage_spine.json"
    health_path = projections_dir / "workflow_health.json"
    runs_path = projections_dir / "workflow_spine.json"

    attempts = _load(attempts_path)
    stages = _load(stages_path)
    health = _load(health_path)
    runs = _load(runs_path)
    # 三份共用一个游标语义;以 attempts 的为准(三份同轮写出,天然一致)。
    offset = int(attempts.get("cursor_offset") or 0)
    events, new_offset = event_log.read_from_offset(offset)

    tasks: dict[str, Any] = attempts.setdefault("tasks", {})
    stage_map: dict[str, Any] = stages.setdefault("stages", {})
    counters: dict[str, int] = health.setdefault("counters", {})
    superseded_fanouts = set(str(item) for item in attempts.get("superseded_fanouts") or [])

    for event in events:
        payload = _payload(event)
        if event.type == "fanout.cancelled" and "supersede" in str(
            payload.get("reason") or ""
        ):
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)
        elif event.type == "fanout.child.stale_completion":
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)
        if event.type in _ATTEMPT_START_EVENTS:
            task_id = _event_task_id(event)
            if task_id:
                entry = tasks.setdefault(task_id, {"attempts": []})
                ordinal = len(entry["attempts"]) + 1
                attempt_key = _attempt_key(task_id, event, payload, ordinal)
                existing = next((
                    attempt for attempt in reversed(entry["attempts"])
                    if isinstance(attempt, dict)
                    and attempt.get("terminal") is None
                    and str(attempt.get("attempt_key") or "") == attempt_key
                ), None)
                if existing is not None:
                    existing["last_activity_ts"] = event.ts
                    existing["last_activity_event_type"] = event.type
                    continue
                _supersede_open_attempts(
                    entry,
                    event=event,
                    next_attempt_key=attempt_key,
                )
                entry["attempts"].append({
                    "schema_version": "task-attempt.v1",
                    "attempt_key": attempt_key,
                    "ordinal": ordinal,
                    "state": "running",
                    "source_event_id": str(event.id or ""),
                    "source_event_type": event.type,
                    "started_ts": event.ts,
                    "role": _event_role(payload),
                    "fanout_id": str(payload.get("fanout_id") or ""),
                    "child_id": str(payload.get("child_id") or ""),
                    "run_id": str(payload.get("run_id") or ""),
                    "dispatch_id": str(payload.get("dispatch_id") or ""),
                    "lease_token": str(payload.get("lease_token") or payload.get("dispatch_id") or ""),
                    "lease_state": "held",
                    "last_heartbeat_ts": "",
                    "last_activity_ts": event.ts,
                    "last_activity_event_type": event.type,
                    "failure_signature": "",
                    "counted": True,
                    "retryable": True,
                    "terminal": None,
                })
                entry["current_owner"] = entry["attempts"][-1]["role"]
        elif event.type in _ATTEMPT_HEARTBEAT_EVENTS | _ATTEMPT_ACTIVITY_EVENTS:
            matched = _matching_open_attempt(tasks, event, payload)
            if matched is not None:
                _entry, attempt = matched
                attempt["last_activity_ts"] = event.ts
                attempt["last_activity_event_type"] = event.type
                if event.type in _ATTEMPT_HEARTBEAT_EVENTS:
                    attempt["last_heartbeat_ts"] = event.ts
                attempt["state"] = "running"
                attempt["lease_state"] = "held"
        elif is_task_attempt_terminal_event(event.type):
            matched = _matching_open_attempt(tasks, event, payload)
            if matched is not None:
                entry, last = matched
                if last.get("terminal") is None:
                    state = _terminal_state(event.type)
                    counted = _counted_terminal(
                        entry,
                        last,
                        event,
                        payload,
                        superseded_fanouts,
                    )
                    retryable = _retryable_terminal(event, payload)
                    failure_signature = (
                        _failure_signature(event)
                        if state in {"failed", "deadlettered"} else ""
                    )
                    last["terminal"] = {
                        "type": event.type,
                        "event_id": str(event.id or ""),
                        "ts": event.ts,
                        "reason": str(payload.get("reason") or "")[:200],
                    }
                    last["state"] = state
                    last["lease_state"] = "released"
                    last["counted"] = counted
                    last["retryable"] = retryable
                    last["failure_signature"] = failure_signature
                    entry["last_terminal"] = event.type
        if event.type == "fanout.started":
            stage_id = str(payload.get("stage_id") or "")
            if stage_id:
                stage_entry = stage_map.setdefault(stage_id, {"rounds": 0})
                stage_entry["rounds"] += 1
                stage_entry["last_fanout_id"] = str(payload.get("fanout_id") or "")
                stage_entry["last_ts"] = event.ts
        elif payload.get("stage_id") and event.type in {
            "fanout.cancelled", "fanout.timed_out",
            "review.approved", "review.rejected",
            "verify.passed", "verify.failed",
            "judge.passed", "judge.failed",
        }:
            stage_entry = stage_map.setdefault(
                str(payload.get("stage_id")), {"rounds": 0},
            )
            stage_entry["last_status"] = event.type
            stage_entry["last_ts"] = event.ts
        if event.type in _HEALTH_COUNTER_EVENTS:
            counters[event.type] = counters.get(event.type, 0) + 1
        health["last_event_ts"] = event.ts
        # P0-4:run 级里程碑(按 pdd 聚合,回答"这个 run 走到哪了")
        if event.type in _RUN_MILESTONE_EVENTS:
            pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "")
            if pdd_id:
                run_entry = runs.setdefault("runs", {}).setdefault(pdd_id, {
                    "milestones": 0,
                })
                run_entry["milestones"] += 1
                run_entry["last_milestone"] = event.type
                run_entry["last_ts"] = event.ts
                run_entry["attention"] = event.type in {
                    "human.escalate", "review.rejected", "verify.failed",
                    "judge.failed", "zaofu.refactor.plan.blocked",
                }

    for doc in (attempts, stages, health, runs):
        doc["cursor_offset"] = new_offset
    # attempt 派生量 = attempts 列表长度。E5 决策级 counted_failures 由
    # attempt_ledger 在 rework 决策点(低频)按窗派生——不进本 30s 热路径
    # (E2 硬前置:禁全量 read_all)。
    attempts["superseded_fanouts"] = sorted(superseded_fanouts)
    for entry in tasks.values():
        if isinstance(entry, dict):
            _summarize_task_attempt_entry(entry)

    atomic_write_text(
        attempts_path, json.dumps(attempts, ensure_ascii=False, indent=1),
    )
    atomic_write_text(
        stages_path, json.dumps(stages, ensure_ascii=False, indent=1),
    )
    atomic_write_text(
        health_path, json.dumps(health, ensure_ascii=False, indent=1),
    )
    atomic_write_text(
        runs_path, json.dumps(runs, ensure_ascii=False, indent=1),
    )
    return {
        "events_folded": len(events),
        "cursor_offset": new_offset,
        "task_count": len(tasks),
        "stage_count": len(stage_map),
    }
