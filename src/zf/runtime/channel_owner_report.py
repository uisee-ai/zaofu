"""Deterministic owner report drafts for Agent Channel."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.channel_projection import project_channel


def build_owner_report_payload(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    owner_id: str,
    member_id: str = "",
    period: str = "current",
    reason: str = "",
    source: str = "runtime",
    request_event_id: str = "",
) -> dict[str, Any]:
    channel = project_channel(Path(state_dir), channel_id) or {}
    workflow_status = _workflow_status(channel)
    replan_status = _replan_status(channel)
    risks = _risks(channel, workflow_status=workflow_status, replan_status=replan_status)
    blockers = _blockers(channel)
    decisions = _decisions(channel, replan_status=replan_status)
    recommended_actions = _recommended_actions(
        workflow_status=workflow_status,
        replan_status=replan_status,
        risks=risks,
        blockers=blockers,
    )
    report_id = _stable_report_id(channel_id, thread_id, owner_id, request_event_id)
    refs = _report_preview_refs(
        request_event_id=request_event_id,
        channel_last_event_id=str(channel.get("last_event_id") or ""),
        channel_last_event_seq=str(channel.get("last_event_seq") or ""),
        reason=reason,
        workflow_status=workflow_status,
        replan_status=replan_status,
    )
    payload = {
        "channel_id": channel_id,
        "thread_id": thread_id or "main",
        "owner_id": owner_id,
        "member_id": member_id,
        "report_id": report_id,
        "period": period,
        "summary": _summary(channel, workflow_status=workflow_status, risks=risks, blockers=blockers),
        "decisions": decisions,
        "risks": risks,
        "blockers": blockers,
        "workflow_status": workflow_status,
        "replan_status": replan_status,
        "recommended_actions": recommended_actions,
        "source": source,
        "refs": refs,
    }
    return redact_obj(payload)


def _workflow_status(channel: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    latest: list[dict[str, str]] = []
    for item in list(channel.get("workflow_requests") or []):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        latest.append({
            "event_id": str(item.get("event_id") or ""),
            "task_id": str(item.get("task_id") or ""),
            "pattern_id": str(item.get("pattern_id") or ""),
            "status": status,
            "reason": str(item.get("reason") or ""),
        })
    reply_counts: dict[str, int] = {}
    for item in list(channel.get("reply_requests") or []):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        reply_counts[status] = reply_counts.get(status, 0) + 1
    return {
        "workflow_counts": counts,
        "reply_counts": reply_counts,
        "pending_workflow_requests": int(channel.get("pending_workflow_requests") or 0),
        "pending_reply_count": int(channel.get("pending_reply_count") or 0),
        "latest_workflows": latest[-5:],
    }


def _replan_status(channel: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for item in list(channel.get("state_updates") or [])[-20:]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "").lower()
        status = str(item.get("status") or "").lower()
        summary = str(item.get("summary") or item.get("reason") or "").strip()
        if "replan" not in kind and "replan" not in status and "replan" not in summary.lower():
            continue
        rows.append({
            "kind": kind or "replan",
            "status": status or "unknown",
            "summary": summary,
            "event_id": str(item.get("event_id") or ""),
            "proposal_ref": str(item.get("proposal_ref") or item.get("artifact_ref") or ""),
        })
    latest = rows[-1] if rows else {}
    needs_owner = str(latest.get("status") or "") in {
        "proposed",
        "eval_requested",
        "revise",
        "blocked",
        "owner_review",
    }
    return {
        "schema_version": "channel-owner-report.replan-status.v0",
        "latest": latest,
        "recent": rows[-5:],
        "needs_owner_decision": needs_owner,
    }


def _risks(
    channel: dict[str, Any],
    *,
    workflow_status: dict[str, Any],
    replan_status: dict[str, Any],
) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    failed_replies = int((workflow_status.get("reply_counts") or {}).get("failed") or 0)
    pending_replies = int(workflow_status.get("pending_reply_count") or 0)
    pending_workflows = int(workflow_status.get("pending_workflow_requests") or 0)
    if failed_replies:
        risks.append({
            "severity": "medium",
            "kind": "provider_reply_failure",
            "summary": f"{failed_replies} channel reply request(s) failed",
        })
    if pending_replies:
        risks.append({
            "severity": "low",
            "kind": "pending_reply",
            "summary": f"{pending_replies} agent reply request(s) still pending",
        })
    if pending_workflows:
        risks.append({
            "severity": "medium",
            "kind": "pending_workflow_gate",
            "summary": f"{pending_workflows} workflow invoke request(s) await kernel decision",
        })
    if replan_status.get("needs_owner_decision"):
        latest = replan_status.get("latest") if isinstance(replan_status.get("latest"), dict) else {}
        risks.append({
            "severity": "medium",
            "kind": "replan_owner_gate",
            "summary": str(latest.get("summary") or "replan proposal awaits owner decision"),
        })
    for item in list(channel.get("state_updates") or [])[-5:]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        if any(token in status for token in ("blocked", "failed", "rejected")):
            risks.append({
                "severity": "high" if "blocked" in status else "medium",
                "kind": status or "state_update",
                "summary": str(item.get("summary") or status),
            })
    return risks[:8]


def _blockers(channel: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for item in list(channel.get("state_updates") or []):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        summary = str(item.get("summary") or "").strip()
        if summary and any(token in status for token in ("blocked", "suspended")):
            blockers.append(summary)
    return blockers[-5:]


def _decisions(channel: dict[str, Any], *, replan_status: dict[str, Any]) -> list[str]:
    decisions: list[str] = []
    latest_replan = replan_status.get("latest") if isinstance(replan_status.get("latest"), dict) else {}
    if latest_replan:
        decisions.append(
            "replan: "
            + str(latest_replan.get("status") or "observed")
            + " "
            + str(latest_replan.get("summary") or "").strip()
        )
    for item in list(channel.get("syntheses") or [])[-5:]:
        if not isinstance(item, dict):
            continue
        decision = str(item.get("decision") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if decision or summary:
            decisions.append(f"{decision}: {summary}".strip(": "))
    return decisions


def _recommended_actions(
    *,
    workflow_status: dict[str, Any],
    replan_status: dict[str, Any],
    risks: list[dict[str, str]],
    blockers: list[str],
) -> list[str]:
    actions: list[str] = []
    if replan_status.get("needs_owner_decision"):
        actions.append("review replan proposal and choose approve/defer/reject through controlled action")
    if blockers:
        actions.append("resolve or narrow the listed blockers before requesting more workflow execution")
    if any(item.get("kind") == "provider_reply_failure" for item in risks):
        actions.append("check provider binding or use runtime-role/fake adapter for deterministic channel validation")
    if int(workflow_status.get("pending_workflow_requests") or 0):
        actions.append("review pending workflow.invoke.requested events and accept/reject through kernel gates")
    if not actions:
        actions.append("continue discussion or request workflow execution only after synthesis is explicit")
    return actions


def _summary(
    channel: dict[str, Any],
    *,
    workflow_status: dict[str, Any],
    risks: list[dict[str, str]],
    blockers: list[str],
) -> str:
    members = len(list(channel.get("members") or []))
    messages = len(list(channel.get("messages") or channel.get("recent_messages") or []))
    workflow_total = sum(int(v) for v in (workflow_status.get("workflow_counts") or {}).values())
    risk_total = len(risks)
    blocker_total = len(blockers)
    return (
        f"Channel has {members} member(s), {messages} message(s), "
        f"{workflow_total} workflow request event(s), {risk_total} risk(s), "
        f"and {blocker_total} blocker(s)."
    )


def _stable_report_id(channel_id: str, thread_id: str, owner_id: str, request_event_id: str) -> str:
    seed = f"{channel_id}:{thread_id}:{owner_id}:{request_event_id}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"owner-report-{digest}"


def _report_preview_refs(
    *,
    request_event_id: str,
    channel_last_event_id: str,
    channel_last_event_seq: str,
    reason: str,
    workflow_status: dict[str, Any],
    replan_status: dict[str, Any],
) -> dict[str, Any]:
    preview_refs: list[dict[str, str]] = []
    if request_event_id:
        preview_refs.append({
            "kind": "event",
            "event_id": request_event_id,
            "name": "owner report request",
        })
    if channel_last_event_id:
        preview_refs.append({
            "kind": "event",
            "event_id": channel_last_event_id,
            "name": "channel latest event",
        })

    workflow_refs: list[dict[str, str]] = []
    task_refs: list[dict[str, str]] = []
    for item in list(workflow_status.get("latest_workflows") or []):
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id") or "").strip()
        task_id = str(item.get("task_id") or "").strip()
        pattern_id = str(item.get("pattern_id") or "").strip()
        if event_id:
            workflow_refs.append({
                "kind": "workflow",
                "event_id": event_id,
                "task_id": task_id,
                "name": pattern_id or task_id or event_id,
            })
        if task_id:
            task_refs.append({"kind": "task", "task_id": task_id, "name": task_id})

    replan_refs: list[dict[str, str]] = []
    for item in list(replan_status.get("recent") or []):
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id") or "").strip()
        proposal_ref = str(item.get("proposal_ref") or "").strip()
        if event_id:
            replan_refs.append({
                "kind": "replan",
                "event_id": event_id,
                "name": proposal_ref or event_id,
            })
        if proposal_ref:
            preview_refs.append({
                "kind": "artifact",
                "path": proposal_ref,
                "name": proposal_ref,
            })

    refs: dict[str, Any] = {
        "request_event_id": request_event_id,
        "channel_last_event_id": channel_last_event_id,
        "channel_last_event_seq": channel_last_event_seq,
        "reason": reason,
    }
    if preview_refs:
        refs["preview_refs"] = _dedupe_ref_dicts(preview_refs)
    if workflow_refs:
        refs["workflow_refs"] = _dedupe_ref_dicts(workflow_refs)
    if task_refs:
        refs["task_refs"] = _dedupe_ref_dicts(task_refs)
    if replan_refs:
        refs["replan_refs"] = _dedupe_ref_dicts(replan_refs)
    return refs


def _dedupe_ref_dicts(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        clean = {
            key: value
            for key, value in row.items()
            if str(value or "").strip()
        }
        key = "|".join(f"{k}={v}" for k, v in sorted(clean.items()))
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out[:20]
