"""Bad writer owners fail before Critic/approval, including pinned submit."""
from types import SimpleNamespace

import pytest

from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.plan_candidate_submit_validation import validate_plan_candidate_semantic
from zf.runtime.plan_execution_review import plan_candidate_writer_policy
from zf.core.config.schema import RoleConfig
from tests.test_plan_candidate_preflight import _source_index, _task_map


@pytest.mark.parametrize("owner,code", [
    ("prd-dev", ""), ("invented-feature-boundary", "flow_owner_instance_unknown"),
    ("issue-dev", "flow_owner_cross_flow"),
])
def test_owner_uses_flow_closure_in_preflight_and_pinned_submit(tmp_path, owner, code):
    config = SimpleNamespace(roles=[RoleConfig(
        name=f"{kind}-dev", instance_id=f"{kind}-dev", role_kind="writer", flow_kind=kind,
    ) for kind in ("prd", "issue")])
    policy = plan_candidate_writer_policy(config)
    task_map = _task_map()
    task_map["tasks"][0]["owner_instance"] = owner
    semantic = {"task_map": task_map, "source_index": _source_index(),
                "source_index_ref": "inline:source-index", "plan_md": "# Plan"}
    validation = {
        "manifest": {"trigger_payload": {"flow_kind": "prd", "prd_ref": "requirements.md"}},
        "metadata": {"flow_kind": "prd"}, "writer_policy": policy,
    }
    preflight = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / "state", project_root=tmp_path,
        reports=[{"report": semantic}], **validation,
    )
    # Submit must use the pinned policy even after the live config changes.
    config.roles.clear()
    submitted = validate_plan_candidate_semantic(
        state_dir=tmp_path / "state", project_root=tmp_path, profile_id="workflow-read",
        request={"plan_candidate_validation": validation}, semantic=semantic,
    )
    for result in (preflight, submitted):
        assert result["status"] == ("failed" if code else "passed"), result["errors"]
        if code:
            assert code in {error["code"] for error in result["errors"]}
