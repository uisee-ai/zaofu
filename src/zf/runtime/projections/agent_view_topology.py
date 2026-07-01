"""Agent View parent-child run 拓扑投影(X17,纯函数)。

补 Kanban 只见任务列、不见"worker 是否真的在做事"的缺口:
workflow run(trace)→ fanout → children 树,每子带 status/
last_activity/active_task。**纯投影**:events 进、dict 出,可丢弃
可重建;落 projections 模块,不回流 orchestrator 三文件(K1 边界)。
"""

from __future__ import annotations

from typing import Any


def build_agent_topology(events: list[Any]) -> dict[str, Any]:
    runs: dict[str, dict] = {}
    activity: dict[str, str] = {}
    tasks: dict[str, str] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", None) or {}
        actor = str(getattr(event, "actor", "") or "")
        ts = str(getattr(event, "ts", "") or "")
        if etype == "fanout.started":
            trace = str(payload.get("trace_id") or "")
            fanout_id = str(payload.get("fanout_id") or "")
            run = runs.setdefault(trace or fanout_id, {
                "trace_id": trace, "fanouts": {},
            })
            run["fanouts"][fanout_id] = {
                "stage_id": str(payload.get("stage_id") or ""),
                "children": {
                    str(c.get("child_id") or ""): {
                        "role_instance": str(c.get("role_instance") or ""),
                        "status": "expected",
                    }
                    for c in payload.get("expected_children") or []
                    if isinstance(c, dict)
                },
            }
        elif etype in ("fanout.child.dispatched", "fanout.child.completed",
                       "fanout.child.failed"):
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or "")
            status = etype.rsplit(".", 1)[-1]
            for run in runs.values():
                child = run["fanouts"].get(fanout_id, {}).get(
                    "children", {},
                ).get(child_id)
                if child is not None:
                    child["status"] = status
                    child["last_ts"] = ts
        elif etype in ("worker.heartbeat", "agent.usage", "phase.progressed"):
            if actor:
                activity[actor] = ts
        elif etype == "task.dispatched":
            assignee = str(payload.get("assignee") or "")
            if assignee and getattr(event, "task_id", None):
                tasks[assignee] = str(event.task_id)

    for run in runs.values():
        for fanout in run["fanouts"].values():
            for child in fanout["children"].values():
                inst = child.get("role_instance", "")
                if inst in activity:
                    child["last_activity_ts"] = activity[inst]
                if inst in tasks:
                    child["active_task"] = tasks[inst]
    return {"schema_version": "agent-view-topology.v1", "runs": runs}
