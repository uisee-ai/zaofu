"""Supervisor Attention v0 projection helpers."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.problem_taxonomy import (
    EXPECTED_NEGATIVE_EVENT_TYPES,
    abnormal_event_projection,
    problem_envelope_from_attention,
)


ATTENTION_SCHEMA_VERSION = "attention-items.v0"


def build_attention_items(
    *,
    events: list[ZfEvent],
    automation: dict[str, Any],
    failure_signals: list[Any],
    plan_integrity: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in events:
        if event.type == "autopilot.proposal.created":
            items.append(_attention_from_autopilot(event))
        elif event.type == "zaofu.bug.detected":
            items.append(_attention_from_zaofu_bug(event))
        elif event.type == "orchestrator.tick.failed":
            items.append(_attention_from_tick_failure(event))
        elif event.type == "human.escalate":
            items.append(_attention_from_human_escalate(event))
        elif event.type == "supervisor.projection.stale":
            items.append(_attention_from_stale_supervisor_projection(event))
        elif event.type in {
            "replan.contract_eval.completed",
            "replan.contract_eval.adoption_blocked",
            "replan.adoption.stale_rejected",
        }:
            item = _attention_from_replan_eval(event)
            if item:
                items.append(item)
    items.extend(_attention_from_abnormal_event_registry(events))
    items.extend(_attention_from_missing_parity_scan_fanout(events))
    items.extend(_attention_from_repeated_runtime_failures(events))
    items.extend(_attention_from_automation(automation))
    for signal in failure_signals[:20]:
        row = failure_signal_row(signal)
        items.append(_attention_item(
            source="autoresearch",
            fingerprint=f"failure:{row.get('fingerprint') or row.get('signal_id')}",
            severity=str(row.get("severity") or "medium"),
            title=str(row.get("summary") or row.get("category") or "failure signal"),
            summary=str(row.get("summary") or ""),
            failure_class=str(row.get("category") or row.get("failure_class") or ""),
            source_event_ids=list(row.get("event_ids") or []),
            source_ref=str(row.get("source_path") or ""),
            suggested_route="autoresearch_trigger",
        ))
    for finding in plan_integrity.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        items.append(_attention_item(
            source="plan_integrity",
            fingerprint=f"plan:{finding.get('finding_id')}",
            severity=str(finding.get("severity") or "info"),
            title=str(finding.get("title") or "plan integrity finding"),
            summary=str(finding.get("summary") or ""),
            task_id=str(finding.get("task_id") or ""),
            source_ref=str(finding.get("source_ref") or ""),
            suggested_route=str(finding.get("suggested_route") or "plan_revision"),
        ))
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("fingerprint") or item.get("attention_id") or "")
        if not key:
            continue
        existing = deduped.get(key)
        if existing is None or severity_rank(item) > severity_rank(existing):
            deduped[key] = item
    return sorted(
        deduped.values(),
        key=lambda row: (-severity_rank(row), row.get("source", ""), row.get("fingerprint", "")),
    )


def attention_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_status: dict[str, int] = {}
    open_count = 0
    for item in items:
        source = str(item.get("source") or "")
        severity = str(item.get("severity") or "")
        status = str(item.get("status") or "open")
        by_source[source] = by_source.get(source, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        if status == "open":
            open_count += 1
    return {
        "open": open_count,
        "total": len(items),
        "by_source": dict(sorted(by_source.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_status": dict(sorted(by_status.items())),
    }


def failure_signal_row(signal: Any) -> dict[str, Any]:
    if hasattr(signal, "to_dict"):
        data = signal.to_dict()
    elif hasattr(signal, "__dataclass_fields__"):
        data = asdict(signal)
    else:
        data = dict(signal) if isinstance(signal, dict) else {}
    return redact_obj(data)


def severity_rank(item: dict[str, Any]) -> int:
    order = {"info": 0, "low": 1, "medium": 2, "warn": 2, "high": 3, "critical": 4}
    return order.get(str(item.get("severity") or "").lower(), 0)


def is_actionable_attention(item: dict[str, Any]) -> bool:
    """Return whether an attention item has a concrete owner/action route."""

    route = str(item.get("suggested_route") or item.get("recommended_route") or "")
    source = str(item.get("source") or "")
    action = item.get("suggested_action")
    if isinstance(action, dict) and str(action.get("kind") or ""):
        return True
    if str(item.get("failure_class") or item.get("primary_failure_class") or ""):
        return True
    if route in {
        "run_manager_recovery",
        "autoresearch_trigger",
        "l2_orchestrator",
        "supervisor_autoresearch",
        "research_probe",
        "owner_notify",
    }:
        return True
    if source in {"workflow_resume", "autoresearch"}:
        return True
    return severity_rank(item) >= 3


def apply_attention_lifecycle(
    items: list[dict[str, Any]],
    events: list[ZfEvent],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Annotate attention candidates with event-sourced lifecycle status."""

    current = now or datetime.now(timezone.utc)
    lifecycle = _attention_lifecycle(events, now=current)
    out: list[dict[str, Any]] = []
    for item in items:
        updated = dict(item)
        keys = _attention_keys(item)
        status = "open"
        lifecycle_ref: dict[str, Any] = {}
        for key in keys:
            ref = lifecycle.get(key)
            if ref:
                status = str(ref.get("status") or status)
                lifecycle_ref = ref
                break
        updated["status"] = status
        if lifecycle_ref:
            updated["lifecycle_event_id"] = str(lifecycle_ref.get("event_id") or "")
            if lifecycle_ref.get("snooze_until"):
                updated["snooze_until"] = str(lifecycle_ref.get("snooze_until") or "")
        out.append(redact_obj(updated))
    return out


def _attention_from_autopilot(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
    return _attention_item(
        source="autopilot",
        fingerprint=f"autopilot:{payload.get('dedupe_key') or event.id}",
        severity=str(payload.get("severity") or "medium"),
        title=str(payload.get("title") or "Autopilot proposal"),
        summary=str(payload.get("reason") or ""),
        task_id=str(payload.get("task_id") or event.task_id or signal.get("task_id") or ""),
        source_event_ids=[event.id],
        source_ref=str(signal.get("event_id") or ""),
        suggested_route="l2_orchestrator",
        suggested_action=payload.get("action_proposal") if isinstance(payload.get("action_proposal"), dict) else {},
    )


def _attention_from_zaofu_bug(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    signature = str(payload.get("signature") or event.id)
    return _attention_item(
        source="zaofu_bug",
        fingerprint=f"zaofu_bug:{signature}:{event.id}",
        severity="critical" if str(payload.get("confidence") or "") == "high" else "high",
        title=f"ZaoFu bug detected: {signature}",
        summary=str(payload.get("suggested_fix_area") or "runtime bug signature matched"),
        source_event_ids=[event.id],
        suggested_route="autoresearch_trigger",
        suggested_action={
            "kind": "diagnose_zaofu_bug_signature",
            "signature": signature,
            "suggested_fix_area": str(payload.get("suggested_fix_area") or ""),
        },
    )


def _attention_from_tick_failure(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    error_type = str(payload.get("error_type") or "tick_failed")
    return _attention_item(
        source="runtime_tick",
        fingerprint=f"runtime_tick:{error_type}:{event.id}",
        severity="high",
        title=f"Runtime tick failed: {error_type}",
        summary=str(payload.get("error") or "orchestrator maintenance tick failed"),
        source_event_ids=[event.id],
        suggested_route="autoresearch_trigger",
        suggested_action={"kind": "diagnose_runtime_tick_failure"},
    )


def _attention_from_human_escalate(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    scope = _runtime_scope_key(event)
    reason = str(
        payload.get("reason")
        or payload.get("summary")
        or payload.get("rework_source")
        or "runtime escalated to human"
    )
    return _attention_item(
        source="runtime_failure",
        fingerprint=f"human_escalate:{scope}",
        severity="critical",
        title="Runtime escalated to human",
        summary=reason,
        task_id=event.task_id or str(payload.get("task_id") or ""),
        source_event_ids=[event.id],
        suggested_route="autoresearch_trigger",
        suggested_action={
            "kind": "diagnose_human_escalation",
            "event_type": event.type,
            "scope": scope,
        },
    )


def _attention_from_stale_supervisor_projection(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    age = str(payload.get("age_seconds") or "")
    latest = str(payload.get("latest_event_type") or "runtime event")
    return _attention_item(
        source="runtime_failure",
        fingerprint=str(
            payload.get("fingerprint")
            or f"supervisor_projection_stale:{event.id}"
        ),
        severity="high",
        title="Supervisor projection is stale",
        summary=(
            f"supervisor snapshot is {age}s behind latest {latest}"
            if age else f"supervisor snapshot is behind latest {latest}"
        ),
        task_id=event.task_id or str(payload.get("task_id") or ""),
        source_event_ids=[event.id],
        suggested_route="autoresearch_trigger",
        suggested_action={
            "kind": "refresh_supervisor_projection",
            "snapshot_path": str(payload.get("snapshot_path") or ""),
            "latest_event_id": str(payload.get("latest_event_id") or ""),
        },
    )


def _attention_from_repeated_runtime_failures(
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[ZfEvent]] = {}
    for event in events:
        if event.type not in EXPECTED_NEGATIVE_EVENT_TYPES:
            continue
        grouped.setdefault((event.type, _runtime_scope_key(event)), []).append(event)

    rows: list[dict[str, Any]] = []
    for (event_type, scope), refs in grouped.items():
        if len(refs) < 2:
            continue
        latest = refs[-1]
        payload = latest.payload if isinstance(latest.payload, dict) else {}
        reason = str(
            payload.get("reason")
            or payload.get("error")
            or payload.get("summary")
            or f"{event_type} repeated {len(refs)} times"
        )
        rows.append(_attention_item(
            source="runtime_failure",
            fingerprint=f"repeated:{event_type}:{scope}",
            severity="high",
            title=f"Repeated {event_type}",
            summary=f"{len(refs)} event(s): {reason}",
            task_id=latest.task_id or str(payload.get("task_id") or ""),
            source_event_ids=[event.id for event in refs[-10:]],
            suggested_route="autoresearch_trigger",
            suggested_action={
                "kind": "diagnose_repeated_runtime_failure",
                "event_type": event_type,
                "scope": scope,
                "count": len(refs),
            },
        ))
    return rows


def _attention_from_abnormal_event_registry(
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        projection = abnormal_event_projection(event)
        if not projection:
            continue
        rows.append(_attention_item(
            source=str(projection.get("source") or "runtime_event"),
            fingerprint=str(projection.get("fingerprint") or event.id),
            severity=str(projection.get("severity") or "medium"),
            title=str(projection.get("title") or event.type),
            summary=str(projection.get("summary") or ""),
            task_id=str(projection.get("task_id") or ""),
            source_event_ids=[
                str(value) for value in projection.get("source_event_ids") or []
                if str(value).strip()
            ],
            source_ref=str(projection.get("source_ref") or ""),
            suggested_route=str(projection.get("suggested_route") or "run_manager_recovery"),
            suggested_action=(
                projection.get("suggested_action")
                if isinstance(projection.get("suggested_action"), dict)
                else {}
            ),
            failure_class=str(projection.get("failure_class") or ""),
            owner_route=str(projection.get("owner_route") or ""),
            action_policy=str(projection.get("action_policy") or ""),
            intervention_class=str(projection.get("intervention_class") or ""),
            problem_envelope=(
                projection.get("problem_envelope")
                if isinstance(projection.get("problem_envelope"), dict)
                else {}
            ),
        ))
    return rows


def _attention_from_missing_parity_scan_fanout(
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    started_trigger_ids = {
        str((event.payload if isinstance(event.payload, dict) else {}).get("trigger_event_id") or "")
        for event in events
        if event.type == "fanout.started"
    }
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.type != "verify.parity_scan.requested":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.id and event.id in started_trigger_ids:
            continue
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "")
        task_map_ref = str(payload.get("task_map_ref") or payload.get("target_ref") or "")
        rows.append(_attention_item(
            source="workflow_runtime",
            fingerprint=f"parity_scan:no_fanout:{event.id or pdd_id or task_map_ref}",
            severity="high",
            title="Module parity scan fanout did not start",
            summary=(
                f"verify.parity_scan.requested {event.id or '(no id)'} "
                "has no matching fanout.started trigger"
            ),
            source_event_ids=[event.id],
            source_ref=f"events.jsonl#{event.id}" if event.id else "",
            suggested_route="run_manager_recovery",
            suggested_action={
                "kind": "request_fanout",
                "stage_id": "cangjie-module-parity-scan",
                "trigger_event_id": event.id,
                "event_type": event.type,
                "pdd_id": pdd_id,
                "task_map_ref": task_map_ref,
            },
        ))
    return rows


def _runtime_scope_key(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in (
        "pdd_id",
        "target_ref",
        "task_id",
        "fanout_id",
        "trigger_event_id",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    if event.task_id:
        return event.task_id
    return event.id


def _attention_from_replan_eval(event: ZfEvent) -> dict[str, Any] | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    decision = str(payload.get("decision") or "").strip()
    if event.type == "replan.contract_eval.completed" and decision == "adopt":
        return None
    eval_id = str(payload.get("eval_id") or payload.get("idempotency_key") or event.id)
    failure_label = (
        "stale_rejected"
        if event.type == "replan.adoption.stale_rejected"
        else "adoption_blocked"
        if event.type == "replan.contract_eval.adoption_blocked"
        else decision or "blocked"
    )
    failed_checks = payload.get("failed_checks")
    if not isinstance(failed_checks, list):
        failed_checks = []
    errors = payload.get("errors")
    if not isinstance(errors, list):
        errors = []
    summary = ", ".join(
        [str(item) for item in failed_checks[:5] if str(item).strip()]
        or [str(item) for item in errors[:3] if str(item).strip()]
    )
    return _attention_item(
        source="replan_eval",
        fingerprint=f"replan_eval:{eval_id}:{failure_label}",
        severity="high" if failure_label in {"reject", "escalate", "stale_rejected"} else "medium",
        title=f"Replan contract gate {failure_label}",
        summary=summary or "replan contract gate did not adopt",
        source_event_ids=[event.id],
        source_ref=_artifact_ref(payload),
        suggested_route="l2_orchestrator",
        suggested_action={
            "kind": "review_replan_contract_eval",
            "decision": decision,
            "event_type": event.type,
            "eval_id": eval_id,
            "task_map_ref": str(
                payload.get("new_task_map_ref")
                or payload.get("task_map_ref")
                or ""
            ),
        },
    )


def _artifact_ref(payload: dict[str, Any]) -> str:
    direct = str(payload.get("artifact_ref") or "").strip()
    if direct:
        return direct
    refs = payload.get("refs")
    if isinstance(refs, dict):
        return str(refs.get("artifact_ref") or "").strip()
    return ""


def _attention_from_automation(automation: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    items = automation.get("items") if isinstance(automation.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict) or item.get("automation_id") != "project-monitor":
            continue
        outputs = item.get("outputs") if isinstance(item.get("outputs"), list) else []
        if not outputs or not isinstance(outputs[0], dict):
            continue
        monitor = outputs[0]
        for key, severity in (
            ("alerts", "warn"),
            ("progress_alerts", "warn"),
            ("channel_alerts", "warn"),
            ("open_proposals", "info"),
        ):
            for ref in monitor.get(key) or []:
                if not isinstance(ref, dict):
                    continue
                event_id = str(ref.get("event_id") or ref.get("request_event_id") or "")
                rows.append(_attention_item(
                    source="automation",
                    fingerprint=f"automation:{key}:{event_id or ref.get('proposal_id')}",
                    severity=severity,
                    title=str(ref.get("type") or key),
                    summary=str(ref.get("reason") or ref.get("summary") or ""),
                    task_id=str(ref.get("task_id") or ""),
                    source_event_ids=[event_id] if event_id else [],
                    source_ref=str(ref.get("proposal_id") or ""),
                    suggested_route="l2_orchestrator",
                ))
    return rows


def _attention_item(
    *,
    source: str,
    fingerprint: str,
    severity: str,
    title: str,
    summary: str,
    task_id: str = "",
    source_event_ids: list[str] | None = None,
    source_ref: str = "",
    suggested_route: str = "observe_only",
    suggested_action: dict[str, Any] | None = None,
    failure_class: str = "",
    owner_route: str = "",
    action_policy: str = "",
    intervention_class: str = "",
    problem_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attention_id = "attn-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    item = {
        "schema_version": "attention-item.v0",
        "attention_id": attention_id,
        "source": source,
        "fingerprint": fingerprint,
        "severity": severity or "medium",
        "status": "open",
        "title": title,
        "summary": summary,
        "task_id": task_id,
        "source_event_ids": [value for value in (source_event_ids or []) if value],
        "source_ref": source_ref,
        "suggested_route": suggested_route,
        "suggested_action": suggested_action or {},
    }
    if failure_class:
        item["failure_class"] = failure_class
    if owner_route:
        item["owner_route"] = owner_route
    if action_policy:
        item["action_policy"] = action_policy
    if intervention_class:
        item["intervention_class"] = intervention_class
    if isinstance(problem_envelope, dict) and problem_envelope.get("schema_version"):
        item["problem_envelope"] = problem_envelope
    else:
        item["problem_envelope"] = problem_envelope_from_attention(item)
    return redact_obj(item)


def _attention_lifecycle(
    events: list[ZfEvent],
    *,
    now: datetime,
) -> dict[str, dict[str, Any]]:
    lifecycle: dict[str, dict[str, Any]] = {}
    for event in events:
        if not event.type.startswith("runtime.attention."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        keys = _attention_keys(payload)
        if not keys:
            continue
        status = ""
        if event.type == "runtime.attention.acknowledged":
            status = "acknowledged"
        elif event.type == "runtime.attention.snoozed":
            snooze_until = str(payload.get("snooze_until") or "")
            if _snooze_active(snooze_until, now=now):
                status = "snoozed"
            else:
                status = "open"
        elif event.type == "runtime.attention.resolved":
            status = "resolved"
        elif event.type == "runtime.attention.escalated":
            status = "escalated"
        elif event.type == "runtime.attention.unacknowledged":
            status = "unacknowledged"
        if not status:
            continue
        ref = {
            "status": status,
            "event_id": event.id,
            "snooze_until": str(payload.get("snooze_until") or ""),
        }
        for key in keys:
            lifecycle[key] = ref
    return lifecycle


def _attention_keys(payload: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    fingerprint = str(payload.get("fingerprint") or "").strip()
    attention_id = str(payload.get("attention_id") or "").strip()
    if fingerprint:
        keys.append(f"fingerprint:{fingerprint}")
    if attention_id:
        keys.append(f"attention_id:{attention_id}")
    return keys


def _snooze_active(value: str, *, now: datetime) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > now


__all__ = [
    "ATTENTION_SCHEMA_VERSION",
    "apply_attention_lifecycle",
    "attention_summary",
    "build_attention_items",
    "failure_signal_row",
]
