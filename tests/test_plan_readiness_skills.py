"""Verify the actual role dependency/materialization boundary, not prose scoring."""
import json
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.skills import materialize_role_skills, read_skill_metadata


@pytest.mark.parametrize("role_skill,method,source", [
    ("zf-yoke-planner-role-context", "zf-plan-execution-readiness", "skills"),
    ("zf-yoke-planner-role-context", "zf-plan-task-map-contract", "skills"),
    ("zf-yoke-critic-role-context", "zf-plan-execution-readiness", "skills"),
    ("zf-yoke-dev-worker-role-context", "tdd-evidence", "yoke"),
    ("zf-yoke-dev-worker-role-context", "zf-harness-done-contract", "skills"),
    ("zf-yoke-test-evaluator-role-context", "tdd-evidence", "yoke"),
    ("zf-yoke-test-evaluator-role-context", "verify-review", "yoke"),
    ("zf-channel-discussion-synthesizer", "zf-requirement-reconciliation", "skills"),
    ("zf-yoke-planner-role-context", "zf-requirement-reconciliation", "skills"),
    ("zf-yoke-critic-role-context", "zf-requirement-reconciliation", "skills"),
])
def test_active_roles_receive_current_review_and_evidence_methods(tmp_path, role_skill, method, source):
    root = Path(__file__).resolve().parents[1]
    state = tmp_path / "runtime"
    state.mkdir()
    role = RoleConfig(name="reader", backend="codex", skills=[role_skill])
    config = ZfConfig(project=ProjectConfig(name="readiness"), roles=[role])
    result = materialize_role_skills(config=config, project_root=root, state_dir=state, role=role)
    assert result is not None
    manifest = json.loads((state / "workdirs/reader/runtime/skills-manifest.json").read_text())
    entry = next(row for row in manifest["skills"] if row["name"] == method)
    assert entry["dependency_of"]
    if method != "zf-requirement-reconciliation":
        assert role_skill in entry["dependency_of"]
    actual = state / "workdirs/reader/codex-home/skills" / method / "SKILL.md"
    assert actual.read_bytes() == (root / source / method / "SKILL.md").read_bytes()
    metadata = read_skill_metadata(actual, expected_name=method)
    assert metadata.name == method
    references = {
        "zf-plan-task-map-contract": ("review-ports.md",),
        "zf-plan-execution-readiness": ("execution-consistency.md", "human-review.md", "epic-refinement.md",
                                        "bounded-spike.md", "cross-domain-obligations.md", "early-feedback.md"),
        "tdd-evidence": ("cycle-evidence.md", "rework-preservation.md"),
        "verify-review": ("regression-and-integration.md", "independent-findings.md"),
    }
    if method in references:
        for name in references[method]:
            reference = Path("references") / name
            assert str(reference) in actual.read_text()
            assert (actual.parent / reference).read_bytes() == (
                root / source / method / reference
            ).read_bytes()


def test_new_method_distribution_matches_canonical_source():
    root = Path(__file__).resolve().parents[1]
    source = (root / "skills/zf-plan-execution-readiness/SKILL.md").read_bytes()
    for provider in (".codex", ".claude"):
        assert (root / provider / "skills/zf-plan-execution-readiness/SKILL.md").read_bytes() == source
        for name in ("execution-consistency.md", "human-review.md", "epic-refinement.md", "epic-plan-example.json"):
            reference = Path("references") / name
            assert (
                root / provider / "skills/zf-plan-execution-readiness" / reference
            ).read_bytes() == (
                root / "skills/zf-plan-execution-readiness" / reference
            ).read_bytes()


@pytest.mark.parametrize("name", ["zf-channel-discussion-synthesizer", "zf-requirement-reconciliation"])
def test_reconciliation_distribution_matches_source(name):
    root = Path(__file__).resolve().parents[1]
    for provider in (".codex", ".claude"):
        assert (root / provider / "skills" / name / "SKILL.md").read_bytes() == (
            root / "skills" / name / "SKILL.md"
        ).read_bytes()


def test_epic_draft_preserves_future_goal_in_task_reads_and_rejects_dispatch_schema():
    from zf.runtime.artifact_read_ledger import _select_json_path
    from zf.runtime.artifact_read_requirements import build_canonical_required_reads
    from zf.runtime.task_scoped_plan_inputs import task_input_paths

    root = Path(__file__).resolve().parents[1]
    body = json.loads((root / "skills/zf-plan-execution-readiness/references/epic-plan-example.json").read_text())
    paths = task_input_paths(body, task_id="vehicle-read")
    selected = [_select_json_path(body, path) for path in paths]
    assert selected[0] == body["shared"]
    assert selected[1]["body"]["acceptance_ids"] == ["AC-LIST", "AC-ERROR"]
    assert "AC-NOTIFY" in selected[0]["unfulfilled_acceptance_ids"]
    assert len(selected[0]["open_questions"]) == 2
    for profile in ("implementation", "task-verify"):
        reads = build_canonical_required_reads({"sources": [{
            "source_id": "plan-port-execution_spec", "artifact_id": "execution_spec",
            "sha256": "a" * 64, "kind": "task_scoped_plan_input", "allowed_paths": paths,
        }]}, output_profile_id=profile)
        assert [r["json_path"] for r in reads] == ["$.shared", "$.tasks[0]"]
        assert all(not r["allow_truncated"] for r in reads)
    assert task_input_paths(body, task_id="", candidate=True) == ["$"]
    with pytest.raises(ValueError, match="no partition"):
        task_input_paths(body, task_id="vehicle-notify")
    # Reading a planning example must not turn its partial scope into an executable map.
    from zf.runtime.task_map import validate_task_map_payload

    validation = validate_task_map_payload(body)
    assert not validation.passed
    assert "unsupported schema_version 'task-scoped-plan-input.v1'" in validation.errors


def test_human_review_example_reaches_existing_task_scoped_reader():
    from zf.runtime.task_scoped_plan_inputs import task_input_paths

    root = Path(__file__).resolve().parents[1]
    method = (root / "skills/zf-plan-execution-readiness/references/human-review.md").read_text()
    body = json.loads(method.split("```json\n", 1)[1].split("```", 1)[0])
    task_id = body["tasks"][0]["task_id"]
    assert task_input_paths(body, task_id=task_id) == ["$.shared", "$.tasks[0]"]
    assert task_input_paths(body, task_id=task_id, candidate=True) == ["$"]
    assert body["tasks"][0]["body"]["human_review"]["scenarios"][0]["acceptance_ids"]


@pytest.mark.parametrize("name", ["zf-plan-execution-readiness", "zf-workflow-start-planner", "zf-harness-self-improve"])
def test_operator_methods_keep_references_in_provider_materialization(tmp_path, name):
    root = Path(__file__).resolve().parents[1]
    source = root / "skills" / name
    role = RoleConfig(name="operator", backend="codex", skills=[name])
    state = tmp_path / "runtime"
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name="methods"), roles=[role])
    materialize_role_skills(config=config, project_root=root, state_dir=state, role=role)
    actual = state / "workdirs/operator/codex-home/skills" / name
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        assert (actual / relative).read_bytes() == path.read_bytes()
        for provider in (".claude", ".codex"):
            assert (root / provider / "skills" / name / relative).read_bytes() == path.read_bytes()
