"""Bind a registered controller revision to its immutable Preview review."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def bound_reviewed_controller_candidate(
    state_dir: Path, request: Mapping[str, Any], base_path: Path, candidate_path: Path,
    synthesis_result_ref: Mapping[str, Any] | None, review_ref: Mapping[str, Any],
) -> Path:
    """Admit only the exact bounded patch of the prior, digest-bound candidate.

    This creates a proposal, not a config approval. Config Apply still requires
    approval of the new proposal and candidate digests before writing zf.yaml.
    """
    from zf.runtime.workflow_preview_review import SCHEMA, WorkflowPreviewReview
    from zf.runtime.workflow_proposal import WorkflowProposalError
    from zf.runtime.workflow_registered_controller import CONTROLLER_PARAMETERS

    def reject() -> None:
        raise WorkflowProposalError("registered controller review binding is invalid")

    draft = hydrate_sidecar_ref(state_dir, review_ref).payload
    if not isinstance(draft, dict) or draft.get("schema_version") != SCHEMA:
        reject()
    binding = draft.get("binding") or {}
    if (binding.get("request_id") != request.get("request_id")
            or binding.get("revision") != int(request.get("revision") or 0) - 1):
        reject()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    if not any(
        event.type == "workflow.request.updated"
        and event.payload.get("request_id") == request["request_id"]
        and event.payload.get("previous_revision") == binding["revision"]
        and event.payload.get("source_event_id") == draft.get("source_event_id")
        for event in writer.event_log.read_all()
    ):
        reject()
    if not draft.get("base_request") or not draft.get("base_proposal_ref"):
        reject()
    review = WorkflowPreviewReview(state_dir, base_path.parent, writer)
    ctx = review.context(binding, _request=draft["base_request"],
                         _proposal_ref=draft["base_proposal_ref"])
    if ctx["config_digest"] != draft.get("config_digest"):
        reject()
    # A review cannot switch controller identity or silently change the project
    # verification commands admitted by synthesis. Existing catalog admission
    # checks the current registered package digest and capability closure.
    def parameters(descriptor: Mapping[str, Any]) -> dict:
        result = hydrate_sidecar_ref(state_dir, descriptor).payload
        spec = hydrate_sidecar_ref(state_dir, result["short_flow_spec_ref"]).payload
        return spec.get("parameters") or {}

    before = parameters(ctx["proposal"]["synthesis_result_ref"])
    after = parameters(synthesis_result_ref or {})
    if (not before.get("controller_id")
            or any(before.get(key) != after.get(key) for key in CONTROLLER_PARAMETERS)):
        reject()
    changes = draft.get("changes") or []
    review._validate_changes(ctx, changes)
    expected = review._candidate_config(
        ctx, changes if changes[0]["field"].startswith("config.") else [],
    )
    try:
        if candidate_path.read_bytes() != expected.read_bytes():
            raise WorkflowProposalError("config candidate does not match the reviewed registered controller")
    finally:
        expected.unlink(missing_ok=True)
    return candidate_path
