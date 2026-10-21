"""Verify the actual role dependency/materialization boundary, not prose scoring."""
import json
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.skills import materialize_role_skills, read_skill_metadata


@pytest.mark.parametrize("role_skill,method,source", [
    ("zf-yoke-planner-role-context", "zf-plan-execution-readiness", "skills"),
    ("zf-yoke-critic-role-context", "zf-plan-execution-readiness", "skills"),
    ("zf-yoke-dev-worker-role-context", "tdd-evidence", "yoke"),
    ("zf-yoke-test-evaluator-role-context", "tdd-evidence", "yoke"),
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
    assert role_skill in entry["dependency_of"]
    actual = state / "workdirs/reader/codex-home/skills" / method / "SKILL.md"
    assert actual.read_bytes() == (root / source / method / "SKILL.md").read_bytes()
    metadata = read_skill_metadata(actual, expected_name=method)
    assert metadata.name == method
    if method == "zf-plan-execution-readiness":
        reference = Path("references/execution-consistency.md")
        assert str(reference) in actual.read_text()
        assert (actual.parent / reference).read_bytes() == (
            root / source / method / reference
        ).read_bytes()


def test_new_method_distribution_matches_canonical_source():
    root = Path(__file__).resolve().parents[1]
    source = (root / "skills/zf-plan-execution-readiness/SKILL.md").read_bytes()
    for provider in (".codex", ".claude"):
        assert (root / provider / "skills/zf-plan-execution-readiness/SKILL.md").read_bytes() == source
        reference = Path("references/execution-consistency.md")
        assert (
            root / provider / "skills/zf-plan-execution-readiness" / reference
        ).read_bytes() == (
            root / "skills/zf-plan-execution-readiness" / reference
        ).read_bytes()
