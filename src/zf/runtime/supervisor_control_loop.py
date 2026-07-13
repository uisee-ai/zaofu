"""Supervisor control-loop projections and event synthesis.

This module keeps the Kairos-inspired supervisor loop deterministic:
it folds existing runtime truth into projections and only emits bounded,
idempotent decision / owner-visible message events for unresolved high
attention items.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.autoresearch_invocation import (
    autoresearch_invocation_projection,
    build_invocation_request_event,
)
from zf.runtime.event_problem_registry import spec_for_event
from zf.runtime.problem_taxonomy import problem_envelope_from_attention
from zf.runtime.supervisor_recovery_projection import (
    CONTEXT_RECOVERY_SCHEMA_VERSION,
    SKILL_PROVENANCE_SCHEMA_VERSION,
    context_recovery_projection,
    skill_provenance_projection,
)
from zf.runtime.supervisor_attention import is_actionable_attention


CONTROL_LOOP_SCHEMA_VERSION = "supervisor.control_loop.v0"
CONTROLLED_ACTION_CAPABILITY_SCHEMA_VERSION = "controlled_action.capabilities.v0"
OWNER_MESSAGE_SCHEMA_VERSION = "owner.visible_message.v0"
SUPERVISOR_DECISION_SCHEMA_VERSION = "supervisor.decision.v0"
_OPEN_ATTENTION_STATUSES = {"", "open", "unacknowledged"}
_OWNER_MESSAGE_EVENTS = {
    "owner.visible_message.requested",
    "owner.visible_message.delivery_attempted",
    "owner.visible_message.delivered",
    "owner.visible_message.failed",
    "owner.visible_message.expired",
    "owner.visible_message.superseded",
}


def controlled_action_capability_projection() -> dict[str, Any]:
    """Return the declared deterministic control-action capability map."""

    actions = [
        _cap("create-task", mutates_truth=True, required_fields=("title",)),
        _cap("capture-regression-case", mutates_truth=True, required_fields=("task_id",)),
        _cap("replay-regression-case", mutates_truth=True, required_fields=("case_id",)),
        _cap("update-task", mutates_truth=True, required_fields=("task_id",)),
        _cap("request-fanout", mutates_truth=True, required_fields=("stage_id",)),
        _cap("channel-post-message", mutates_truth=True, required_fields=("channel_id", "text")),
        _cap("channel-create", mutates_truth=True, required_fields=("name",)),
        _cap("channel-invite-member", mutates_truth=True, required_fields=("channel_id", "member_id")),
        _cap("channel-remove-member", mutates_truth=True, required_fields=("channel_id", "member_id")),
        _cap("channel-delete", mutates_truth=True, required_fields=("channel_id",), owner_approval_required=True),
        _cap("channel-clear-history", mutates_truth=True, required_fields=("channel_id",), owner_approval_required=True),
        _cap("channel-mark-read", mutates_truth=True, required_fields=("channel_id",), requires_token=False),
        _cap("channel-synthesis", mutates_truth=True, required_fields=("channel_id", "decision", "summary")),
        _cap("workflow-invoke", mutates_truth=True, required_fields=("task_id", "pattern_id")),
        _cap("channel-drain-replies", mutates_truth=True, required_fields=("channel_id",)),
        _cap("channel-handoff", mutates_truth=True, required_fields=("channel_id", "message_id", "member_id", "target_member_id")),
        _cap("channel-discussion-mode", mutates_truth=True, required_fields=("channel_id", "mode")),
        _cap("channel-owner-report", mutates_truth=True, required_fields=("channel_id", "owner_id")),
        _cap("automation-run", mutates_truth=True, required_fields=("automation_id", "trigger")),
        _cap("maintenance-prepare", mutates_truth=True, required_fields=("trigger_id",), owner_approval_required=True),
        _cap("attention-ack", mutates_truth=True, required_fields=("attention_id|fingerprint",), requires_token=False),
        _cap("attention-snooze", mutates_truth=True, required_fields=("attention_id|fingerprint", "snooze_until")),
        _cap("attention-resolve", mutates_truth=True, required_fields=("attention_id|fingerprint",)),
        _cap("attention-feedback", mutates_truth=True, required_fields=("attention_id|fingerprint",)),
        _cap("attention-escalate", mutates_truth=True, required_fields=("attention_id|fingerprint",)),
        _cap(
            "workflow-batch-resume",
            mutates_truth=True,
            required_fields=("checkpoint_id", "safe_resume_action"),
            owner_approval_required=True,
        ),
    ]
    by_action = {str(item["action"]): item for item in actions}
    return redact_obj({
        "schema_version": CONTROLLED_ACTION_CAPABILITY_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "total": len(actions),
            "mutation_actions": sum(1 for item in actions if item["mutates_truth"]),
            "token_gated": sum(1 for item in actions if item["requires_token"]),
            "owner_approval_required": sum(1 for item in actions if item["owner_approval_required"]),
        },
        "by_action": dict(sorted(by_action.items())),
        "actions": sorted(by_action),
    })


def supervisor_decision_projection(events: list[ZfEvent]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    by_route: Counter[str] = Counter()
    by_outcome: Counter[str] = Counter()
    for event in events:
        if event.type != "supervisor.decision.recorded":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        route = str(payload.get("route") or "")
        outcome = str(payload.get("outcome") or "")
        by_route[route] += 1
        by_outcome[outcome] += 1
        rows.append(redact_obj({
            "event_id": event.id,
            "ts": event.ts,
            "decision_id": str(payload.get("decision_id") or ""),
            "idempotency_key": str(payload.get("idempotency_key") or ""),
            "route": route,
            "outcome": outcome,
            "attention_id": str(payload.get("attention_id") or ""),
            "fingerprint": str(payload.get("fingerprint") or ""),
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "severity": str(payload.get("severity") or ""),
            "title": str(payload.get("title") or ""),
        }))
    return {
        "schema_version": SUPERVISOR_DECISION_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "total": len(rows),
            "by_route": dict(sorted(by_route.items())),
            "by_outcome": dict(sorted(by_outcome.items())),
        },
        "recent": rows[-50:],
    }


def owner_message_delivery_projection(events: list[ZfEvent]) -> dict[str, Any]:
    messages: dict[str, dict[str, Any]] = {}
    by_status: Counter[str] = Counter()
    attempts = 0
    for event in events:
        if event.type not in _OWNER_MESSAGE_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        message_id = _message_id(payload, fallback=event.id)
        row = messages.setdefault(message_id, {
            "message_id": message_id,
            "status": "unknown",
            "attempts": 0,
            "failures": 0,
            "last_event_id": "",
            "last_event_at": "",
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "decision_id": str(payload.get("decision_id") or ""),
            "severity": str(payload.get("severity") or ""),
            "title": str(payload.get("title") or ""),
            "targets": [],
            "last_error": "",
        })
        row["last_event_id"] = event.id
        row["last_event_at"] = event.ts
        row["task_id"] = event.task_id or str(payload.get("task_id") or row.get("task_id") or "")
        row["decision_id"] = str(payload.get("decision_id") or row.get("decision_id") or "")
        row["severity"] = str(payload.get("severity") or row.get("severity") or "")
        row["title"] = str(payload.get("title") or row.get("title") or "")
        target = str(payload.get("target") or payload.get("surface") or "")
        if target and target not in row["targets"]:
            row["targets"].append(target)
        if event.type == "owner.visible_message.requested":
            row["status"] = "requested"
        elif event.type == "owner.visible_message.delivery_attempted":
            row["status"] = "delivery_attempted"
            row["attempts"] = int(row.get("attempts") or 0) + 1
            attempts += 1
        elif event.type == "owner.visible_message.delivered":
            row["status"] = "delivered"
        elif event.type == "owner.visible_message.failed":
            row["status"] = "failed"
            row["failures"] = int(row.get("failures") or 0) + 1
            row["last_error"] = str(payload.get("reason") or payload.get("error") or "")
        elif event.type == "owner.visible_message.expired":
            row["status"] = "expired"
        elif event.type == "owner.visible_message.superseded":
            row["status"] = "superseded"
    for row in messages.values():
        by_status[str(row.get("status") or "unknown")] += 1
    return redact_obj({
        "schema_version": OWNER_MESSAGE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "total": len(messages),
            "attempts": attempts,
            "pending": sum(by_status.get(status, 0) for status in ("requested", "delivery_attempted")),
            "delivered": by_status.get("delivered", 0),
            "failed": by_status.get("failed", 0),
            "by_status": dict(sorted(by_status.items())),
        },
        "recent": sorted(
            messages.values(),
            key=lambda row: str(row.get("last_event_at") or ""),
        )[-50:],
    })


def build_supervisor_control_loop_events(
    snapshot: dict[str, Any],
    *,
    events: list[ZfEvent],
    projection_ref: dict[str, str],
    contract: Any | None = None,
    now: float | None = None,
) -> list[ZfEvent]:
    """Create idempotent decision/message events for actionable attention.

    G2 (doc 87 §3.2 I41 operator 推论, R24): every owner-facing message
    leads with ``events_derived_state`` — what events.jsonl actually says
    about the trace (missing set / terminal seen) — so the human decides
    on truth, not pane appearance. ``contract`` is the reconcile
    GraphContract when available; without it the field still states the
    task-level events verdict.
    """

    existing_decisions = _existing_ids(events, "supervisor.decision.recorded", "decision_id")
    existing_messages = _existing_ids(events, "owner.visible_message.requested", "message_id")
    out: list[ZfEvent] = []
    for item in snapshot.get("attention_items") or []:
        if not isinstance(item, dict):
            continue
        if not is_actionable_attention(item):
            continue
        status = str(item.get("status") or "open")
        if status not in _OPEN_ATTENTION_STATUSES:
            continue
        decision = _decision_payload(item, snapshot=snapshot, projection_ref=projection_ref)
        triage_suppressed = _suppress_owner_message_for_triage(item, decision)
        if triage_suppressed:
            decision["outcome"] = "run_manager_triage_first"
        decision_id = str(decision["decision_id"])
        message = _owner_message_payload(
            item,
            decision=decision,
            projection_ref=projection_ref,
            events_derived_state=_events_derived_state(
                item, events=events, contract=contract, now=now,
            ),
        )
        message_id = str(message["message_id"])
        source_event_ids = [
            str(value) for value in item.get("source_event_ids") or []
            if str(value).strip()
        ]
        if decision_id not in existing_decisions:
            out.append(ZfEvent(
                type="supervisor.decision.recorded",
                actor="zf-supervisor",
                task_id=str(item.get("task_id") or "") or None,
                payload=redact_obj(decision),
                causation_id=source_event_ids[0] if source_event_ids else None,
            ))
            existing_decisions.add(decision_id)
        if message_id not in existing_messages and not triage_suppressed:
            out.append(ZfEvent(
                type="owner.visible_message.requested",
                actor="zf-supervisor",
                task_id=str(item.get("task_id") or "") or None,
                payload=redact_obj(message),
                causation_id=source_event_ids[0] if source_event_ids else None,
            ))
            existing_messages.add(message_id)
        invocation = build_invocation_request_event(
            item,
            decision=decision,
            events=events + out,
            projection_ref=projection_ref,
        )
        if invocation is not None:
            out.append(invocation)
    return out


def control_loop_projection(
    *,
    events: list[ZfEvent],
    state_dir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": CONTROL_LOOP_SCHEMA_VERSION,
        "is_derived_projection": True,
        "controlled_action_capabilities": controlled_action_capability_projection(),
        "supervisor_decisions": supervisor_decision_projection(events),
        "owner_message_delivery": owner_message_delivery_projection(events),
        "autoresearch_invocations": autoresearch_invocation_projection(events),
        "context_recovery": context_recovery_projection(events),
        "skill_provenance": skill_provenance_projection(state_dir),
    }


def _cap(
    action: str,
    *,
    mutates_truth: bool,
    required_fields: tuple[str, ...] = (),
    requires_token: bool = True,
    owner_approval_required: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "controlled_action.capability.v0",
        "action": action,
        "mutates_truth": mutates_truth,
        "requires_token": requires_token,
        "idempotency_key_required": mutates_truth,
        "max_inflight": 1 if mutates_truth else 6,
        "owner_approval_required": owner_approval_required,
        "interrupt_behavior": "fail_closed",
        "required_fields": list(required_fields),
        "evidence_required": mutates_truth,
        "allowed_sources": ["web", "feishu", "openclaw", "cli"],
    }


def _decision_payload(
    item: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    projection_ref: dict[str, str],
) -> dict[str, Any]:
    fingerprint = str(item.get("fingerprint") or item.get("attention_id") or "")
    route = _route_for_attention(item)
    decision_id = "dec-" + _sha1(f"{fingerprint}|{route}")[:12]
    problem_envelope = problem_envelope_from_attention(item)
    return {
        "schema_version": SUPERVISOR_DECISION_SCHEMA_VERSION,
        "project_id": str(snapshot.get("project_id") or ""),
        "decision_id": decision_id,
        "idempotency_key": f"supervisor:{decision_id}",
        "cooldown_key": f"{route}:{fingerprint}",
        "route": route,
        "outcome": "owner_visible_message_requested",
        "attention_id": str(item.get("attention_id") or ""),
        "fingerprint": fingerprint,
        "severity": str(item.get("severity") or ""),
        "source": str(item.get("source") or ""),
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or ""),
        "task_id": str(item.get("task_id") or ""),
        "suggested_route": str(item.get("suggested_route") or ""),
        "insight_type": str(item.get("insight_type") or ""),
        "recommended_route": str(item.get("recommended_route") or ""),
        "source_insight_ref": str(item.get("source_insight_ref") or ""),
        "expected_output": item.get("expected_output") if isinstance(item.get("expected_output"), list) else [],
        "problem_envelope": problem_envelope,
        "confidence": "derived",
        "budget_class": "operator_visible",
        "notification_policy": _notification_policy(item),
        "recovery_policy": _recovery_policy(item),
        "projection_ref": projection_ref,
    }


def _events_derived_state(
    item: dict[str, Any],
    *,
    events: list[ZfEvent],
    contract: Any | None,
    now: float | None,
) -> dict[str, Any]:
    """events.jsonl 推导的 trace 状态 — owner 决策的第一信息源(I41 推论)。

    pane/heartbeat 观感只作附注;这里陈述真相:该 trace 有/无 missing、
    任务终态是否已落盘。R24 案:operator 看 pane 权限框判"卡死"出手,
    events 显示 trace 早已完成 —— 本字段就是防那次无效干预的机械形态。
    """
    task_id = str(item.get("task_id") or "")
    terminal_types = ("judge.passed", "task.done", "ship.completed")
    latest: dict[str, Any] = {}
    terminal_seen = ""
    for event in reversed(events):
        if task_id and str(getattr(event, "task_id", "") or "") != task_id:
            continue
        etype = str(getattr(event, "type", "") or "")
        if not latest and task_id:
            latest = {"type": etype, "ts": str(getattr(event, "ts", "") or "")}
        if etype in terminal_types:
            terminal_seen = etype
            break
    missing_items: list[dict[str, Any]] = []
    missing_available = False
    if contract is not None:
        try:
            from zf.core.workflow.reconcile_expected import (
                expected_next,
                fold_state,
            )
            import time as _time
            traces = fold_state(contract, events)
            probe_now = float(now) if now is not None else _time.time()
            missing_items = [
                {
                    "trace_id": m.trace_id,
                    "stage_id": m.stage_id,
                    "expected": list(m.expected),
                    "age_s": int(m.age_s),
                }
                for m in expected_next(contract, traces, now=probe_now)
            ]
            missing_available = True
        except Exception:
            missing_available = False
    if terminal_seen:
        verdict = "terminal_seen"
    elif missing_available and missing_items:
        verdict = "missing_present"
    elif missing_available:
        verdict = "no_missing"
    else:
        verdict = "no_reconcile_contract"
    return {
        "source": "events.jsonl",
        "verdict": verdict,
        "task_id": task_id,
        "task_terminal_seen": terminal_seen,
        "task_latest_event": latest,
        "missing": missing_items[:10],
        "missing_available": missing_available,
    }


def _owner_message_payload(
    item: dict[str, Any],
    *,
    decision: dict[str, Any],
    projection_ref: dict[str, str],
    events_derived_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision_id = str(decision.get("decision_id") or "")
    message_id = "omsg-" + _sha1(decision_id)[:12]
    human_action_required = _human_action_required(item, decision)
    return {
        "schema_version": OWNER_MESSAGE_SCHEMA_VERSION,
        "message_id": message_id,
        "events_derived_state": events_derived_state or {
            "source": "events.jsonl",
            "verdict": "no_reconcile_contract",
            "missing": [],
            "missing_available": False,
        },
        "decision_id": decision_id,
        "idempotency_key": f"owner-visible:{message_id}",
        "status": "requested",
        "source": "supervisor",
        "route": str(decision.get("route") or "owner_notify"),
        "handled_by": "run-manager" if _run_manager_triage_first(item, decision) else "supervisor",
        "human_action_required": human_action_required,
        "severity": str(item.get("severity") or ""),
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or ""),
        "task_id": str(item.get("task_id") or ""),
        "attention_id": str(item.get("attention_id") or ""),
        "fingerprint": str(item.get("fingerprint") or ""),
        "problem_envelope": decision.get("problem_envelope") or problem_envelope_from_attention(item),
        "notification_policy": _notification_policy(item),
        "recovery_policy": _recovery_policy(item),
        "delivery_targets": _owner_message_delivery_targets(
            item,
            decision=decision,
            human_action_required=human_action_required,
        ),
        "projection_ref": projection_ref,
    }


def _owner_message_delivery_targets(
    item: dict[str, Any],
    *,
    decision: dict[str, Any],
    human_action_required: bool,
) -> list[str]:
    targets = ["web", "channel"]
    if _run_manager_human_decision(item, decision):
        if human_action_required:
            targets.append("feishu")
        return targets
    if human_action_required or _critical_immediate_attention(item, decision):
        targets.append("feishu")
    return targets


# ZF-E2E-RACING-P2 (2026-07-11): conditions that hold by the nature of the
# event itself — declaring them in a registry spec means "when this fires, a
# human owns the next move". A hard budget cap can only be raised/stopped by
# the owner, so cost.budget.exceeded firing IS the condition. Stateful
# conditions (budget_level_changed, run_manager_no_progress) still have no
# evaluator and are intentionally not listed.
_INTRINSIC_HUMAN_CONDITIONS = frozenset({"owner_budget_decision_needed"})


def _human_action_required(item: dict[str, Any], decision: dict[str, Any]) -> bool:
    if bool(item.get("human_action_required")):
        return True
    # ZF-E2E-RACING-P2: registry specs declared human_required_when but
    # nothing ever evaluated it, so an owner_on_human_required policy could
    # never open its gate (racing e2e: cost.budget.exceeded ×38 froze the
    # pipeline with zero owner-visible escalation). Evaluate the intrinsic
    # subset here; fingerprint dedupe upstream folds repeats to one message.
    declared = item.get("human_required_when") or ()
    if isinstance(declared, (list, tuple, set)) and any(
        str(cond) in _INTRINSIC_HUMAN_CONDITIONS for cond in declared
    ):
        return True
    route = str(decision.get("route") or item.get("suggested_route") or "")
    source = str(item.get("source") or "")
    title = str(item.get("title") or "").lower()
    summary = str(item.get("summary") or "").lower()
    if route in {
        "human",
        "owner_notify",
        "human_decision",
        "approval_required",
        "operator_approval",
    }:
        return True
    triage_first = (
        _run_manager_triage_first(item, decision)
        and not _run_manager_human_decision(item, decision)
    )
    human_tokens = (
        "approve",
        "approval",
        "credential",
        "secret",
        "permission",
        "destructive",
        "merge",
        "restart",
    )
    if any(token in title or token in summary for token in human_tokens):
        return True
    if source in {"human_gate", "owner_approval", "repair_closeout"}:
        return True
    return False


def _critical_immediate_attention(item: dict[str, Any], decision: dict[str, Any]) -> bool:
    severity = str(item.get("severity") or "").lower()
    if severity != "critical":
        return False
    return not _run_manager_triage_first(item, decision)


def _suppress_owner_message_for_triage(item: dict[str, Any], decision: dict[str, Any]) -> bool:
    """131 §16.3-4 triage-first 机械闸:RM 可先诊断的项不立即打扰 owner。

    人类必需(route/registry 判定)与 critical 永不压制;被压制项仍记
    supervisor.decision.recorded,RM 处置失败升级为 human.escalate 后
    自然走 owner 通道(escape hatch)。avbs-r5 实证:silent_stall 活锁
    期间每轮 resume 都发 owner 消息,全是 RM 已在自动处置的重复噪音。
    """
    if _human_action_required(item, decision):
        return False
    if str(item.get("severity") or "").lower() == "critical":
        return False
    policy = _notification_policy(item)
    if policy in {
        "run_manager_first",
        "owner_on_repair_failed",
        "owner_on_human_required",
    }:
        return True
    if policy == "owner_immediate":
        return False
    return _run_manager_triage_first(item, decision)


def _run_manager_triage_first(item: dict[str, Any], decision: dict[str, Any]) -> bool:
    if _recovery_policy(item) in {"run_manager", "run_manager_then_autoresearch"}:
        return True
    route = str(decision.get("route") or item.get("suggested_route") or "")
    source = str(item.get("source") or "")
    if route in {"run_manager_recovery", "run_manager_human_decision", "supervisor_autoresearch"}:
        return True
    return source in {"workflow_resume", "autoresearch", "plan_integrity", "run_manager_decision"}


def _notification_policy(item: dict[str, Any]) -> str:
    value = str(item.get("notification_policy") or "").strip()
    if value:
        return value
    spec = _spec_for_attention(item)
    if spec is not None:
        return spec.effective_notification_policy
    return ""


def _recovery_policy(item: dict[str, Any]) -> str:
    value = str(item.get("recovery_policy") or "").strip()
    if value:
        return value
    spec = _spec_for_attention(item)
    if spec is not None:
        return spec.effective_recovery_policy
    return ""


def _spec_for_attention(item: dict[str, Any]):
    for event_type in _attention_event_type_candidates(item):
        spec = spec_for_event(event_type)
        if spec is not None:
            return spec
    return None


def _attention_event_type_candidates(item: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("event_type", "source_event_type", "type"):
        value = str(item.get(key) or "").strip()
        if value:
            candidates.append(value)
    action = item.get("suggested_action")
    if isinstance(action, dict):
        for key in ("event_type", "source_event_type", "type"):
            value = str(action.get(key) or "").strip()
            if value:
                candidates.append(value)
    envelope = item.get("problem_envelope")
    if isinstance(envelope, dict):
        for key in ("event_type", "source_event_type", "type"):
            value = str(envelope.get(key) or "").strip()
            if value:
                candidates.append(value)
    out: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _run_manager_human_decision(item: dict[str, Any], decision: dict[str, Any]) -> bool:
    route = str(decision.get("route") or item.get("suggested_route") or "")
    source = str(item.get("source") or "")
    return route == "run_manager_human_decision" or source == "run_manager_decision"


def _route_for_attention(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "")
    suggested = str(item.get("suggested_route") or "")
    if source == "run_manager_decision" or suggested == "run_manager_human_decision":
        return "run_manager_human_decision"
    if source == "workflow_resume" or suggested == "run_manager_recovery":
        return "run_manager_recovery"
    if source == "autoresearch" or suggested == "autoresearch_trigger":
        return "supervisor_autoresearch"
    if source == "plan_insight" and str(item.get("recommended_route") or "") == "research_probe":
        return "supervisor_autoresearch"
    if suggested in {"l2_orchestrator", "plan_revision"}:
        return "orchestrator_review"
    return "owner_notify"


def _existing_ids(events: list[ZfEvent], event_type: str, payload_key: str) -> set[str]:
    values: set[str] = set()
    for event in events:
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        value = str(payload.get(payload_key) or "").strip()
        if value:
            values.add(value)
    return values


def _message_id(payload: dict[str, Any], *, fallback: str) -> str:
    return str(
        payload.get("message_id")
        or payload.get("owner_message_id")
        or payload.get("id")
        or fallback
    )


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


__all__ = [
    "CONTEXT_RECOVERY_SCHEMA_VERSION",
    "CONTROLLED_ACTION_CAPABILITY_SCHEMA_VERSION",
    "CONTROL_LOOP_SCHEMA_VERSION",
    "OWNER_MESSAGE_SCHEMA_VERSION",
    "SKILL_PROVENANCE_SCHEMA_VERSION",
    "SUPERVISOR_DECISION_SCHEMA_VERSION",
    "build_supervisor_control_loop_events",
    "context_recovery_projection",
    "control_loop_projection",
    "controlled_action_capability_projection",
    "owner_message_delivery_projection",
    "skill_provenance_projection",
    "supervisor_decision_projection",
]
