"""loop-view.v1 投影 — Loop 页 v2 的单一数据源(doc130 第二步)。

在 shadow-spine 三投影(E2)与事件流之上,派生 Loop 页 v2 需要的全部只读
数据:阶段链(从 stage_id 发现,零 flow 硬编码)、task attempt 行、业务环
聚合(README 环注册表:形状/闭合弧三态/成员/三态记账)、completion promise
求值(131 §3.3,config 契约优先)、故障族汇总(loop.v1 域)与伴生环。

2026-07-04~06 五 run dry-run 换来的投影教训固化为本模块行为(测试同名断言):
泵事件不算活动、传输回执与语义完成去重、孤儿完成保留、superseded open
attempt 不计数、无事件环族零态缺席。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

SCHEMA_VERSION = "loop-view.v1"

PUMP_TYPES = frozenset({
    "orchestrator.round.complete",
    "run.manager.tick.started", "run.manager.tick.completed",
    "run.manager.agent.observation",
    "hook.orphan_event", "runtime.snapshot.recorded",
    "provider.stop.check", "provider.permission.snapshot.recorded",
    "worker.heartbeat",
})

_TRANSPORT_COMPLETION = "fanout.child.completed"

GENERIC_PROMISE_CHAIN = (
    "plan.approved", "task_map.ready", "verify.child.completed",
    "judge.passed", "run.completed",
)
_PROMISE_ALIASES = {"verify.child.completed": ("test.passed", "verify.passed")}

# 业务环注册表(README §Main Business Loops)。shape/closure 是结构常量,
# 在场性与全部数字来自数据;此表只登记"环长什么样、成员怎么认"。
LOOP_REGISTRY: dict[str, dict[str, Any]] = {
    "delivery": {
        "label": "Delivery",
        "shape": ["plan", "task-map", "impl", "verify", "ship"],
        "closure_edge": ["verify", "impl"],
        "always_present": True,
    },
    "quality": {
        "label": "Quality",
        "shape": ["evidence", "checker", "pass"],
        "closure_edge": ["checker", "evidence"],
        "fault_kinds": ["verify.failed", "review.rejected", "fanout.child.stale_completion"],
    },
    "parity": {
        "label": "Module parity",
        "shape": ["scan", "gap-plan", "closed"],
        "closure_edge": ["gap-plan", "scan"],
        "presence_prefixes": ["module.parity.", "gap_plan.", "flow.gap_plan.", "goal.gap_plan."],
    },
    "replan": {
        "label": "Replan / Learning",
        "shape": ["insight", "proposal", "eval", "adoption"],
        "closure_edge": ["adoption", "insight"],
        "presence_types": ["run.manager.reflect.completed", "orchestrator.replan_requested"],
        "presence_prefixes": ["replan."],
    },
    "approval": {
        "label": "Human approval",
        "shape": ["escalate", "inbox", "decision", "re-inject"],
        "closure_edge": ["re-inject", "escalate"],
        "presence_types": ["human.escalate", "plan.approved"],
    },
    "recovery": {
        "label": "Run recovery",
        "shape": ["observe", "decide", "act", "post-verify"],
        "closure_edge": ["post-verify", "observe"],
        "presence_prefixes": ["run.manager.action.", "autoresearch.repair"],
    },
}

_FAULT_OWNERS = {
    "verify.failed": "quality",
    "review.rejected": "quality",
    "fanout.child.stale_completion": "quality",
    "integration.failed": "quality",
    "runtime.watcher.lag_warning": "observability",
    "human.escalate": "approval",
}


def _payload(event: ZfEvent) -> dict:
    return event.payload if isinstance(event.payload, dict) else {}


def _read_events(state_dir: Path, *, config: Any | None) -> list[tuple[int, ZfEvent]]:
    log = event_log_from_project(state_dir, config=config)
    return list(enumerate(log.read_all()))


def _load_projection(state_dir: Path, name: str) -> dict[str, Any] | None:
    path = Path(state_dir) / "projections" / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------- stages ----------------

def _discover_stages(
    events: list[tuple[int, ZfEvent]],
    spine_stages: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    order: list[str] = []
    fallback_rounds: dict[str, int] = {}
    last_ts: dict[str, str] = {}
    for _seq, event in events:
        stage_id = str(_payload(event).get("stage_id") or "")
        if not stage_id or ":" in stage_id:
            continue
        if stage_id not in order:
            order.append(stage_id)
        if event.type == "fanout.started":
            fallback_rounds[stage_id] = fallback_rounds.get(stage_id, 0) + 1
        last_ts[stage_id] = event.ts
    spine = (spine_stages or {}).get("stages") or {}
    stages = []
    for stage_id in order:
        sv = spine.get(stage_id) or {}
        rounds = int(sv.get("rounds") or fallback_rounds.get(stage_id, 0))
        status = str(sv.get("last_status") or "")
        stages.append({
            "id": stage_id,
            "rounds": rounds,
            "last_status": status,
            "last_ts": str(sv.get("last_ts") or last_ts.get(stage_id, "")),
            "warn": rounds >= 8 or status.endswith((".failed", ".cancelled", ".timed_out")),
        })
    return stages


# ---------------- task attempts ----------------

def _attempts_from_spine(spine_attempts: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = []
    for task_id, entry in (spine_attempts.get("tasks") or {}).items():
        attempts = []
        raw = entry.get("attempts") or []
        for idx, a in enumerate(raw):
            terminal = a.get("terminal") or None
            open_ = terminal is None
            # superseded open attempt(后面还有 attempt)= uncounted(E5 语义)
            counted = not (open_ and idx < len(raw) - 1)
            attempts.append({
                "started_ts": a.get("started_ts", ""),
                "role": a.get("role", ""),
                "terminal": terminal,
                "open": open_,
                "counted": counted,
            })
        tasks.append({
            "id": task_id,
            "attempts": attempts,
            "fails": sum(1 for a in attempts
                         if (a["terminal"] or {}).get("type", "").endswith(".failed")),
            "counted": sum(1 for a in attempts if a["counted"]),
            "source": "task_attempts.json",
        })
    tasks.sort(key=lambda t: -len(t["attempts"]))
    return tasks


def _attempts_from_events(events: list[tuple[int, ZfEvent]]) -> list[dict[str, Any]]:
    """事件回退:配对 dispatch→完成;传输回执去重、孤儿完成保留。"""
    lanes: dict[tuple[str, str], dict[str, list]] = {}
    for seq, event in events:
        p = _payload(event)
        key = (str(p.get("stage_id") or ""),
               str(p.get("child_id") or event.task_id or ""))
        if event.type in ("fanout.child.dispatched", "task.dispatched"):
            lanes.setdefault(key, {"disp": [], "done": []})["disp"].append((seq, event))
        elif key[1] and (event.type.endswith(".completed") or event.type.endswith(".failed")
                         or event.type in ("dev.build.done", "dev.blocked")) \
                and "stale" not in event.type and "aggregate" not in event.type:
            lanes.setdefault(key, {"disp": [], "done": []})["done"].append((seq, event))
    tasks = []
    for (stage_id, child_id), lane in sorted(lanes.items()):
        semantic = [d for d in lane["done"] if d[1].type != _TRANSPORT_COMPLETION]
        transport = [d for d in lane["done"] if d[1].type == _TRANSPORT_COMPLETION]
        used: set[int] = set()
        attempts = []
        for _dseq, disp in lane["disp"]:
            comp = next(((s, e) for s, e in semantic if e.ts >= disp.ts and s not in used), None) \
                or next(((s, e) for s, e in transport if e.ts >= disp.ts and s not in used), None)
            if comp:
                used.add(comp[0])
                attempts.append({
                    "started_ts": disp.ts, "role": str(_payload(disp).get("role_instance") or ""),
                    "terminal": {"type": comp[1].type, "ts": comp[1].ts, "seq": comp[0]},
                    "open": False, "counted": True,
                })
            else:
                attempts.append({
                    "started_ts": disp.ts, "role": str(_payload(disp).get("role_instance") or ""),
                    "terminal": None, "open": True, "counted": True,
                })
        # 孤儿完成(dispatch 在日志窗口外)保留为独立 attempt,不得丢弃
        for seq, event in semantic:
            if seq not in used:
                attempts.insert(0, {
                    "started_ts": event.ts, "role": str(_payload(event).get("role_instance") or ""),
                    "terminal": {"type": event.type, "ts": event.ts, "seq": seq},
                    "open": False, "counted": True, "orphan": True,
                })
        if attempts:
            tasks.append({
                "id": child_id or stage_id,
                "stage_id": stage_id,
                "attempts": attempts,
                "fails": sum(1 for a in attempts
                             if (a["terminal"] or {}).get("type", "").endswith(".failed")),
                "counted": sum(1 for a in attempts if a["counted"]),
                "source": "events",
            })
    tasks.sort(key=lambda t: -len(t["attempts"]))
    return tasks


# ---------------- stage backflows(主环边级回弧)----------------

_BACKFLOW_TRIGGERS = ("verify.failed", "review.rejected", "judge.failed")
_BACKFLOW_KINDS = {"verify.failed": "rework", "review.rejected": "rework", "judge.failed": "replan"}


def _stage_backflows(events: list[tuple[int, ZfEvent]]) -> list[dict[str, Any]]:
    """失败事件 → 下一次跨阶段重派 = 一次边级回流(事件配对,非猜测)。"""
    edges: dict[tuple[str, str, str], int] = {}
    pending: tuple[str, str] | None = None  # (trigger_type, from_stage)
    for _seq, event in events:
        p = _payload(event)
        stage = str(p.get("stage_id") or "")
        if event.type in _BACKFLOW_TRIGGERS and stage and ":" not in stage:
            pending = (event.type, stage)
        elif event.type == "fanout.child.dispatched" and pending and stage \
                and ":" not in stage and stage != pending[1]:
            key = (pending[1], stage, _BACKFLOW_KINDS[pending[0]])
            edges[key] = edges.get(key, 0) + 1
            pending = None
    return [{"from_stage": f, "to_stage": t, "kind": k, "count": n}
            for (f, t, k), n in sorted(edges.items())]


_SUBSCRIBER_TRIGGERS = (
    "task_map.ready", "plan.approved", "verify.failed",
    "review.rejected", "judge.failed", "judge.passed",
)
_SUBSCRIBER_RESULTS = (
    "fanout.child.dispatched", "task.rework.triage.completed",
    "run.manager.repair.accepted", "run.completed",
)


def _subscriber_chains(events: list[tuple[int, ZfEvent]]) -> list[dict[str, Any]]:
    """event → subscriber → result(131 §8.1),每类 trigger 取首例。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, (seq, event) in enumerate(events):
        if event.type not in _SUBSCRIBER_TRIGGERS or event.type in seen:
            continue
        nxt = next(((s2, e2) for s2, e2 in events[i + 1:] if e2.type in _SUBSCRIBER_RESULTS), None)
        if not nxt:
            continue
        seen.add(event.type)
        p2 = _payload(nxt[1])
        out.append({
            "topic": event.type, "seq": seq,
            "subscriber": str(p2.get("stage_id") or nxt[1].type.split(".")[0]),
            "result": str(p2.get("child_id") or nxt[1].type),
            "result_seq": nxt[0],
        })
    return out


# ---------------- promise ----------------

def _promise_chain_from_config(project_root: Path | None) -> list[str] | None:
    if not project_root:
        return None
    cfg = Path(project_root) / "zf.yaml"
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"workflow_completion:.*?required_events:\s*((?:\s*-\s*[\w.]+\n?)+)", text, re.S)
    return re.findall(r"-\s*([\w.]+)", m.group(1)) if m else None


def _promise(events: list[tuple[int, ZfEvent]], project_root: Path | None) -> dict[str, Any]:
    chain = _promise_chain_from_config(project_root)
    source = "workflow_completion contract" if chain else "generic fallback"
    chain = chain or list(GENERIC_PROMISE_CHAIN)
    seen: dict[str, dict[str, Any]] = {}
    for seq, event in events:
        keys = [event.type] + [k for k, aliases in _PROMISE_ALIASES.items()
                               if event.type in aliases]
        for key in keys:
            if key in chain and key not in seen:
                seen[key] = {"seq": seq, "ts": event.ts}
    items = [{"event": key, "satisfied": key in seen, **seen.get(key, {})} for key in chain]
    return {
        "source": source,
        "chain": items,
        "satisfied": sum(1 for i in items if i["satisfied"]),
        "latched": any(i["event"] == "run.completed" and i["satisfied"] for i in items),
    }


# ---------------- business loops / faults / companions ----------------

def _type_counts(events: list[tuple[int, ZfEvent]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _seq, event in events:
        counts[event.type] = counts.get(event.type, 0) + 1
    return counts


def _present(spec: dict[str, Any], counts: dict[str, int]) -> bool:
    if spec.get("always_present"):
        return True
    for ty in spec.get("presence_types", []):
        if counts.get(ty):
            return True
    for prefix in spec.get("presence_prefixes", []):
        if any(ty.startswith(prefix) and n for ty, n in counts.items()):
            return True
    for ty in spec.get("fault_kinds", []):
        if counts.get(ty):
            return True
    return False


def _loop_entry(loop_id: str, spec: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    counts, tasks, promise = ctx["counts"], ctx["tasks"], ctx["promise"]
    entry: dict[str, Any] = {
        "id": loop_id,
        "label": spec["label"],
        "shape": spec["shape"],
        "closure_edge": spec["closure_edge"],
    }
    fails = counts.get("verify.failed", 0) + counts.get("review.rejected", 0)
    task_members = [
        {"kind": "task", "id": t["id"],
         "note": f'{t["counted"]} att · {t["fails"]}✗'}
        for t in sorted(tasks, key=lambda t: -t["fails"])
    ]
    if loop_id == "delivery":
        entry["counts"] = {
            "tasks": len(tasks),
            "attempts": sum(len(t["attempts"]) for t in tasks),
            "rejects": sum(t["fails"] for t in tasks),
        }
        backflow = entry["counts"]["rejects"]
        entry["arc"] = {"state": "active" if backflow else "flow",
                        "label": f"rework backflow · {backflow} rejects"}
        entry["node_stats"] = {
            "plan": {"approved": counts.get("plan.approved", 0)},
            "task-map": {"ready": counts.get("task_map.ready", 0)},
            "impl": {"attempts": entry["counts"]["attempts"], "rejects": entry["counts"]["rejects"]},
            "verify": {"failed": counts.get("verify.failed", 0),
                       "passed": counts.get("verify.passed", 0) + counts.get("verify.child.completed", 0)},
            "ship": {"latched": int(promise["latched"]),
                     "outstanding": len([i for i in promise["chain"] if not i["satisfied"]])},
        }
        entry["health"] = "closed" if promise["latched"] else (
            "diverging" if backflow and not promise["latched"] else "converging")
        entry["members"] = task_members
    elif loop_id == "quality":
        passes = counts.get("verify.passed", 0) + counts.get("judge.passed", 0) \
            + counts.get("verify.child.completed", 0)
        entry["counts"] = {"fails": fails, "passes": passes,
                           "stale": counts.get("fanout.child.stale_completion", 0)}
        entry["arc"] = {"state": "active" if fails else "flow",
                        "label": f"bounded rework · {fails} rejects"}
        entry["node_stats"] = {
            "evidence": {"attempts": sum(t["counted"] for t in tasks)},
            "checker": {"rejects": fails,
                        "review": counts.get("review.rejected", 0) + counts.get("review.approved", 0),
                        "verify": counts.get("verify.failed", 0) + counts.get("verify.passed", 0)},
            "pass": {"passed": passes},
        }
        entry["acct"] = {"open": max(fails - passes, 0), "recovered": passes, "exhausted": 0}
        entry["health"] = "diverging" if fails > passes else ("converging" if fails else "closed")
        entry["members"] = [m for m in task_members if "✗" in m["note"] and not m["note"].endswith("0✗")] or task_members[:3]
    elif loop_id == "parity":
        closed = counts.get("module.parity.closed", 0)
        tripped = counts.get("module.parity.blocked", 0) + counts.get("flow.goal.blocked", 0)
        rounds = sum(n for ty, n in counts.items() if ty.endswith("gap_plan.ready"))
        entry["counts"] = {"rounds": rounds, "closed": closed, "tripped": tripped}
        entry["arc"] = {"state": "broken" if tripped else ("flow" if closed else "active"),
                        "label": "latched" if closed else f"{rounds} gap rounds"}
        entry["node_stats"] = {
            "scan": {"rounds": rounds or closed},
            "gap-plan": {"rounds": rounds},
            "closed": {"closed": closed, "tripped": tripped},
        }
        entry["health"] = "closed" if closed else ("broken" if tripped else "converging")
    elif loop_id == "replan":
        reflections = counts.get("run.manager.reflect.completed", 0)
        adopted = sum(n for ty, n in counts.items() if ty.startswith("replan.")) \
            + counts.get("orchestrator.replan_requested", 0)
        entry["counts"] = {"reflections": reflections, "adopted": adopted}
        entry["arc"] = {"state": "flow" if adopted else "broken",
                        "label": f"{reflections} reflections · {adopted} adopted"}
        entry["node_stats"] = {
            "insight": {"reflections": reflections},
            "proposal": {"proposals": adopted},
            "eval": {},
            "adoption": {"adopted": adopted},
        }
        entry["health"] = "converging" if adopted else "broken"
    elif loop_id == "approval":
        esc = counts.get("human.escalate", 0)
        decisions = sum(n for ty, n in counts.items()
                        if "human" in ty and ("decision" in ty or "resolved" in ty))
        entry["counts"] = {"escalations": esc, "decisions": decisions,
                           "plan_approved": counts.get("plan.approved", 0)}
        entry["arc"] = {"state": "broken" if esc and not decisions else ("active" if esc else "flow"),
                        "label": f"{esc} in / {decisions} back"}
        entry["node_stats"] = {
            "escalate": {"in": esc},
            "inbox": {"pending": max(esc - decisions, 0)},
            "decision": {"made": decisions},
            "re-inject": {"back": decisions},
        }
        entry["health"] = "broken" if esc and not decisions else ("converging" if esc else "closed")
    elif loop_id == "recovery":
        applied = counts.get("run.manager.action.applied", 0)
        verified = counts.get("run.manager.action.verify.passed", 0)
        entry["counts"] = {"planned": counts.get("run.manager.action.planned", 0),
                           "applied": applied, "post_verified": verified,
                           "blocked": counts.get("run.manager.action.blocked", 0)}
        entry["arc"] = {"state": "flow" if verified else "active",
                        "label": f"loop closed · {verified} cycles"}
        entry["node_stats"] = {
            "observe": {"reflections": counts.get("run.manager.reflect.completed", 0)},
            "decide": {"planned": entry["counts"]["planned"]},
            "act": {"applied": applied},
            "post-verify": {"verified": verified, "blocked": entry["counts"]["blocked"]},
        }
        entry["health"] = "closed" if verified else "converging"
    return entry


def _faults(counts: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for kind, owner in _FAULT_OWNERS.items():
        n = counts.get(kind, 0)
        if n:
            out.append({"kind": kind, "count": n, "owner_loop": owner})
    return out


def _companions(counts: dict[str, int]) -> dict[str, Any]:
    """伴生环:有事件才出现(零态缺席)。"""
    out: dict[str, Any] = {}
    if counts.get("run.manager.reflect.completed"):
        out["learning"] = {"reflections": counts["run.manager.reflect.completed"],
                           "autoresearch_requests": counts.get("run.manager.autoresearch.requested", 0)}
    if any(ty.startswith("run.manager.action.") for ty in counts):
        out["repair"] = {"post_verified": counts.get("run.manager.action.verify.passed", 0),
                         "blocked": counts.get("run.manager.action.blocked", 0)}
    human = counts.get("human.escalate", 0) + counts.get("plan.approved", 0) \
        + counts.get("web.action.requested", 0) + counts.get("user.message", 0)
    if human:
        out["human"] = {"signals": human, "escalations": counts.get("human.escalate", 0)}
    lease = counts.get("task.attempt.retry_scheduled", 0)
    if lease:
        out["lease"] = {"retry_scheduled": lease}
    return out


# ---------------- entry point ----------------

def build_loop_view(
    state_dir: Path,
    *,
    config: Any | None = None,
    project_root: Path | None = None,
    project_id: str = "",
    events: list[tuple[int, ZfEvent]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    events = list(events) if events is not None else _read_events(state_dir, config=config)
    counts = _type_counts(events)
    spine_attempts = _load_projection(state_dir, "task_attempts")
    spine_stages = _load_projection(state_dir, "stage_spine")
    spine_health = _load_projection(state_dir, "workflow_health")

    tasks = _attempts_from_spine(spine_attempts) if spine_attempts and spine_attempts.get("tasks") \
        else _attempts_from_events(events)
    promise = _promise(events, project_root)
    ctx = {"counts": counts, "tasks": tasks, "promise": promise}
    loops = {loop_id: _loop_entry(loop_id, spec, ctx)
             for loop_id, spec in LOOP_REGISTRY.items() if _present(spec, counts)}

    pump_total = sum(counts.get(ty, 0) for ty in PUMP_TYPES)
    semantic = [e for e in events if e[1].type not in PUMP_TYPES]
    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "run": {
            "event_count": len(events),
            "semantic_event_count": len(semantic),
            "first_ts": events[0][1].ts if events else "",
            "last_ts": events[-1][1].ts if events else "",
            "latched": promise["latched"],
            "promise": promise,
        },
        "stages": _discover_stages(events, spine_stages),
        "backflows": _stage_backflows(events),
        "subscriber_chains": _subscriber_chains(events),
        "tasks": tasks,
        "loops": loops,
        "faults": _faults(counts),
        "companions": _companions(counts),
        "pump": {
            "total": pump_total,
            "lag_warnings": counts.get("runtime.watcher.lag_warning", 0),
        },
        "health_counters": (spine_health or {}).get("counters") or {},
        "source_projection_refs": [
            "task_attempts.json" if spine_attempts else "EventLog",
            "stage_spine.json" if spine_stages else "EventLog",
            "workflow_health.json" if spine_health else "EventLog",
        ],
    }
    return redact_obj(result)
