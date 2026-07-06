"""Gated Autoresearch invocation contracts.

Supervisor may request diagnosis, but runtime handlers keep repair
bounded: default accepted requests are proposal-only and never apply
patches directly to the main worktree.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj


AUTORESEARCH_INVOCATION_SCHEMA_VERSION = "autoresearch.invocation.v0"
AUTORESEARCH_INVOCATION_EVENTS = {
    "autoresearch.invocation.requested",
    "autoresearch.invocation.accepted",
    "autoresearch.invocation.rejected",
}
_SAFE_LEVELS = {"", "diagnose", "l1", "L1"}
_DIRECT_APPLY_POLICIES = {"direct_apply", "mainline_apply", "auto_apply", "apply_to_main"}


def autoresearch_invocation_projection(events: list[ZfEvent]) -> dict[str, Any]:
    invocations: dict[str, dict[str, Any]] = {}
    by_status: Counter[str] = Counter()
    for event in events:
        if event.type not in AUTORESEARCH_INVOCATION_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        invocation_id = invocation_id_from_payload(payload, fallback=event.id)
        row = invocations.setdefault(invocation_id, {
            "invocation_id": invocation_id,
            "status": "unknown",
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "severity": str(payload.get("severity") or ""),
            "fingerprint": str(payload.get("fingerprint") or ""),
            "source": str(payload.get("source") or ""),
            "level": str(payload.get("level") or ""),
            "apply_policy": str(payload.get("apply_policy") or ""),
            "last_event_id": "",
            "last_event_type": "",
            "last_event_at": "",
            "reason": "",
        })
        row["last_event_id"] = event.id
        row["last_event_type"] = event.type
        row["last_event_at"] = event.ts
        row["task_id"] = event.task_id or str(payload.get("task_id") or row.get("task_id") or "")
        row["severity"] = str(payload.get("severity") or row.get("severity") or "")
        row["fingerprint"] = str(payload.get("fingerprint") or row.get("fingerprint") or "")
        row["source"] = str(payload.get("source") or row.get("source") or "")
        row["level"] = str(payload.get("level") or row.get("level") or "")
        row["apply_policy"] = str(payload.get("apply_policy") or row.get("apply_policy") or "")
        if event.type == "autoresearch.invocation.requested":
            row["status"] = "requested"
        elif event.type == "autoresearch.invocation.accepted":
            row["status"] = "accepted"
        else:
            row["status"] = "rejected"
            row["reason"] = str(payload.get("reason") or payload.get("reject_reason") or "")
    for row in invocations.values():
        by_status[str(row.get("status") or "unknown")] += 1
    return redact_obj({
        "schema_version": "autoresearch.invocations.projection.v0",
        "is_derived_projection": True,
        "summary": {
            "total": len(invocations),
            "pending": by_status.get("requested", 0),
            "accepted": by_status.get("accepted", 0),
            "rejected": by_status.get("rejected", 0),
            "by_status": dict(sorted(by_status.items())),
        },
        "recent": sorted(
            invocations.values(),
            key=lambda row: str(row.get("last_event_at") or ""),
        )[-50:],
    })


def build_invocation_request_event(
    item: dict[str, Any],
    *,
    decision: dict[str, Any],
    events: list[ZfEvent],
    projection_ref: dict[str, str],
) -> ZfEvent | None:
    if str(decision.get("route") or "") != "supervisor_autoresearch":
        return None
    fingerprint = str(item.get("fingerprint") or item.get("attention_id") or "")
    invocation_id = "arinv-" + _sha1(fingerprint)[:12]
    if invocation_id in handled_invocation_ids(events):
        return None
    source_event_ids = [
        str(value) for value in item.get("source_event_ids") or []
        if str(value).strip()
    ]
    payload = {
        "schema_version": AUTORESEARCH_INVOCATION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "source": "supervisor",
        "level": "diagnose",
        "apply_policy": "proposal_only",
        "sandbox_required": True,
        "requires_owner_approval_for_apply": True,
        "direct_mainline_apply": False,
        "trigger_reason": str(item.get("summary") or item.get("title") or ""),
        "severity": str(item.get("severity") or ""),
        "task_id": str(item.get("task_id") or ""),
        "attention_id": str(item.get("attention_id") or ""),
        "fingerprint": fingerprint,
        "decision_id": str(decision.get("decision_id") or ""),
        "failure_signal_ids": source_event_ids,
        "evidence_paths": _string_list(item.get("evidence_paths")),
        "mode": "probe" if str(item.get("recommended_route") or "") == "research_probe" else "debug",
        "insight_type": str(item.get("insight_type") or ""),
        "source_insight_ref": str(item.get("source_insight_ref") or ""),
        "expected_output": (
            item.get("expected_output")
            if isinstance(item.get("expected_output"), list)
            else ["diagnosis_report", "reproduction_steps", "patch_proposal"]
        ),
        "recommended_route": str(item.get("recommended_route") or ""),
        "target_metrics": (
            item.get("expected_output")
            if isinstance(item.get("expected_output"), list)
            else ["diagnosis_report", "reproduction_steps", "patch_proposal"]
        ),
        "budget": {"max_runs": 1, "max_minutes": 30},
        "resume_policy": "resume_original_task_after_validation",
        "projection_ref": projection_ref,
    }
    return ZfEvent(
        type="autoresearch.invocation.requested",
        actor="zf-supervisor",
        task_id=payload["task_id"] or None,
        payload=redact_obj(payload),
        causation_id=source_event_ids[0] if source_event_ids else None,
    )


def build_invocation_request_from_run_manager_event(
    event: ZfEvent,
    *,
    events: list[ZfEvent],
) -> ZfEvent | None:
    if event.type != "run.manager.autoresearch.requested":
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    if _is_observability_only_run_manager_request(payload):
        return None
    request_id = str(
        payload.get("request_id")
        or payload.get("loop_request_id")
        or event.correlation_id
        or event.id
    ).strip()
    if not request_id:
        return None
    invocation_id = str(payload.get("invocation_id") or f"arinv-rm-{_sha1(request_id)[:12]}")
    if invocation_id in handled_invocation_ids(events):
        return None
    source_event_ids = _string_list(payload.get("source_event_ids"))
    evidence_paths = _string_list(payload.get("evidence_paths"))
    for key in ("source_ref", "context_ref"):
        value = str(payload.get(key) or "").strip()
        if value:
            evidence_paths.append(value)
    return ZfEvent(
        type="autoresearch.invocation.requested",
        actor="run-manager",
        task_id=event.task_id or str(payload.get("task_id") or "") or None,
        causation_id=event.id,
        correlation_id=event.correlation_id or request_id,
        payload=redact_obj({
            "schema_version": AUTORESEARCH_INVOCATION_SCHEMA_VERSION,
            "invocation_id": invocation_id,
            "request_id": request_id,
            "loop_request_id": request_id,
            "run_manager_request_id": request_id,
            "source": "run_manager",
            "level": "diagnose",
            "apply_policy": "proposal_only",
            "sandbox_required": True,
            "requires_owner_approval_for_apply": True,
            "direct_mainline_apply": False,
            "trigger_reason": str(
                payload.get("summary")
                or payload.get("title")
                or payload.get("failure_class")
                or "Run Manager requested Autoresearch diagnosis"
            ),
            "severity": str(payload.get("severity") or "high"),
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "attention_id": str(payload.get("attention_id") or ""),
            "fingerprint": str(payload.get("fingerprint") or request_id),
            "failure_signal_ids": source_event_ids,
            "evidence_paths": evidence_paths,
            "mode": str(payload.get("mode") or payload.get("research_mode") or "debug"),
            "insight_type": str(payload.get("insight_type") or ""),
            "source_insight_ref": str(
                payload.get("source_insight_ref")
                or payload.get("context_ref")
                or "projections/run_manager.json#run_context_bundle"
            ),
            "expected_output": (
                _string_list(payload.get("expected_output"))
                or ["diagnosis_report", "reproduction_steps", "patch_or_resume_proposal"]
            ),
            "recommended_route": "run_manager_diagnosis",
            "target_metrics": (
                _string_list(payload.get("expected_output"))
                or ["diagnosis_report", "reproduction_steps", "patch_or_resume_proposal"]
            ),
            "budget": payload.get("budget") if isinstance(payload.get("budget"), dict) else {
                "max_runs": 1,
                "max_minutes": 30,
            },
            "resume_policy": str(payload.get("resume_policy") or "return_proposal_to_run_manager"),
            "owner_route": str(payload.get("owner_route") or "run_manager"),
            "action_policy": str(payload.get("action_policy") or "needs_diagnosis"),
            "intervention_class": str(payload.get("intervention_class") or "diagnose"),
            "safe_resume_action": str(payload.get("safe_resume_action") or ""),
            "checkpoint_id": str(payload.get("checkpoint_id") or ""),
            "projection_ref": {
                "run_manager": str(payload.get("context_ref") or "projections/run_manager.json#run_context_bundle"),
            },
        }),
    )


def _is_observability_only_run_manager_request(payload: dict[str, Any]) -> bool:
    """Suppress low-confidence active-run observations before L2 escalation.

    Run Manager may diagnose active workflow state on every tick. A dispatched
    fanout child without a terminal event is only actionable after timeout or a
    deterministic recovery checkpoint; otherwise it is normal in-flight work.
    """
    fingerprint = str(payload.get("fingerprint") or "").lower()
    text = " ".join([
        fingerprint,
        str(payload.get("summary") or ""),
        str(payload.get("title") or ""),
        str(payload.get("failure_class") or ""),
        str(payload.get("safe_resume_action") or ""),
    ]).lower()
    if "fanout_child_pending" not in text and (
        "fanout child dispatched without a terminal child event" not in text
    ):
        return False
    return not any(
        marker in text
        for marker in (
            "fanout_timed_out",
            "fanout.timed_out",
            "timed out",
            "silent_stall",
            "resume",
            "rework",
            "fanout.child.failed",
            "child failed",
        )
    )


def handled_invocation_ids(events: list[ZfEvent]) -> set[str]:
    handled: set[str] = set()
    for event in events:
        if event.type not in AUTORESEARCH_INVOCATION_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        invocation_id = invocation_id_from_payload(payload, fallback="")
        if invocation_id:
            handled.add(invocation_id)
    return handled


def invocation_id_from_payload(payload: dict[str, Any], *, fallback: str) -> str:
    return str(payload.get("invocation_id") or payload.get("trigger_id") or fallback)


def validate_invocation_request(payload: dict[str, Any]) -> str:
    level = str(payload.get("level") or payload.get("requested_level") or "diagnose")
    apply_policy = str(payload.get("apply_policy") or "proposal_only")
    if level not in _SAFE_LEVELS:
        return "only L1 diagnose invocation is accepted automatically"
    if apply_policy in _DIRECT_APPLY_POLICIES:
        return "direct apply is not accepted by supervisor autoresearch"
    return ""


def acceptance_payload(payload: dict[str, Any], *, source_event_id: str) -> dict[str, Any]:
    invocation_id = invocation_id_from_payload(payload, fallback=source_event_id)
    out = {
        "schema_version": AUTORESEARCH_INVOCATION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "source": str(payload.get("source") or "supervisor"),
        "level": "diagnose",
        "apply_policy": "proposal_only",
        "sandbox_required": True,
        "accepted_level": "L1",
        "source_event_id": source_event_id,
        "severity": str(payload.get("severity") or ""),
        "fingerprint": str(payload.get("fingerprint") or ""),
        "reason": str(payload.get("trigger_reason") or payload.get("reason") or ""),
        "evidence_paths": _string_list(payload.get("evidence_paths")),
        "mode": str(payload.get("mode") or "debug"),
        "insight_type": str(payload.get("insight_type") or ""),
        "source_insight_ref": str(payload.get("source_insight_ref") or ""),
        "expected_output": _string_list(payload.get("expected_output")),
    }
    _copy_request_identity(payload, out)
    return out


def rejection_payload(
    payload: dict[str, Any],
    *,
    source_event_id: str,
    reason: str,
) -> dict[str, Any]:
    invocation_id = invocation_id_from_payload(payload, fallback=source_event_id)
    return {
        "schema_version": AUTORESEARCH_INVOCATION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "source": str(payload.get("source") or "supervisor"),
        "level": str(payload.get("level") or ""),
        "apply_policy": str(payload.get("apply_policy") or ""),
        "source_event_id": source_event_id,
        "severity": str(payload.get("severity") or ""),
        "fingerprint": str(payload.get("fingerprint") or ""),
        "reason": reason,
        "mode": str(payload.get("mode") or ""),
        "insight_type": str(payload.get("insight_type") or ""),
        "source_insight_ref": str(payload.get("source_insight_ref") or ""),
    }


def trigger_payload_from_invocation(
    payload: dict[str, Any],
    *,
    source_event_id: str,
) -> dict[str, Any]:
    invocation_id = invocation_id_from_payload(payload, fallback=source_event_id)
    out = {
        "trigger_id": invocation_id,
        "invocation_id": invocation_id,
        "source": "autoresearch.invocation.accepted",
        "mode": "supervised_diagnose",
        "research_mode": str(payload.get("mode") or "debug"),
        "source_insight_ref": str(payload.get("source_insight_ref") or ""),
        "insight_type": str(payload.get("insight_type") or ""),
        "expected_output": _string_list(payload.get("expected_output")),
        "apply_policy": "proposal_only",
        "severity": str(payload.get("severity") or ""),
        "reason": str(payload.get("trigger_reason") or payload.get("reason") or ""),
        "fingerprint": str(payload.get("fingerprint") or ""),
        "evidence_paths": _string_list(payload.get("evidence_paths")),
        "source_event_id": source_event_id,
    }
    _copy_request_identity(payload, out)
    return out


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _copy_request_identity(source: dict[str, Any], target: dict[str, Any]) -> None:
    request_id = str(
        source.get("run_manager_request_id")
        or source.get("request_id")
        or source.get("loop_request_id")
        or ""
    ).strip()
    if not request_id:
        return
    target["request_id"] = request_id
    target["loop_request_id"] = request_id
    target["run_manager_request_id"] = request_id


__all__ = [
    "AUTORESEARCH_INVOCATION_EVENTS",
    "AUTORESEARCH_INVOCATION_SCHEMA_VERSION",
    "acceptance_payload",
    "autoresearch_invocation_projection",
    "build_invocation_request_from_run_manager_event",
    "build_invocation_request_event",
    "handled_invocation_ids",
    "invocation_id_from_payload",
    "rejection_payload",
    "trigger_payload_from_invocation",
    "validate_invocation_request",
]
