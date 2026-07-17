"""恢复案卷(recovery case file)——给 RM agent 裁决用的机械对账投影。

07-17 UISSE 实弹定案:RM resident 醒着(reflect 正常)却裁决不出
"child 已死 + kanban in_progress + worker idle"这类矛盾——不是不会判,
是简报里没有对账视图(首轮 prompted 14 次仅 2 次裁决同因)。

案卷 = 纯机械三表对账(kanban × fanout child 账 × instance 态),
零 LLM、可重建投影。**裁决 100% 归 agent**:案卷只陈述矛盾事实,
选不选动词/选哪个/何时选,由 RM resident 决定;执行经受控动作,
内核前置条件只拒绝物理不安全的执行,不替 agent 选方案。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent

CASE_FILE_REL_PATH = "projections/recovery_case_file.json"

_CHILD_TERMINAL = frozenset({"fanout.child.completed", "fanout.child.failed"})
_FANOUT_TERMINAL = frozenset({
    "fanout.aggregate.completed", "fanout.timed_out", "fanout.cancelled",
})
_STARVED_GRACE_S = 600.0
_QUEUE_GRACE_S = 600.0


def _epoch(event: ZfEvent) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(event.ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def build_case_file(
    events: Iterable[ZfEvent],
    *,
    tasks: Iterable[Mapping[str, Any]],
    instance_states: Mapping[str, str],
    now_epoch: float,
    config: Any = None,
    stillness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """三表对账出矛盾清单。``tasks`` 来自 kanban 投影,
    ``instance_states`` 来自 role_sessions(instance → busy/idle/...)。"""

    events = list(events)
    child_last: dict[str, tuple[float, str, str]] = {}
    fanout_terminal: set[str] = set()
    queued: dict[str, dict[str, float]] = {}
    dispatched_children: dict[str, set[str]] = {}
    open_escalations: list[dict[str, Any]] = []
    acked: set[str] = set()
    last_budget: dict[str, Any] = {}
    quiescent = False
    last_requeue: dict[str, float] = {}

    for event in events:
        etype = event.type
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or "")
        task_id = str(payload.get("task_id") or event.task_id or "")
        if etype == "fanout.child.queued" and fanout_id and child_id:
            queued.setdefault(fanout_id, {})[child_id] = _epoch(event)
        elif etype == "fanout.child.dispatched":
            if fanout_id and child_id:
                dispatched_children.setdefault(fanout_id, set()).add(child_id)
                queued.get(fanout_id, {}).pop(child_id, None)
            if task_id:
                child_last[task_id] = (_epoch(event), "inflight", child_id)
        elif etype in _CHILD_TERMINAL:
            if fanout_id and child_id:
                dispatched_children.get(fanout_id, set()).discard(child_id)
            state = "completed" if etype.endswith("completed") else "failed"
            if task_id:
                child_last[task_id] = (_epoch(event), state, child_id)
        elif etype in _FANOUT_TERMINAL and fanout_id:
            fanout_terminal.add(fanout_id)
            queued.pop(fanout_id, None)
        elif etype == "human.escalate":
            open_escalations.append({
                "event_id": event.id,
                "token": str(payload.get("decision_token") or "") or None,
                "reason": str(payload.get("reason") or "")[:120],
            })
        elif etype == "human.escalation.acknowledged":
            acked.add(str(payload.get("decision_token") or ""))
        elif etype == "cost.budget.exceeded":
            last_budget = {
                "budget_usd": payload.get("budget_usd"),
                "current_usd": payload.get("current_usd"),
                "at_epoch": _epoch(event),
            }
        elif etype == "run.goal.quiescent.entered":
            quiescent = True
        elif etype == "run.goal.quiescent.exited":
            quiescent = False
        elif etype == "task.requeued" and task_id:
            last_requeue[task_id] = _epoch(event)

    open_escalations = [
        e for e in open_escalations if not (e["token"] and e["token"] in acked)
    ]

    contradictions: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        tid = str(task.get("id") or "")
        status = str(task.get("status") or "")
        assignee = str(task.get("assigned_to") or "")
        worker_state = str(instance_states.get(assignee, "") or "")
        last = child_last.get(tid)
        # ★ wip_without_carrier:任务号称进行中,承接它的 child 已终局,
        #   worker 也不忙——两头脱节,谁都不会推进(07-17 UISSE 实弹)
        if (
            status == "in_progress"
            and last is not None
            and last[1] in ("failed", "completed")
            and worker_state not in ("busy", "active")
        ):
            contradictions.append({
                "kind": "wip_without_carrier",
                "task": tid,
                "evidence": {
                    "kanban": status,
                    "child": f"{last[1]}:{last[2]}",
                    "worker": f"{assignee}:{worker_state or 'unknown'}",
                },
            })
        # ready_but_starved:可派而久不派
        if status in ("backlog", "ready"):
            since = last_requeue.get(tid, 0.0)
            if since and now_epoch - since > _STARVED_GRACE_S and worker_state in ("idle", ""):
                contradictions.append({
                    "kind": "ready_but_starved",
                    "task": tid,
                    "evidence": {
                        "idle_for_s": round(now_epoch - since, 1),
                        "worker": f"{assignee}:{worker_state or 'unknown'}",
                    },
                })
    # queue_stuck:fanout 未终局、有排队 child、却无在飞 child
    for fanout_id, members in queued.items():
        if fanout_id in fanout_terminal or not members:
            continue
        if dispatched_children.get(fanout_id):
            continue
        oldest = min(members.values())
        if now_epoch - oldest > _QUEUE_GRACE_S:
            contradictions.append({
                "kind": "queue_stuck",
                "fanout": fanout_id,
                "evidence": {
                    "queued": sorted(members),
                    "stuck_for_s": round(now_epoch - oldest, 1),
                },
            })

    budget_cfg = getattr(config, "global_budget_usd", None) if config else None
    return {
        "schema_version": "recovery-case-file.v1",
        "generated_at_epoch": now_epoch,
        "stillness": dict(stillness or {}),
        "contradictions": contradictions,
        "current_state": {
            "budget_config_usd": budget_cfg,
            "last_budget_exceeded": last_budget or None,
            "budget_stale": bool(
                last_budget and budget_cfg
                and float(last_budget.get("budget_usd") or 0) < float(budget_cfg)
            ),
            "open_escalations": open_escalations,
            "quiescent": quiescent,
        },
    }


def write_case_file(state_dir: Path, case_file: Mapping[str, Any]) -> Path:
    path = Path(state_dir) / CASE_FILE_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case_file, ensure_ascii=False, indent=1))
    return path


__all__ = [
    "CASE_FILE_REL_PATH",
    "build_case_file",
    "write_case_file",
]
