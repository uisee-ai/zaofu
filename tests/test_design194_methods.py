"""The 194 method additions must reach shipped profiles and worker skill input."""

from pathlib import Path
import shutil

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills
from zf.runtime.skill_briefing import render_skill_briefing_section


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suffix", ["", "-claude"])
def test_document_review_uses_existing_generic_workflow(suffix):
    config = load_config(ROOT / f"examples/prod/controller/general-workflow-v3{suffix}.yaml")
    stages = {s.id: s for s in config.workflow.stages}
    assert len(stages) == 5
    assert stages["scope"].trigger == "workflow.invoke.requested"
    assert stages["synthesize"].dependency_events == ["collect-a.completed", "collect-b.completed"]
    assert stages["verify"].operation == "agent.verify"
    assert stages["verify"].roles != stages["synthesize"].roles
    for role in config.roles:
        if role.name.startswith("general-"):
            assert role.role_kind == "reader"
            assert "zf-evidence-synthesis" in role.skills


@pytest.mark.parametrize("name", ["zf-evidence-synthesis", "zf-yoke-orchestrator-role-context"])
@pytest.mark.parametrize("backend", ["codex", "claude-code"])
def test_required_method_survives_distribution_and_runtime_materialization(tmp_path, name, backend):
    source = ROOT / "skills" / name
    for dist in (".codex/skills", ".claude/skills"):
        assert (ROOT / dist / name / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
    shutil.copytree(source, tmp_path / "skills" / name)
    role = RoleConfig(name="method-worker", backend=backend, role_kind="reader", skills=[name])
    config = ZfConfig(project=ProjectConfig(state_dir="state-custom"), roles=[role])
    config.runtime.skills.materialize = "copy"
    result = materialize_role_skills(
        config=config, project_root=tmp_path, state_dir=tmp_path / "state-custom",
        role=role, task_id="method-fixture",
    )
    assert result is not None
    entry = next(e for e in result.skills if e.name == name)
    assert entry.status == "resolved" and entry.auto_inject
    target = result.materialized_paths_under(tmp_path)[name]
    assert (target / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
    briefing = "\n".join(render_skill_briefing_section(role, list(result.skills)))
    assert f"/{name}" in briefing
    assert not (tmp_path / ".zf").exists()
