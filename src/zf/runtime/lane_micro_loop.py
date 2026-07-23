"""Lane rework delivery: canonical attempt → live/resumed session.

prd-goal e2e 复盘的结构病根治:findings 精确到行,响应粒度却是"新
fanout 代际+新身份+完整 briefing 重派"——一次小修正让整套协调机器
空转一圈(52 派发/16 交付的膨胀主源)。lane 会话已实测连续(rework
同 session),codex 的 turn 内循环在多 agent 架构里的对等物就是:

    拒收 → findings 注入原 pane(continuation)→ agent 原上下文增量改
    → 重发 dev.build.done → 既有 repair/收编路径接受 → 重集成重审。

The micro-loop does not own a second retry policy. It first records the same
canonical ``task.rework.requested`` identity used by full dispatch, then
chooses a delivery mode: inject into a live session or request respawn/resume
of the deterministic provider session. Mechanical gates and canonical caps
remain authoritative.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_ledger import counted_failure_events
from zf.runtime.canonical_recovery import (
    recovery_series_from_event,
    rework_dispatch_count,
    build_rework_cap_payload,
)
from zf.runtime.rework_feedback import (
    feedback_briefing_lines,
    feedback_payload_fields,
    hydrate_rework_feedback,
    write_rework_feedback,
)

CONTINUATION_EVENT = "task.rework.continuation_injected"
GOAL_RESCAN_COMPLETED_EVENT = "goal.rescan.completed"
GOAL_RESCAN_FAILED_EVENT = "goal.rescan.failed"
_REJECTION_EVENTS = frozenset({
    "review.rejected", "verify.failed", "test.failed",
})
_REWORK_IDENTITY_KEYS = (
    "workflow_run_id",
    "contract_revision",
    "stage_slot",
    "target_stage_slot",
    "failure_fingerprint",
    "attempt",
    "max_attempts",
    "feedback_id",
    "finding_ids",
)


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


def _role_for_lane(config: Any, lane: str) -> Any | None:
    for role in getattr(config, "roles", []) or []:
        if lane in {
            str(getattr(role, "instance_id", "") or ""),
            str(getattr(role, "name", "") or ""),
        }:
            return role
    return None


def _task_scope(task_store: Any, task_id: str) -> list[str]:
    try:
        task = task_store.get(task_id)
        return [
            str(item)
            for item in (getattr(getattr(task, "contract", None), "scope", []) or [])
            if str(item).strip()
        ]
    except Exception:
        return []


def _verification_result(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    result = payload.get("verification_result")
    if isinstance(result, Mapping):
        return result
    if isinstance(payload.get("requirement_results"), list):
        return payload
    return None


def _existing_rework_request(
    events: list[ZfEvent],
    *,
    task_id: str,
    trigger_event_id: str,
) -> ZfEvent | None:
    for prior in reversed(events):
        if prior.type != "task.rework.requested":
            continue
        payload = prior.payload if isinstance(prior.payload, dict) else {}
        if str(prior.task_id or payload.get("task_id") or "") != task_id:
            continue
        if str(payload.get("trigger_event_id") or "") == trigger_event_id:
            return prior
    return None


def _delivery_already_recorded(
    events: list[ZfEvent],
    *,
    task_id: str,
    rework_request_id: str,
) -> bool:
    for prior in events:
        payload = prior.payload if isinstance(prior.payload, dict) else {}
        if str(prior.task_id or payload.get("task_id") or "") != task_id:
            continue
        if str(payload.get("rework_request_event_id") or "") != rework_request_id:
            continue
        if prior.type in {CONTINUATION_EVENT, "worker.respawn.requested"}:
            return True
    return False


def _max_rework_attempts(config: Any, lane: str) -> int:
    role = _role_for_lane(config, lane)
    try:
        return max(1, int(getattr(role, "max_rework_attempts", 0) or 2))
    except (TypeError, ValueError):
        return 2


def _emit_micro_loop_cap_fact(
    *,
    event: ZfEvent,
    events: list[ZfEvent],
    event_writer: Any,
    series: Any,
    task_id: str,
    lane: str,
    max_attempts: int,
) -> None:
    """Emit the cap fact for a capped task in a mixed rejection (138 §8)."""
    for existing in events:
        if (
            existing.type == "task.rework.capped"
            and str(existing.causation_id or "") == event.id
            and str(
                (existing.payload or {}).get("task_id")
                or existing.task_id
                or ""
            ) == task_id
        ):
            return
    fingerprint = series.failure_fingerprint or _fingerprint(event)
    failure_events = events
    if not any(existing.id == event.id for existing in events):
        failure_events = [*events, event]
    failures = counted_failure_events(
        failure_events, task_id, fingerprint=fingerprint,
    )
    payload = build_rework_cap_payload(
        series=series,
        failures=failures,
        max_attempts=max_attempts,
        trigger_event=event,
        role=lane,
        extra={
            "source": "lane_micro_loop",
            "max_attempts_source": "role",
            # The aggregate source event is split into one cap fact per child.
            # Recovery ownership is therefore task-local even when the source
            # event itself has no event-level task_id.
            "recovery_scope": "task",
            # cap 耗尽即需 RM 语义分诊(138 裁决 8)。
            "semantic_triage_required": True,
        },
    )
    event_writer.append(ZfEvent(
        type="task.rework.capped",
        actor="zf-cli",
        task_id=task_id,
        payload=payload,
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))


def _record_canonical_request(
    *,
    event: ZfEvent,
    config: Any,
    state_dir: Path,
    events: list[ZfEvent],
    event_writer: Any,
    task_store: Any,
    task_id: str,
    lane: str,
    delivery_mode: str,
) -> tuple[ZfEvent, dict[str, Any], dict[str, Any]] | None:
    existing = _existing_rework_request(
        events,
        task_id=task_id,
        trigger_event_id=event.id,
    )
    if existing is not None:
        payload = existing.payload if isinstance(existing.payload, dict) else {}
        try:
            descriptor = {
                "ref": str(payload.get("rework_feedback_ref") or ""),
                "sha256": str(payload.get("rework_feedback_digest") or ""),
                "kind": "rework_feedback",
                "schema_version": "rework-feedback.v1",
                "content_type": "application/json",
                "required": True,
                "feedback_id": str(payload.get("feedback_id") or ""),
                "finding_ids": list(payload.get("finding_ids") or []),
            }
            body = hydrate_rework_feedback(
                state_dir,
                descriptor,
                expected_task_id=task_id,
                expected_fingerprint=str(payload.get("failure_fingerprint") or ""),
            )
        except Exception:
            return None
        return existing, descriptor, body

    # A verifier may report several ``failed_task_ids`` without setting the
    # event-level task_id.  Canonical recovery identity is still per task, so
    # bind the selected child before counting requests and applying the cap.
    series = replace(recovery_series_from_event(event), task_id=task_id)
    max_attempts = _max_rework_attempts(config, lane)
    prior_count = rework_dispatch_count(
        events,
        series,
        event_type="task.rework.requested",
    )
    if prior_count >= max_attempts:
        # ZF-REVIEW-137-B1(2026-07-16 评审复现):混合多任务拒收里已 cap
        # 的任务此前被静默丢弃——caller 只对 handled 任务负责,reactor 又
        # 因"任一任务已处理"整体抑制全价路径,cap 任务永远拿不到
        # task.rework.capped、RM 永不接手(违反 138 裁决 8)。cap 即事实,
        # 在此发出交 RM 分诊;按 causation+task 幂等,重放不重复。
        _emit_micro_loop_cap_fact(
            event=event,
            events=events,
            event_writer=event_writer,
            series=series,
            task_id=task_id,
            lane=lane,
            max_attempts=max_attempts,
        )
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    fingerprint = series.failure_fingerprint or _fingerprint(event)
    descriptor = write_rework_feedback(
        state_dir,
        task_id=task_id,
        failure_fingerprint=fingerprint,
        source_event=event,
        source_attempt=prior_count + 1,
        verification_result=_verification_result(payload),
        allowed_paths=_task_scope(task_store, task_id),
        required_actions=[
            str(item)
            for item in payload.get("required_actions") or []
            if str(item).strip()
        ],
        summary=str(payload.get("reason") or payload.get("summary") or event.type),
    )
    body = hydrate_rework_feedback(
        state_dir,
        descriptor,
        expected_task_id=task_id,
        expected_fingerprint=fingerprint,
    )
    request = ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id=task_id,
        payload={
            "task_id": task_id,
            "role": str(getattr(_role_for_lane(config, lane), "name", "") or lane),
            "assignee": lane,
            "attempt": prior_count + 1,
            "max_attempts": max_attempts,
            "trigger_event_type": event.type,
            "trigger_event_id": event.id,
            "failure_fingerprint": fingerprint,
            "delivery_mode": delivery_mode,
            **series.to_payload(),
            **feedback_payload_fields(descriptor),
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    )
    request.payload["dispatch_id"] = request.id
    request = event_writer.append(request)
    return request, descriptor, body


def _rework_identity_fields(request: ZfEvent) -> dict[str, Any]:
    payload = request.payload if isinstance(request.payload, dict) else {}
    return {
        key: payload[key]
        for key in _REWORK_IDENTITY_KEYS
        if key in payload and payload[key] not in (None, "", [])
    }


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
    """Return task ids whose canonical rework delivery is handled."""
    if not micro_loop_enabled(config):
        return []
    if event.type not in _REJECTION_EVENTS:
        return []
    task_ids = _failed_task_ids(event)
    if not task_ids:
        return []
    handled: list[str] = []
    for task_id in task_ids:
        lane = _lane_for_task(task_id, task_store=task_store, events=events)
        if not lane:
            continue
        try:
            live = bool(transport.is_alive(lane))
        except Exception:
            live = False
        delivery_mode = "live_session" if live else "resume_session"
        recorded = _record_canonical_request(
            event=event,
            config=config,
            state_dir=state_dir,
            events=events,
            event_writer=event_writer,
            task_store=task_store,
            task_id=task_id,
            lane=lane,
            delivery_mode=delivery_mode,
        )
        if recorded is None:
            continue
        rework_request, feedback_descriptor, feedback_body = recorded
        rework_identity = _rework_identity_fields(rework_request)
        if _delivery_already_recorded(
            events,
            task_id=task_id,
            rework_request_id=rework_request.id,
        ):
            handled.append(task_id)
            continue
        briefing_path = _write_continuation_briefing(
            state_dir=state_dir,
            lane=lane,
            task_id=task_id,
            event=event,
            config=config,
            events=events,
            feedback_descriptor=feedback_descriptor,
            feedback_body=feedback_body,
            rework_identity=rework_identity,
        )
        dispatch_id = rework_request.id
        try:
            task_store.update(
                task_id,
                status="in_progress",
                assigned_to=lane,
                active_dispatch_id=dispatch_id,
            )
        except Exception:
            pass
        if live:
            try:
                transport.send_task(
                    lane,
                    briefing_path,
                    f"REWORK CONTINUATION for {task_id} — read {briefing_path}",
                )
            except Exception:
                live = False
                delivery_mode = "resume_session"
        if live:
            event_writer.append(ZfEvent(
                type="task.dispatched",
                actor="orchestrator",
                task_id=task_id,
                payload={
                    "task_id": task_id,
                    "assignee": lane,
                    "source": "rework_continuation",
                    "dispatch_id": dispatch_id,
                    "delivery_mode": "live_session",
                    "rework_request_event_id": rework_request.id,
                    "briefing": str(briefing_path),
                    **rework_identity,
                    **feedback_payload_fields(feedback_descriptor),
                },
                causation_id=rework_request.id,
                correlation_id=event.correlation_id,
            ))
            event_writer.append(ZfEvent(
                type=CONTINUATION_EVENT,
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "task_id": task_id,
                    "lane": lane,
                    "rework_of": event.id,
                    "rework_source": event.type,
                    "rework_request_event_id": rework_request.id,
                    "dispatch_id": dispatch_id,
                    "delivery_mode": "live_session",
                    "fingerprint": str(
                        (rework_request.payload or {}).get("failure_fingerprint") or ""
                    ),
                    "briefing_ref": str(briefing_path),
                    "reason": "canonical rework delivered to live provider session",
                    **rework_identity,
                    **feedback_payload_fields(feedback_descriptor),
                },
                causation_id=rework_request.id,
                correlation_id=event.correlation_id,
            ))
            handled.append(task_id)
            continue
        event_writer.append(ZfEvent(
            type="worker.respawn.requested",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "role": str(getattr(_role_for_lane(config, lane), "name", "") or lane),
                "instance_id": lane,
                "reason": "canonical rework target session is not live",
                "delivery_mode": "resume_session",
                "continuation_briefing_ref": str(briefing_path),
                "rework_request_event_id": rework_request.id,
                "dispatch_id": dispatch_id,
                **rework_identity,
                **feedback_payload_fields(feedback_descriptor),
            },
            causation_id=rework_request.id,
            correlation_id=event.correlation_id,
        ))
        handled.append(task_id)
    return handled


def _write_continuation_briefing(
    *,
    state_dir: Path,
    lane: str,
    task_id: str,
    event: ZfEvent,
    config: Any,
    events: list[ZfEvent],
    feedback_descriptor: Mapping[str, Any] | None = None,
    feedback_body: Mapping[str, Any] | None = None,
    rework_identity: Mapping[str, Any] | None = None,
) -> Path:
    briefings_dir = Path(state_dir) / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    path = briefings_dir / f"{lane}-continuation-{task_id}-{event.id}.md"
    goal_lines: list[str] = []
    try:
        from zf.runtime.goal_briefing import goal_briefing_section

        goal_lines = goal_briefing_section(
            events,
            config=config,
            role=lane,
            stage="rework",
            output_profile="implementation-result",
        )
    except Exception:
        goal_lines = []
    findings = (
        feedback_briefing_lines(feedback_body)
        if feedback_body is not None
        else _findings_lines(event)
    )
    feedback_refs: list[str] = []
    if feedback_descriptor:
        feedback_refs = [
            "## Immutable Feedback",
            f"- rework_feedback_ref: `{feedback_descriptor.get('ref', '')}`",
            f"- rework_feedback_digest: `{feedback_descriptor.get('sha256', '')}`",
            "- Treat the verifier failure/reproduction command as RED; fix, rerun, then self-audit.",
            "",
        ]
    identity_lines = [
        "## Canonical Attempt Identity",
        *[
            f"- {key}: `{value}`"
            for key, value in (rework_identity or {}).items()
        ],
        "",
    ] if rework_identity else []
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
        *identity_lines,
        *feedback_refs,
        "Findings to resolve (address EVERY one):",
        *[f"- {line}" for line in findings],
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
    if event.type != "goal.rescan.requested":
        return []
    payload = event.payload if isinstance(event.payload, dict) else {}
    if not micro_loop_enabled(config):
        _settle_rescan(
            event=event,
            events=events,
            event_writer=event_writer,
            event_type=GOAL_RESCAN_FAILED_EVENT,
            outcome="micro_loop_disabled",
            eligible_task_ids=[],
            payload=payload,
        )
        return []
    try:
        tasks = task_store.list_all()
    except Exception:
        _settle_rescan(
            event=event,
            events=events,
            event_writer=event_writer,
            event_type=GOAL_RESCAN_FAILED_EVENT,
            outcome="task_store_unavailable",
            eligible_task_ids=[],
            payload=payload,
        )
        return []
    injected: list[str] = []
    eligible_task_ids: list[str] = []
    for task in tasks:
        status = str(getattr(task, "status", "") or "")
        if status in {"done", "cancelled"}:
            continue
        task_id = str(getattr(task, "id", "") or "")
        if not task_id:
            continue
        eligible_task_ids.append(task_id)
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
    if not injected:
        event_type = (
            GOAL_RESCAN_COMPLETED_EVENT
            if not eligible_task_ids
            else GOAL_RESCAN_FAILED_EVENT
        )
        _settle_rescan(
            event=event,
            events=events,
            event_writer=event_writer,
            event_type=event_type,
            outcome=(
                "no_eligible_tasks"
                if not eligible_task_ids
                else "no_live_lane_delivery"
            ),
            eligible_task_ids=eligible_task_ids,
            payload=payload,
        )
    return injected


def _settle_rescan(
    *,
    event: ZfEvent,
    events: list[ZfEvent],
    event_writer: Any,
    event_type: str,
    outcome: str,
    eligible_task_ids: list[str],
    payload: Mapping[str, Any],
) -> None:
    if _rescan_settled(events, event.id):
        return
    event_writer.append(ZfEvent(
        type=event_type,
        actor="zf-cli",
        payload={
            "request_event_id": event.id,
            "rescan_ordinal": payload.get("rescan_ordinal"),
            "outcome": outcome,
            "eligible_task_ids": eligible_task_ids,
            "injected_task_ids": [],
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))


def _rescan_settled(events: list[ZfEvent], request_event_id: str) -> bool:
    return any(
        prior.type in {GOAL_RESCAN_COMPLETED_EVENT, GOAL_RESCAN_FAILED_EVENT}
        and (
            prior.causation_id == request_event_id
            or str((prior.payload or {}).get("request_event_id") or "")
            == request_event_id
        )
        for prior in events
        if isinstance(prior.payload, dict)
    )


__all__ = [
    "CONTINUATION_EVENT",
    "GOAL_RESCAN_COMPLETED_EVENT",
    "GOAL_RESCAN_FAILED_EVENT",
    "maybe_inject_rescan_continuation",
    "maybe_inject_rework_continuation",
    "micro_loop_enabled",
]
