"""A bounded view of the admitted report for the authorized, current Reader incident."""
from pathlib import Path

from zf.core.security.redaction import redact_text
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def _excerpt(value, limit):
    if not isinstance(value, str):
        return ""
    cleaned = redact_text(value)
    return cleaned if len(cleaned) <= limit else cleaned[:limit - 1] + "…"


def worker_failure_evidence(state: Path, events, option: dict) -> dict:
    """The option identifies the retry; the independently bound report describes the failure."""
    if option.get("binding_preflight") != "passed" or option.get("human_gate_status") != "clear":
        return {}
    target = option["recommendation"]["action_payload"]
    op = reduce_workflow_operations(events).get(target.get("target_operation_id"), {})
    if (op.get("status") != "settled" or op.get("operation_type") != "fanout_reader_child"
            or op.get("semantic_verdict") != "abstained"
            or any(not op.get(k) or target.get(k) != op[k]
                   for k in ("workflow_run_id", "task_id", "active_attempt_id"))):
        return {}
    by_id = {e.id: e for e in events}
    failed = by_id.get(option.get("failure_event_id"))
    admitted = by_id.get(op.get("call_result_admitted_event_id"))
    ref = op.get("admitted_control_result_ref") or {}
    if (not failed or not admitted or not ref.get("ref") or not ref.get("sha256")
            or ref.get("kind") != "call_control_result" or failed.origin != "kernel"
            or failed.type != "fanout.child.failed" or failed.id != target.get("source_event_id")
            or admitted.type != "workflow.call.result.admitted" or admitted.origin != "kernel"
            or any(e.payload.get("control_result_ref") != ref or e.payload.get("semantic_verdict") != "abstained"
                   or any(e.payload.get(k) != op[k] for k in ("workflow_run_id", "operation_id", "request_hash"))
                   for e in (admitted, failed))
            or admitted.payload.get("attempt_id") != op["active_attempt_id"]):
        return {}
    try:
        result = hydrate_sidecar_ref(state, ref).payload
    except (KeyError, OSError, ValueError):
        return {}
    if (not isinstance(result, dict) or result.get("execution_status") != "failed"
            or result.get("verdict") != "abstained" or result.get("attempt_id") != op["active_attempt_id"]
            or result.get("schema_version") not in {"workflow-read-result.v1", "verification-result.v1"}
            or result["schema_version"] != admitted.payload.get("control_result_schema")
            or any(result.get(k) != op[k] for k in ("workflow_run_id", "operation_id", "request_hash", "task_id"))
            or result.get("role_instance") != op["role_instance"]):
        return {}
    rows = result.get("findings") or []
    findings = []
    for row in rows[:4] if isinstance(rows, list) else []:
        if isinstance(row, dict):
            findings.append({k: _excerpt(row[k], n) for k, n in (
                ("code", 100), ("severity", 32), ("field", 180), ("message", 500),
            ) if isinstance(row.get(k), str)})
    return {
        "source_status": "admitted_worker_report",
        "failure_event_id": failed.id, "admission_event_id": admitted.id,
        "operation_id": op["operation_id"], "attempt_id": op["active_attempt_id"],
        "original_result": {"ref": ref["ref"], "sha256": ref["sha256"]},
        "failure_class": _excerpt(result.get("failure_class"), 100),
        "reason": _excerpt(failed.payload.get("reason"), 240),
        "summary": _excerpt(result.get("summary"), 600),
        "findings": findings, "finding_count": len(rows) if isinstance(rows, list) else 0,
        "preflight": {"binding": option["binding_preflight"], "human_gate": option["human_gate_status"],
                      "product_validation": "not_established"},
    }
