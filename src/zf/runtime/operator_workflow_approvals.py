"""Inbox projection of existing version-bound Workflow proposals."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from zf.runtime.operator_inbox_read import put_inbox_item
from zf.runtime.workflow_proposal import WorkflowProposalError, load_workflow_proposal
from zf.runtime.workflow_requests import WorkflowRequestError, hydrate_workflow_requirement


def project_workflow_approval_event(
    state_dir: Path, items: dict[str, dict[str, Any]], event: Any,
) -> bool:
    data = event if isinstance(event, dict) else vars(event)
    event_type = str(data.get("type") or "")
    if not event_type.startswith("workflow.request."):
        return False
    body = data.get("payload") or {}
    request_id = str(body.get("request_id") or "")
    revision = body.get("revision")
    if not request_id or not isinstance(revision, int) or revision < 1:
        return True
    key = f"approval:workflow:{request_id}:r{revision}"
    event_id, timestamp = str(data.get("id") or ""), str(data.get("ts") or "")
    for previous in items.values():
        if (previous.get("owner_route") == "workflow_proposal"
            and previous.get("request_id") == request_id
            and previous.get("revision", 0) < revision
            and previous.get("status") == "pending"):
            previous.update(status="superseded", resolved_event_id=event_id,
                            resolved_ts=timestamp, resolution=f"Replaced by revision {revision}")
    if event_type == "workflow.request.proposed":
        ref = body.get("proposal_ref")
        digest = str(body.get("proposal_digest") or "")
        if not isinstance(ref, dict) or not digest:
            return True
        try:
            proposal = load_workflow_proposal(state_dir, ref)
        except (WorkflowProposalError, OSError, ValueError):
            # No actionable proposal can be claimed from a missing/invalid sidecar.
            # The original Workflow page retains the preparation/error context.
            return True
        if (proposal.get("request_id") != request_id
            or proposal.get("request_revision") != revision
            or proposal.get("proposal_digest") != digest
            or proposal.get("approval_status") != "approvable"):
            return True
        try:
            requirement = hydrate_workflow_requirement(state_dir, body)
        except (WorkflowRequestError, OSError, ValueError):
            return True
        summary = str(requirement.get("objective") or "Review the workflow before execution.")
        put_inbox_item(items, key, {
            "id": key, "kind": "approval", "reviewable": True,
            "owner_route": "workflow_proposal", "status": "pending",
            "title": "Workflow approval", "summary": summary,
            "request_id": request_id, "revision": revision,
            "approval_ref": f"workflow:{request_id}:r{revision}",
            "workflow_proposal_ref": ref, "proposal_digest": digest,
            "source_role": "workflow", "source_actor": str(data.get("actor") or ""),
            "created_event_id": event_id, "created_ts": timestamp,
            "resolved_event_id": "", "resolved_ts": "",
            "deep_link": "?" + urlencode({"page": "workflows", "request": request_id}),
            "deep_link_label": "Review workflow", "actions": [],
        })
    elif key in items:
        item = items[key]
        status = str(body.get("status") or "")
        if status in {"approved", "submitted", "running", "rejected"}:
            item.update(status="rejected" if status == "rejected" else "approved",
                        resolved_event_id=event_id, resolved_ts=timestamp,
                        resolution="Proposal rejected" if status == "rejected" else "Proposal approved")
            if status in {"submitted", "running"}:
                item["execution_status"] = status
    return True
