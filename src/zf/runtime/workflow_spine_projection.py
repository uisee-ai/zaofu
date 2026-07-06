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

SPINE_SCHEMA_VERSION = "shadow-spine.v1"

_ATTEMPT_START_EVENTS = frozenset({
    "task.dispatched",
    "fanout.child.dispatched",
})
_ATTEMPT_TERMINAL_EVENTS = frozenset({
    "dev.build.done",
    "dev.failed",
    "dev.blocked",
    "fanout.child.completed",
    "fanout.child.failed",
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

    for event in events:
        payload = _payload(event)
        if event.type in _ATTEMPT_START_EVENTS:
            task_id = _event_task_id(event)
            if task_id:
                entry = tasks.setdefault(task_id, {"attempts": []})
                entry["attempts"].append({
                    "started_ts": event.ts,
                    "role": str(
                        payload.get("role")
                        or payload.get("role_instance")
                        or payload.get("assigned_to")
                        or ""
                    ),
                    "fanout_id": str(payload.get("fanout_id") or ""),
                    "child_id": str(payload.get("child_id") or ""),
                    "terminal": None,
                })
                entry["current_owner"] = entry["attempts"][-1]["role"]
        elif event.type in _ATTEMPT_TERMINAL_EVENTS:
            task_id = _event_task_id(event)
            entry = tasks.get(task_id)
            if entry and entry.get("attempts"):
                last = entry["attempts"][-1]
                if last.get("terminal") is None:
                    last["terminal"] = {
                        "type": event.type,
                        "ts": event.ts,
                        "reason": str(payload.get("reason") or "")[:200],
                    }
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
    for entry in tasks.values():
        entry["attempt_count"] = len(entry.get("attempts") or [])

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
