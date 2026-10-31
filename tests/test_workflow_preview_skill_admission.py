"""A Preview must reject skill bindings that would make zf start fail."""
import pytest

from tests.test_workflow_registered_controller import assembly  # noqa: F401
from tests.test_workflow_review_controller import preview, draft_for  # noqa: F401
from zf.runtime.workflow_preview_review import PreviewReviewError
from zf.runtime.workflow_config_apply import WorkflowConfigApplyService, WorkflowConfigApplyError
from zf.runtime.workflow_proposal import load_workflow_proposal


def test_preview_rejects_installed_skill_with_incompatible_stage(preview):
    review, binding = preview
    original = review.config_path.read_bytes()
    with pytest.raises(PreviewReviewError, match="preflight"):
        draft_for(review, binding, ".roleSkillBundles.impl", [
            "zf-yoke-dev-worker-role-context", "zf-requirement-reconciliation",
        ])
    assert review.config_path.read_bytes() == original
    assert not any(e.type == "workflow.invoke.requested" for e in review.writer.event_log.read_all())


def test_config_apply_rechecks_skill_admission_before_writing(preview, monkeypatch):
    review, binding = preview
    result = review.apply(draft_for(review, binding))
    proposal = load_workflow_proposal(review.state_dir, result["proposal_ref"])
    original = review.config_path.read_bytes()
    monkeypatch.setattr("zf.core.skills.validate_skill_sources", lambda **kwargs: [
        "role 'dev-lane-0' skill visibility.stages does not overlap role.stages ('impl',)",
    ])
    service = WorkflowConfigApplyService(state_dir=review.state_dir, project_root=review.project_root)
    with pytest.raises(WorkflowConfigApplyError, match="skill validation failed"):
        service.apply({"proposal_ref": result["proposal_ref"], "proposal_digest": result["proposal_digest"],
                       "validation_result_ref": proposal["validation_result_ref"], "approval_ref": "owner-approved"})
    assert review.config_path.read_bytes() == original
