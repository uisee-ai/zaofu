from dataclasses import asdict

import pytest

from tests.test_workflow_registered_controller import assembly  # noqa: F401
from tests.test_workflow_review_controller import preview, Agent  # noqa: F401
from zf.core.config.loader import load_config
from zf.runtime.workflow_config_apply import WorkflowConfigApplyService
from zf.runtime.workflow_preview_review import PreviewReviewError
from zf.runtime.workflow_proposal import load_workflow_proposal


def test_per_role_review_preserves_behavior_and_independent_models(preview):
    review, binding = preview
    ctx = review.context(binding)
    service = WorkflowConfigApplyService(state_dir=review.state_dir, project_root=review.project_root)
    before = load_config(service._private_candidate(ctx["proposal"]))
    models = {"planner": ("gpt-6-astra", "xhigh"), "dev-lane-0": ("gpt-6.1-terra", "xhigh"),
              "verify-lane-0": ("gpt-6.1-sol", "high"), "judge-prd": ("gpt-6.1-sol", "high")}
    patches = []
    for name, values in models.items():
        for key, value in zip(("model", "model_reasoning_effort"), values):
            field = next(f for f in ctx["fields"] if f.endswith(f".roles.{name}.{key}"))
            patches.append({"field": field, "replacement": value})
    original = review.config_path.read_bytes()
    response = review.discuss({**binding, "field": patches[0]["field"], "mode": "chat",
        "message": "Apply the owner's distinct per-role models"},
        agent=Agent({"answer": "Review these exact role changes", "changes": patches}))
    revised = review.apply(response["draft_ref"])
    proposal = load_workflow_proposal(review.state_dir, revised["proposal_ref"])
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    after = load_config(service._private_candidate(proposal))
    expected = {r.name: asdict(r) for r in before.roles}
    for name, (model, effort) in models.items():
        expected[name].update(model=model, model_reasoning_effort=effort)
    assert {r.name: asdict(r) for r in after.roles} == expected
    after_workflow, before_workflow = asdict(after.workflow), asdict(before.workflow)
    # Provenance records the new explicit overrides; the executable workflow is unchanged.
    after_workflow.pop("pipelines_role_meta")
    before_workflow.pop("pipelines_role_meta")
    assert after_workflow == before_workflow
    assert review.config_path.read_bytes() == original
    assert not any(e.type == "workflow.invoke.requested" for e in review.writer.event_log.read_all())


@pytest.mark.parametrize("suffix", ["roles.unknown.model", "roles.planner.permission_mode"])
def test_role_review_cannot_create_roles_or_edit_permissions(preview, suffix):
    review, binding = preview
    ctx = review.context(binding)
    known = next(f for f in ctx["fields"] if f.endswith(".roles.planner.model"))
    field = ".".join(known.split(".")[:2]) + "." + suffix
    with pytest.raises(PreviewReviewError, match="Unknown"):
        review.discuss({**binding, "field": known, "mode": "chat", "message": "Invalid role change"},
            agent=Agent({"answer": "Rejected change", "changes": [{"field": field, "replacement": "x"}]}))
