"""Policy for enabling typed task-contract handoff at dispatch."""

from __future__ import annotations

from typing import Any, Mapping


def typed_task_contract_handoff_enabled(
    config: Any,
    task_item: Mapping[str, Any],
) -> bool:
    if str(task_item.get("contract_snapshot_ref") or "").strip():
        return True

    from zf.runtime.call_result_admission import result_protocol_mode

    task_payload = task_item.get("payload")
    payload = task_payload if isinstance(task_payload, dict) else task_item
    if result_protocol_mode(config, payload) != "shadow":
        return True

    from zf.core.verification.event_schema import event_schemas_for_config

    schemas = event_schemas_for_config(config, payload=payload)
    if not isinstance(schemas, dict):
        return False
    for event_type in (
        "verify.child.completed",
        "review.child.completed",
        "judge.child.completed",
    ):
        rule = schemas.get(event_type)
        if (
            isinstance(rule, dict)
            and "verification_result" in (rule.get("required") or [])
        ):
            return True
    return False


__all__ = ["typed_task_contract_handoff_enabled"]
