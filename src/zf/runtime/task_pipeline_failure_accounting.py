"""Count admitted semantic failures independently of operation identities."""

from typing import Any, Iterable, Mapping


def is_semantic_failure(operation: Mapping[str, Any]) -> bool:
    return (
        operation.get("status") == "settled"
        and str(operation.get("semantic_verdict") or "").lower()
        in {"failed", "blocked", "rejected", "reject", "revise"}
        and str(operation.get("failure_class") or "none").lower()
        in {"none", "semantic", "product"}
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
