"""Resolve explicit delta selection carried by a canonical Task Map amendment."""

from typing import Any


def writer_amend_selection(
    data: dict[str, Any], *, requested_task_ids: list[str], resume_scope: str,
) -> tuple[list[str], str]:
    amend = data.get("amend")
    if not isinstance(amend, dict):
        return requested_task_ids, resume_scope
    scope = resume_scope
    if not requested_task_ids:
        scope = scope or str(amend.get("resume_scope") or "").strip()
    if requested_task_ids or scope != "gap_tasks_only":
        return requested_task_ids, scope
    ids = amend.get("gap_task_ids")
    if not isinstance(ids, list) or not ids or any(
        not isinstance(value, str) or not value.strip() for value in ids
    ):
        raise ValueError("gap_tasks_only amendment requires non-empty gap_task_ids")
    return list(dict.fromkeys(value.strip() for value in ids)), scope
