"""Method delivery and frozen identity checks; not semantic quality evidence."""
from copy import deepcopy
from pathlib import Path
import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.skills import materialize_role_skills
from zf.runtime.channel_reply_prompt import channel_reply_response_contract
from zf.runtime.output_evaluation import EvaluationInput
from zf.runtime.prd_quality_prompt import PRD_AUTHOR_GUIDANCE, prd_review_method


def test_channel_author_receives_complete_method_without_skill_binding():
    prompt = channel_reply_response_contract({}, {}, {"refs": {"synthesis_request_id": "r1"}})
    assert PRD_AUTHOR_GUIDANCE in prompt


def test_method_revision_changes_digest_without_changing_source_or_target(monkeypatch):
    item = EvaluationInput(kind="prd_output", objective="评审", source_text="原始权限要求",
                           target_text="候选设计", context={"channel_id": "ch"})
    current = item.manifest()
    frozen = deepcopy(current)
    old_method = deepcopy(current["review_method"])
    old_method["version"] = "prd-quality-review.v1"
    old_method["criteria"][0]["expected"] = "旧版来源方法"
    monkeypatch.setattr("zf.runtime.output_evaluation.prd_review_method", lambda: old_method)
    historical = item.manifest()
    assert current["review_method"]["version"] == "prd-quality-review.v2"
    assert {r["criterion_id"] for r in current["review_method"]["criteria"]} == {
        f"PRD-Q{i}" for i in range(1, 9)}
    assert historical["review_method_digest"] != current["review_method_digest"]
    assert historical["source"] == current["source"]
    assert historical["target"] == current["target"]
    assert current == frozen
    # A consumer cannot mutate the next evaluation's instructions.
    method = prd_review_method()
    method["criteria"].clear()
    assert len(prd_review_method()["criteria"]) == 8


@pytest.mark.parametrize("role_name", ["planner", "plan-critic"])
def test_prd_role_materializes_design_dependency_and_reference_bytes(tmp_path, role_name):
    root = Path(__file__).resolve().parents[1]
    role = RoleConfig(name=role_name, backend="codex", skills=["zf-prd-plan-synth"])
    config = ZfConfig(project=ProjectConfig(name="method-test"), roles=[role])
    state = tmp_path / "isolated-state"
    state.mkdir()
    materialize_role_skills(config=config, project_root=root, state_dir=state, role=role, task_id="fixture")
    target = state / "workdirs" / role_name / "codex-home" / "skills"
    for name, reference in (
        ("zf-prd-plan-synth", "product-reading-evidence.md"),
        ("zf-plan-execution-readiness", "design-research-handoff.md"),
        ("zf-plan-execution-readiness", "task-spec-slices.md"),
    ):
        relative = Path(name) / "references" / reference
        canonical = root / "skills" / relative
        assert (target / relative).read_bytes() == canonical.read_bytes()
        for distribution in (".codex/skills", ".claude/skills"):
            assert (root / distribution / relative).read_bytes() == canonical.read_bytes()
