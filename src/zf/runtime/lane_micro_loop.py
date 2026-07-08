"""批B(lane 微环):findings → 活会话续改,不换代不重派。

prd-goal e2e 复盘的结构病根治:findings 精确到行,响应粒度却是"新
fanout 代际+新身份+完整 briefing 重派"——一次小修正让整套协调机器
空转一圈(52 派发/16 交付的膨胀主源)。lane 会话已实测连续(rework
同 session),codex 的 turn 内循环在多 agent 架构里的对等物就是:

    拒收 → findings 注入原 pane(continuation)→ agent 原上下文增量改
    → 重发 dev.build.done → 既有 repair/收编路径接受 → 重集成重审。

只有三种情况回退全价重派:lane 死了、同指纹已注入过一次(停滞,
该换方案)、开关未开。机械验收门(admission/F7/树哈希/review)不动。
灰度:goal.micro_loop 默认关 = 现行为零回归。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent

CONTINUATION_EVENT = "task.rework.continuation_injected"
_REJECTION_EVENTS = frozenset({
    "review.rejected", "verify.failed", "test.failed",
})


def micro_loop_enabled(config: Any) -> bool:
    goal = getattr(config, "goal", None)
    return bool(getattr(goal, "enabled", False)) and bool(
        getattr(goal, "micro_loop", False)
    )


def _failed_task_ids(event: ZfEvent) -> list[str]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    out: list[str] = []
    if event.task_id:
        out.append(str(event.task_id))
    for key in ("failed_task_ids", "task_ids", "failed_children"):
        raw = payload.get(key)
        if isinstance(raw, list):
            out.extend(str(item) for item in raw if str(item or "").strip())
    seen: set[str] = set()
    unique = []
    for task_id in out:
        if task_id not in seen:
            seen.add(task_id)
            unique.append(task_id)
    return unique


def _findings_lines(event: ZfEvent) -> list[str]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    lines: list[str] = []
    reason = str(payload.get("reason") or "").strip()
    if reason:
        lines.append(reason)
    findings = payload.get("findings")
    for item in findings if isinstance(findings, list) else []:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            message = str(item.get("message") or "").strip()
            severity = str(item.get("severity") or "").strip()
            text = " ".join(part for part in (severity, path, message) if part)
        else:
            text = str(item).strip()
        if text:
            lines.append(text)
    return lines[:20]


def _lane_for_task(task_id: str, *, task_store: Any, events: list[ZfEvent]) -> str:
    try:
        task = task_store.get(task_id)
        assigned = str(getattr(task, "assigned_to", "") or "")
        if assigned:
            return assigned
    except Exception:
        pass
    for event in reversed(events):
        if event.type == "dev.build.done" and str(event.task_id or "") == task_id:
            actor = str(event.actor or "")
            if actor and actor != "zf-cli":
                return actor
    return ""


def _fingerprint(event: ZfEvent) -> str:
    from zf.runtime.candidate_rework import _rejection_fingerprint

    payload = event.payload if isinstance(event.payload, dict) else {}
    return _rejection_fingerprint(payload)


def _latest_goal_objective(event: ZfEvent, events: list[ZfEvent]) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    objective = str(payload.get("objective") or "").strip()
    if objective:
        return objective
    for prior in reversed(events):
        if prior.type not in {"run.goal.started", "run.goal.updated"}:
            continue
        prior_payload = prior.payload if isinstance(prior.payload, dict) else {}
        objective = str(prior_payload.get("objective") or "").strip()
        if objective:
            return objective
    return "(use the active task contract and current workflow artifacts)"


def _recent_task_state_lines(
    *,
    task_id: str,
    event: ZfEvent,
    events: list[ZfEvent],
) -> list[str]:
    recent: list[ZfEvent] = []
    for prior in reversed(events):
        if prior.task_id and prior.task_id != task_id:
            continue
        if prior.type in {
            "task.dispatched",
            "worker.heartbeat",
            "dev.build.done",
            "verify.failed",
            "verify.passed",
            "review.rejected",
            "review.approved",
            "test.failed",
            "test.passed",
            "goal.rescan.requested",
            CONTINUATION_EVENT,
        }:
            recent.append(prior)
        if len(recent) >= 5:
            break
    if not recent:
        return ["- recent_events: (none recorded before this continuation)"]
    lines = ["- recent_events:"]
    for prior in reversed(recent):
        lines.append(f"  - `{prior.type}` `{prior.id}`")
    lines.append(f"- trigger_event: `{event.type}` `{event.id}`")
    return lines


def _continuation_anchor_lines(
    *,
    task_id: str,
    event: ZfEvent,
    events: list[ZfEvent],
) -> list[str]:
    return [
        "## Objective",
        f"- {_latest_goal_objective(event, events)}",
        "",
        "## Work State",
        f"- active_task: `{task_id}`",
        *_recent_task_state_lines(task_id=task_id, event=event, events=events),
        "",
        "## Next Move",
        "- Treat this as continuation of the same task, not a new task.",
        "- Resolve every finding or open goal gap using the current workdir.",
        "- Re-run focused verification and emit the terminal event from the original briefing.",
        "",
    ]


def maybe_inject_rework_continuation(
    *,
    event: ZfEvent,
    config: Any,
    state_dir: Path,
    events: list[ZfEvent],
    event_writer: Any,
    transport: Any,
    task_store: Any,
) -> list[str]:
    """返回成功注入的 task_id 列表(空 = 走原全价路径)。"""
    if not micro_loop_enabled(config):
        return []
    if event.type not in _REJECTION_EVENTS:
        return []
    task_ids = _failed_task_ids(event)
    if not task_ids:
        return []
    fingerprint = _fingerprint(event)
    injected: list[str] = []
    for task_id in task_ids:
        # 幂等:本拒收已注入过
        duplicate = False
        stalled = False
        for prior in events:
            if prior.type != CONTINUATION_EVENT:
                continue
            payload = prior.payload if isinstance(prior.payload, dict) else {}
            if str(payload.get("task_id") or "") != task_id:
                continue
            if str(payload.get("rework_of") or "") == event.id:
                duplicate = True
                break
            if str(payload.get("fingerprint") or "") == fingerprint:
                # 同指纹已续改过一次仍拒 → 停滞,回退全价路径(换方案)
                stalled = True
        if duplicate or stalled:
            continue
        lane = _lane_for_task(task_id, task_store=task_store, events=events)
        if not lane:
            continue
        try:
            if not bool(transport.is_alive(lane)):
                continue
        except Exception:
            continue
        briefing_path = _write_continuation_briefing(
            state_dir=state_dir,
            lane=lane,
            task_id=task_id,
            event=event,
            config=config,
            events=events,
        )
        try:
            transport.send_task(
                lane,
                briefing_path,
                f"REWORK CONTINUATION for {task_id} — read {briefing_path}",
            )
        except Exception:
            continue
        event_writer.append(ZfEvent(
            type=CONTINUATION_EVENT,
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "lane": lane,
                "rework_of": event.id,
                "rework_source": event.type,
                "fingerprint": fingerprint,
                "briefing_ref": str(briefing_path),
                "pdd_id": str(
                    (event.payload or {}).get("pdd_id")
                    if isinstance(event.payload, dict) else ""
                ) or "",
                "reason": "lane micro-loop: findings injected into live session",
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        injected.append(task_id)
    return injected


def _write_continuation_briefing(
    *,
    state_dir: Path,
    lane: str,
    task_id: str,
    event: ZfEvent,
    config: Any,
    events: list[ZfEvent],
) -> Path:
    briefings_dir = Path(state_dir) / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    path = briefings_dir / f"{lane}-continuation-{task_id}-{event.id}.md"
    goal_lines: list[str] = []
    try:
        from zf.runtime.goal_briefing import goal_briefing_section

        goal_lines = goal_briefing_section(events, config=config)
    except Exception:
        goal_lines = []
    lines = [
        f"# REWORK CONTINUATION: {task_id}",
        "",
        "Your previous delivery for this task was REJECTED. You still own it.",
        "Continue IN THIS SESSION — your workdir/branch state is intact; fix",
        "incrementally, do NOT start over.",
        "",
        *_continuation_anchor_lines(
            task_id=task_id,
            event=event,
            events=events,
        ),
        "Findings to resolve (address EVERY one):",
        *[f"- {line}" for line in _findings_lines(event)],
        "",
        "When fixed: commit, regenerate affected runtime evidence, run your",
        "full self-check (prove nothing else broke), then re-emit",
        "`dev.build.done` exactly as your original briefing instructed —",
        "same task_id and current HEAD as source_commit; stale identity",
        "fields (fanout_id/run_id) are harmless (kernel adopts).",
        "",
        *goal_lines,
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def maybe_inject_rescan_continuation(
    *,
    event: ZfEvent,
    config: Any,
    state_dir: Path,
    events: list[ZfEvent],
    event_writer: Any,
    transport: Any,
    task_store: Any,
) -> list[str]:
    """B2 = G1 消费侧:goal.rescan.requested → 对未终局任务的活 lane
    注入 continuation(objective+未解决 findings),不新起 fanout——
    G 批 defer 项在微环机制上收口。"""
    if not micro_loop_enabled(config):
        return []
    if event.type != "goal.rescan.requested":
        return []
    payload = event.payload if isinstance(event.payload, dict) else {}
    try:
        tasks = task_store.list_all()
    except Exception:
        return []
    injected: list[str] = []
    for task in tasks:
        status = str(getattr(task, "status", "") or "")
        if status in {"done", "cancelled"}:
            continue
        task_id = str(getattr(task, "id", "") or "")
        if not task_id:
            continue
        duplicate = any(
            prior.type == CONTINUATION_EVENT
            and str((prior.payload or {}).get("rework_of") or "") == event.id
            and str((prior.payload or {}).get("task_id") or "") == task_id
            for prior in events
            if isinstance(prior.payload, dict)
        )
        if duplicate:
            continue
        lane = _lane_for_task(task_id, task_store=task_store, events=events)
        if not lane:
            continue
        try:
            if not bool(transport.is_alive(lane)):
                continue
        except Exception:
            continue
        briefing_path = _write_continuation_briefing(
            state_dir=state_dir,
            lane=lane,
            task_id=task_id,
            event=event,
            config=config,
            events=events,
        )
        try:
            transport.send_task(
                lane,
                briefing_path,
                f"GOAL RESCAN for {task_id} — read {briefing_path}",
            )
        except Exception:
            continue
        event_writer.append(ZfEvent(
            type=CONTINUATION_EVENT,
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "lane": lane,
                "rework_of": event.id,
                "rework_source": event.type,
                "fingerprint": "",
                "briefing_ref": str(briefing_path),
                "reason": "goal rescan consumed via lane micro-loop continuation",
                "rescan_ordinal": payload.get("rescan_ordinal"),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        injected.append(task_id)
    return injected


__all__ = [
    "CONTINUATION_EVENT",
    "maybe_inject_rescan_continuation",
    "maybe_inject_rework_continuation",
    "micro_loop_enabled",
]
