"""Count admitted semantic failures independently of operation identities."""

from typing import Any, Iterable, Mapping


def is_semantic_failure(operation: Mapping[str, Any]) -> bool:
    return (
        operation.get("status") == "settled"
        and str(operation.get("semantic_verdict") or "").lower()
        in {"failed", "blocked", "rejected", "reject", "revise"}
        and str(operation.get("failure_class") or "none").lower()
        in {"none", "semantic", "product", "product_rejection"}
    )


def semantic_failures(operations: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    rows = []
    for row in operations:
        if not is_semantic_failure(row):
            continue
        identity = str(row.get("call_result_admitted_event_id") or row.get("operation_id") or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        rows.append(row)
    return rows


def retry_budget_exhausted(state_dir, events, operations, operation, contexts, policy, config) -> bool:
    """Use admitted failures, never identity generations, to bound retry."""
    from zf.runtime.task_pipeline_execution_status import normalize_task_pipeline_operation_semantics
    from zf.runtime.recovery_association import inherited_failure_operations

    rows = normalize_task_pipeline_operation_semantics(state_dir, [
        row for row in operations if all(row.get(k) == operation.get(k)
            for k in ("task_id", "workflow_run_id", "task_map_generation"))
    ])
    rows.extend(inherited_failure_operations(state_dir, events, contexts).get(operation["task_id"], []))
    failures = semantic_failures(rows)
    limit = int(policy.get("max_rework_attempts") or 0)
    if len(failures) > limit:
        return True
    target = next(row for row in rows if row["operation_id"] == operation["operation_id"])
    if target.get("failure_class") == "host_publication_required":
        publication = getattr(getattr(config, "runtime", None), "task_publication", None)
        # A worker's failure label grants no host authority or free retry.
        if not publication or not publication.enabled:
            return True
        from dataclasses import asdict
        from zf.runtime.task_host_manifest import digest
        approval = publication.approval_ref + "#" + digest(asdict(publication))
        receipts = [e for e in events if e.type == "runtime.action.attempt.completed"
            and e.origin in {"", "kernel"} and e.payload.get("action") == "task-host-assist"
            and e.payload.get("kind") == "commit"
            and e.payload.get("operation_id") == operation["operation_id"]
            and e.payload.get("approval_ref") == approval]
        used = sum(e.type == "runtime.action.attempt.started"
            and e.payload.get("action") == "task-host-assist" and e.payload.get("kind") == "commit"
            and e.payload.get("workflow_run_id") == operation["workflow_run_id"] for e in events)
        if len(receipts) != 1 or used > publication.max_publications_per_run:
            return True
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        try:
            receipt = hydrate_sidecar_ref(state_dir, receipts[0].payload["receipt_ref"]).payload
            return (receipt.get("status") != "completed" or receipt.get("approval_ref") != approval
                or receipt.get("kind") != "commit" or not receipt.get("source_commit")
                or any(receipt.get(key) != operation.get(key) for key in (
                    "operation_id", "task_id", "workflow_run_id", "task_map_generation", "operation_generation")))
        except (KeyError, OSError, ValueError, TypeError):
            return True
    infrastructure = {row["operation_id"] for row in rows if row.get("status") == "settled"
        and row.get("semantic_verdict") in {"blocked", "failed", "rejected"}
        and not is_semantic_failure(row) and row.get("failure_class") != "host_publication_required"}
    return len(infrastructure) > limit
