"""Workflow fanout anchor helpers.

Workflow invoke creates a kernel-owned root task so the fanout can be traced
through the canonical task store. That root is not an ordinary worker task.
"""

from __future__ import annotations

from zf.core.task.schema import Task


WORKFLOW_INVOKE_BOOTSTRAP_SOURCE = "workflow_invoke_bootstrap"


def mark_workflow_fanout_anchor(
    task: Task,
    *,
    request_id: str = "",
    workflow_input_manifest_ref: str = "",
    pattern_id: str = "",
) -> Task:
    evidence = dict(getattr(task.contract, "evidence_contract", {}) or {})
    evidence.update({
        "source": WORKFLOW_INVOKE_BOOTSTRAP_SOURCE,
        "workflow_fanout_anchor": True,
        "request_id": request_id,
        "workflow_input_manifest_ref": workflow_input_manifest_ref,
        "pattern_id": pattern_id,
    })
    task.contract.evidence_contract = evidence
    return task


def is_workflow_fanout_anchor_task(task: Task) -> bool:
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", {}) if contract else {}
    if not isinstance(evidence, dict):
        return False
    return bool(
        evidence.get("workflow_fanout_anchor") is True
        or str(evidence.get("source") or "") == WORKFLOW_INVOKE_BOOTSTRAP_SOURCE
    )
