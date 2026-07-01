"""Signal helpers for ``loop.v1`` projection."""

from __future__ import annotations

import hashlib
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.delivery_projection_common import dedupe

GATE_PREFIXES = ("gate.", "static_gate.", "meta_gate.", "discriminator.")
RECOVERY_EVENTS = {
    "dev.build.done",
    "review.approved",
    "verify.passed",
    "judge.passed",
    "task.done",
    "static_gate.passed",
    "static_gate.skipped",
}


def is_gate_failure(event: ZfEvent) -> bool:
    return event.type.startswith(GATE_PREFIXES) and event.type.endswith((".failed", ".rejected"))


def is_gate_recovery(event: ZfEvent) -> bool:
    return event.type.startswith(GATE_PREFIXES) and event.type.endswith((".passed", ".approved", ".skipped"))


def is_missing_evidence(event: ZfEvent, data: dict[str, Any]) -> bool:
    if event.type != "task.rework.triage.completed":
        return False
    return str(data.get("classification") or data.get("harness_classification") or "") == "evidence_payload_gap"


def is_fanout_retry(event: ZfEvent, data: dict[str, Any]) -> bool:
    if not event.type.startswith("fanout."):
        return False
    if event.type.endswith((".retry_requested", ".retry.started", ".retry.completed")):
        return True
    if data.get("retry_requested") is True or data.get("retry_of_run_id") or data.get("rerun_of"):
        return True
    for key in ("retry_count", "retry_attempt", "attempt"):
        try:
            if int(data.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return str(data.get("status") or "") in {"retrying", "retry_requested"}


def fanout_loop_status(event: ZfEvent, data: dict[str, Any]) -> str:
    status = str(data.get("status") or "")
    if event.type.endswith(".completed") and status in {"completed", "passed", "done"}:
        return "recovered"
    if status in {"failed", "timed_out", "cancelled"}:
        return "exhausted"
    return "open"


def autoresearch_status(event: ZfEvent) -> str:
    if event.type.endswith((".completed", ".validation.passed")):
        return "recovered"
    if event.type.endswith((".failed", ".rejected", ".validation.failed")):
        return "exhausted"
    return "open"


def replan_status(event: ZfEvent, data: dict[str, Any]) -> str:
    decision = str(data.get("decision") or data.get("status") or "")
    if event.type.endswith((".rejected", ".adoption_blocked")) or decision in {"reject", "rejected", "blocked"}:
        return "exhausted"
    if decision in {"adopt", "accepted", "approved"} or event.type.endswith((".accepted", ".approved")):
        return "recovered"
    if event.type.endswith((".completed", ".created", ".requested")):
        return "verifying"
    return "open"


def event_key(kind: str, event: ZfEvent, data: dict[str, Any]) -> str:
    task_key = ",".join(task_ids(event, data))
    feature_key = ",".join(feature_ids(data))
    return f"{kind}:{feature_key}:{task_key}:{event.id}"


def gate_key(event: ZfEvent) -> str:
    family = event.type.rsplit(".", 1)[0]
    return f"gate_failure:{family}:{event.task_id or ''}"


def worker_key(event: ZfEvent, data: dict[str, Any]) -> str:
    worker = str(data.get("worker_id") or data.get("instance_id") or data.get("role_instance") or event.actor or "")
    return f"stuck_worker:{worker}:{event.task_id or data.get('task_id') or ''}"


def fanout_key(event: ZfEvent, data: dict[str, Any]) -> str:
    return f"fanout_retry:{','.join(fanout_ids(data)) or event.id}"


def autoresearch_key(event: ZfEvent, data: dict[str, Any]) -> str:
    run_id = str(data.get("run_id") or data.get("loop_id") or event.correlation_id or "")
    return f"autoresearch:{run_id or event.id}"


def replan_key(event: ZfEvent, data: dict[str, Any]) -> str:
    ref = str(data.get("new_task_map_ref") or data.get("candidate_task_map_ref") or data.get("proposal_ref") or "")
    return f"replan:{ref or event.correlation_id or event.id}"


def task_ids(event: ZfEvent, data: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if event.task_id:
        values.append(str(event.task_id))
    raw = data.get("task_ids")
    if isinstance(raw, list):
        values.extend(str(item) for item in raw if str(item))
    elif data.get("task_id"):
        values.append(str(data.get("task_id")))
    return dedupe(values)


def feature_ids(data: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("feature_id", "pdd_id"):
        if data.get(key):
            values.append(str(data.get(key)))
    return dedupe(values)


def fanout_ids(data: dict[str, Any]) -> list[str]:
    raw = data.get("fanout_ids")
    values = [str(item) for item in raw] if isinstance(raw, list) else []
    if data.get("fanout_id"):
        values.append(str(data.get("fanout_id")))
    return dedupe(values)


def trace_ids(event: ZfEvent, data: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if event.correlation_id:
        values.append(str(event.correlation_id))
    if data.get("trace_id"):
        values.append(str(data.get("trace_id")))
    return dedupe(values)


def signal_summary(data: dict[str, Any], fallback: str) -> str:
    return str(data.get("summary") or data.get("reason") or data.get("message") or fallback)


def suggested_action(source_kind: str) -> str:
    return {
        "gate_failure": "review_gate_evidence",
        "missing_evidence": "harden_evidence_contract",
        "rework": "inspect_rework_route",
        "stuck_worker": "inspect_worker_liveness",
        "fanout_retry": "review_fanout_barrier",
        "autoresearch": "review_autoresearch_result",
        "replan": "review_replan_contract",
    }.get(source_kind, "inspect_loop")


def stable_id(*parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
