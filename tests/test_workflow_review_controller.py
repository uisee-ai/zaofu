"""Registered controller Raw edits retain provenance and owner approval gates."""

import pytest
import yaml

from tests.test_control_actions_workflow_request import _execute
from tests.test_workflow_preview_review import Agent
from tests.test_workflow_registered_controller import assembly  # noqa: F401
from zf.core.config.loader import load_config
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_config_apply import WorkflowConfigApplyService
from zf.runtime.workflow_preview_review import WorkflowPreviewReview, PreviewReviewError
from zf.runtime.workflow_proposal import build_workflow_proposal, load_workflow_proposal, WorkflowProposalError
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations


@pytest.fixture
def preview(assembly):
    base, state, _, result, writer = assembly
    service = ControlledActionService(state, writer, config=load_config(base), project_root=base.parent,
                                     actor="operator", source="kanban-agent", surface="web")
    queued = _execute(service, "workflow-request", {
        "request_id": "v5-raw", "kind": "auto", "objective": "Deliver Hello with V5",
        "source_ref": "prd.md", "target_root": ".", "backend": "mock", "synthesis_backend": "mock",
        "allow_missing_env": True, "acceptance": ["Hello and unittest pass"],
    })
    assert queued["ok"], queued
    request = load_workflow_request(state, queued["request_id"])
    reply = {**result, "request_id": request["request_id"], "request_revision": request["revision"],
             "requirement_ref": request["requirement_spec_ref"], "requirement_digest": request["requirement_spec_digest"]}
    assert consume_workflow_synthesis_operations(
        state_dir=state, project_root=base.parent, config=service.config, writer=writer,
        agent=Agent(reply), limit=1) == 1
    request = load_workflow_request(state, request["request_id"])
    assert request["status"] == "proposed", request
    binding = {key: request[key] for key in ("request_id", "revision", "proposal_digest")}
    return WorkflowPreviewReview(state, base.parent, writer), binding


def draft_for(review, binding, suffix=".roleDefaults.model", replacement="gpt-6.1-terra"):
    field = next(key for key in review.context(binding)["fields"] if key.endswith(suffix))
    return review.discuss({**binding, "field": field, "mode": "revise", "message": "Change this field only"},
                          agent=Agent({"answer": "Review the exact change", "replacement": replacement}))["draft_ref"]


@pytest.mark.parametrize("suffix,replacement", [(".roleDefaults.model", "gpt-6.1-terra"), (".lanes", 1)])
def test_reviewed_controller_edit_requires_exact_config_approval(preview, suffix, replacement):
    review, binding = preview
    original = review.config_path.read_bytes()
    draft = draft_for(review, binding, suffix, replacement)
    result = review.apply(draft)
    proposal = load_workflow_proposal(review.state_dir, result["proposal_ref"])
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    assert proposal["compiler_inputs"]["preview_review_ref"] == draft
    assert result["revision"] == binding["revision"] + 1
    assert review.config_path.read_bytes() == original
    service = WorkflowConfigApplyService(state_dir=review.state_dir, project_root=review.project_root)
    candidate = service._private_candidate(proposal)
    docs = list(yaml.safe_load_all(candidate.read_text()))
    flow = next(d["spec"] for d in docs if d["kind"] == "PrdFlow")
    assert (flow["lanes"] if suffix == ".lanes" else flow["roleDefaults"]["model"]) == replacement
    cfg = load_config(candidate)
    assert cfg.workflow.run_limits.token_budget == 0
    assert cfg.workflow.execution_profiles["bounded-direct-v1"].limits.token_budget == 0
    assert cfg.workflow.orchestration.normal_frontier_owner == "wrc"
    assert cfg.workflow.flow_metadata["task_pipeline"]["mode"] == "blocking"
    assert review.apply(draft)["replayed"] is True
    applied = service.apply({"proposal_ref": result["proposal_ref"], "proposal_digest": result["proposal_digest"],
                            "validation_result_ref": proposal["validation_result_ref"], "approval_ref": "owner-approved"})
    assert applied.status == "applied"
    assert not any(e.type == "workflow.invoke.requested" for e in review.writer.event_log.read_all())


def test_candidate_tamper_is_rejected_and_same_review_resumes_once(preview, monkeypatch):
    import zf.runtime.workflow_preview_review as module
    review, binding = preview
    original = review.config_path.read_bytes()
    draft = draft_for(review, binding)

    def tamper(*args, **kwargs):
        path = kwargs["candidate_config_path"]
        path.write_text(path.read_text().replace("normal_frontier_owner: wrc", "normal_frontier_owner: kernel"))
        return build_workflow_proposal(*args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(module, "build_workflow_proposal", tamper)
        with pytest.raises(WorkflowProposalError, match="does not match the reviewed"):
            review.apply(draft)
    assert review.config_path.read_bytes() == original
    assert load_workflow_request(review.state_dir, binding["request_id"])["revision"] == binding["revision"] + 1
    result = review.apply(draft)
    assert result["revision"] == binding["revision"] + 1
    assert review.apply(draft)["replayed"]


def test_consecutive_reviews_preserve_prior_model_edit_and_reject_stale_draft(preview):
    review, binding = preview
    stale = draft_for(review, binding, ".lanes", 2)
    result = review.apply(draft_for(review, binding))
    with pytest.raises(PreviewReviewError, match="changed"):
        review.apply(stale)
    next_binding = {key: result[key] for key in ("request_id", "revision", "proposal_digest")}
    revised = review.apply(draft_for(review, next_binding, ".lanes", 1))
    proposal = load_workflow_proposal(review.state_dir, revised["proposal_ref"])
    candidate = WorkflowConfigApplyService(state_dir=review.state_dir, project_root=review.project_root)._private_candidate(proposal)
    flow = next(d["spec"] for d in yaml.safe_load_all(candidate.read_text()) if d["kind"] == "PrdFlow")
    assert flow["roleDefaults"]["model"] == "gpt-6.1-terra"
    assert flow["lanes"] == 1


def test_review_descriptor_digest_cannot_be_forged(preview):
    review, binding = preview
    draft = draft_for(review, binding)
    with pytest.raises(ValueError):
        review.apply({**draft, "sha256": "0" * 64})
    assert load_workflow_request(review.state_dir, binding["request_id"])["revision"] == binding["revision"]


def test_review_still_rechecks_registered_package_at_compile_time(preview, monkeypatch):
    import zf.runtime.workflow_registered_controller as controller
    review, binding = preview
    draft = draft_for(review, binding)
    carry = review._carry_synthesis
    catalog = controller.controller_catalog

    def change_after_synthesis(*args):
        result = carry(*args)
        changed = catalog()
        for entry in changed.values():
            entry["digest"] = "0" * 64
        monkeypatch.setattr(controller, "controller_catalog", lambda: changed)
        return result

    monkeypatch.setattr(review, "_carry_synthesis", change_after_synthesis)
    with pytest.raises(ValueError, match="stale"):
        review.apply(draft)
    assert not any(e.type == "web.action.completed" and e.payload.get("preview_review_digest") == draft["sha256"]
                   for e in review.writer.event_log.read_all())
