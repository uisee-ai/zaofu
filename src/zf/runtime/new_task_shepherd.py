"""Kanban new-task 牧养与防死卡(B18,doc 93 §7.4)。

看板直建任务的两个保证:有归属(缺省 kanban-agent 牧养,可选 squad=
既有 affinity lane profile)、必触发(SLA 内无认领无派发 →
task.unclaimed.warning,绝不静默死卡 —— R25 停摆教训的任务粒度对应物)。
纯函数评估 + 调用方发事件;不新增 truth 文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

DEFAULT_UNCLAIMED_SLA_S = 600.0
SHEPHERD_ASSIGNEE = "kanban-agent"


@dataclass(frozen=True)
class ShepherdRoute:
    mode: str  # "shepherd" | "squad" | "hold"
    target: str
    reason: str


def route_new_task(task: Any, *, lane_profiles: set[str]) -> ShepherdRoute:
    """assignee 路由(doc 93 §7.4):squad 直派需最小 contract;不全降级
    回牧养补全 —— 不拒死。"""
    assignee = str(getattr(task, "assigned_to", "") or "").strip()
    contract = getattr(task, "contract", None)
    scope = list(getattr(contract, "scope", []) or [])
    verification = str(getattr(contract, "verification", "") or "")
    if assignee.startswith("squad:"):
        profile = assignee.split(":", 1)[1]
        if profile not in lane_profiles:
            return ShepherdRoute(
                "shepherd", SHEPHERD_ASSIGNEE,
                f"unknown squad profile {profile!r} → 牧养兜底",
            )
        if scope and verification:
            return ShepherdRoute("squad", profile, "contract 齐,直派 lane 组")
        return ShepherdRoute(
            "shepherd", SHEPHERD_ASSIGNEE,
            "contract 不全(scope/verification 缺)→ 牧养补全后转派",
        )
    return ShepherdRoute(
        "shepherd", assignee or SHEPHERD_ASSIGNEE, "缺省牧养",
    )


def unclaimed_warnings(
    tasks: list[Any],
    events: list[Any],
    *,
    now_ts: float,
    sla_seconds: float = DEFAULT_UNCLAIMED_SLA_S,
) -> list[dict[str, Any]]:
    """task.created 后 SLA 内无 dispatch/认领/终态 → warning payload 列表。

    幂等留给调用方(同 task 已有 warning 事件则不重发)。
    """
    # A workflow invoke bootstrap task is a kernel-owned fanout anchor, not a
    # worker-owned unit of work.  Its children carry the actual dispatches;
    # warning on the anchor creates a false SLA escalation for healthy runs.
    from zf.runtime.workflow_anchor import is_workflow_fanout_anchor_task

    ordinary_task_ids = {
        str(getattr(task, "id", "") or "")
        for task in tasks
        if not is_workflow_fanout_anchor_task(task)
    }
    created_at: dict[str, float] = {}
    claimed: set[str] = set()
    warned: set[str] = set()
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        task_id = str(getattr(event, "task_id", "") or "")
        if not task_id:
            continue
        if etype == "task.created":
            try:
                created_at[task_id] = datetime.fromisoformat(
                    str(getattr(event, "ts", "")),
                ).timestamp()
            except (ValueError, TypeError):
                continue
        elif etype == "task.unclaimed.warning":
            warned.add(task_id)
        elif etype in (
            "task.dispatched", "fanout.child.dispatched",
            "dev.build.done", "task.done.evidence",
        ):
            claimed.add(task_id)
    open_ids = {
        str(getattr(t, "id", "") or "")
        for t in tasks
        if str(getattr(t, "id", "") or "") in ordinary_task_ids
        and str(getattr(t, "status", "") or "") not in
        ("done", "cancelled", "superseded", "blocked")
    }
    out: list[dict[str, Any]] = []
    for task_id, ts in created_at.items():
        if task_id in claimed or task_id in warned or task_id not in open_ids:
            continue
        age = now_ts - ts
        if age >= sla_seconds:
            out.append({
                "task_id": task_id,
                "age_s": round(age, 1),
                "sla_s": sla_seconds,
                "note": "created 无认领无派发超 SLA — 终点只有三种:"
                        "被执行/显式关闭/升级提醒,不存在静默死卡"
                        "(doc 93 §7.4)",
            })
    return out
