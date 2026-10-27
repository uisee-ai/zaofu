"""Resolve immutable candidates for review without admitting a blocked proposal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from zf.runtime.workflow_proposal import load_workflow_proposal


def review_candidate(state_dir: Path, request: dict, events: Iterable[Any]) -> tuple[dict, dict]:
    if request.get("proposal_ref"):
        ref = request["proposal_ref"]
        return load_workflow_proposal(state_dir, ref), ref
    # Prefer a matching prepared candidate. Otherwise recover a revised blocked
    # candidate from the ledger when the preparation projection is still old.
    refs = []
    try:
        root = state_dir.resolve()
        path = (root / "artifacts" / "workflow" / request["request_id"] / "workflow-submit-preview.json").resolve()
        path.relative_to(root)
        refs.append(json.loads(path.read_text()).get("proposal_ref"))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    for event in reversed(list(events)):
        result = event.payload.get("result", {})
        if (event.type == "web.action.completed"
                and event.payload.get("action") == "workflow-preview-revise"
                and isinstance(result, dict)
                and result.get("request_id") == request["request_id"]
                and result.get("revision") == request["revision"]):
            refs.append(result.get("proposal_ref"))
            break
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        proposal = load_workflow_proposal(state_dir, ref)
        if (proposal.get("approval_status") == "blocked"
                and proposal.get("request_id") == request["request_id"]
                and proposal.get("request_revision") == request["revision"]
                and proposal.get("requirement_spec_digest") == request["requirement_spec_digest"]):
            return proposal, ref
    return {}, {}
