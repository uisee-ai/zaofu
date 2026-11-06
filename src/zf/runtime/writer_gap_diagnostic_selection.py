"""Separate a prior-generation diagnosis from the approved amendment delta."""

from collections.abc import Mapping
from typing import Any


def select_gap_from_diagnostic_scope(
    data: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    requested_task_ids: list[str],
    resume_scope: str,
) -> tuple[list[str], str]:
    amend = data.get("amend")
    if (
        not isinstance(amend, Mapping)
        or amend.get("schema_version") != "task-map-amend.v1"
        or amend.get("resume_scope") != "gap_tasks_only"
        or resume_scope not in {
            "gap_tasks_only", "failed_children_only", "failed_children_and_downstream",
        }
    ):
        return requested_task_ids, resume_scope

    def ids(value: Any) -> set[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            return set()
        return set(value)

    failed = ids(payload.get("failed_task_ids"))
    diagnosis = failed | ids(payload.get("downstream_task_ids", []))
    superseded = ids(amend.get("superseded_task_ids"))
    # An explicit selection of new tasks (or an unrelated operator selection)
    # is not a diagnosis and must keep its existing admission behavior.
    if (
        not failed
        or not failed <= superseded <= diagnosis
        or set(requested_task_ids) != diagnosis
    ):
        return requested_task_ids, resume_scope
    # A bounded amendment may also replace a diagnosed downstream consumer
    # to rebind its prerequisite. Neither unrelated tasks nor an omitted
    # failed task establish authority to replace the diagnostic selector.
    gap_ids = amend.get("gap_task_ids")
    if not ids(gap_ids) or len(gap_ids) != len(set(gap_ids)):
        raise RuntimeError("gap_tasks_only amendment requires unique non-empty gap_task_ids")
    return list(gap_ids), "gap_tasks_only"
