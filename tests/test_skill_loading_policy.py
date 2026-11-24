import json

import pytest

from zf.core.config.loader import _build_role
from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.skills import build_skill_lock_entries, materialize_role_skills
from zf.runtime.skill_briefing import render_skill_briefing_section
from zf.runtime.skill_dispatch_treatment import freeze_skill_dispatch_treatment


def test_role_policy_reaches_both_clients_and_preserves_frozen_dispatch(tmp_path):
    source = tmp_path / "skills/method/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "---\nname: method\ndescription: review method\nauto_inject: true\nload_on_demand: false\n---\n# Review\n"
    )
    state = tmp_path / "state"
    frozen = []
    for backend in ("codex", "claude-code"):
        for policy in ({}, {"method": "on_demand"}, {"method": "required"}):
            role = _build_role(
                {
                    "name": "verify",
                    "backend": backend,
                    "skills": ["method"],
                    "skill_loading": policy,
                }
            )
            config = ZfConfig(project=ProjectConfig(name="demo"), roles=[role])
            args = dict(
                role=role,
                config=config,
                project_root=tmp_path,
                state_dir=state,
                task_id="T",
            )
            result = materialize_role_skills(**args)
            locks = build_skill_lock_entries(**args)
            required = policy.get("method") != "on_demand"
            assert result.skills[0].auto_inject is locks[0].auto_inject is required
            assert (
                "Auto-Injected Skills (Required)"
                in "\n".join(render_skill_briefing_section(role, locks))
            ) is required
            ref = freeze_skill_dispatch_treatment(
                state_dir=state,
                role_instance=role.instance_id,
                task_id="T",
                run_id="r",
                selected_overlays=(),
                lock_entries=locks,
                manifest_payload=result.to_payload(),
            )
            frozen.append((state / ref["ref"], required))
    for path, required in frozen:
        row = json.loads(path.read_text())["materialized_skills"][0]
        assert row["auto_inject"] is required and row["load_on_demand"] is not required


@pytest.mark.parametrize(
    "policy", [{"unknown": "required"}, {"method": "sometimes"}, ["method"]]
)
def test_bad_loading_policy_rejected(policy):
    with pytest.raises(ValueError, match="skill_loading"):
        _build_role({"name": "verify", "skills": ["method"], "skill_loading": policy})


def test_prd_flow_limits_the_pilot_to_verify_lanes(tmp_path):
    from zf.core.config.loader import load_config

    path = tmp_path / "zf.yaml"
    path.write_text("""apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: demo}
spec:
  lanes: 2
  backend: mock
  roleSkillBundles: {verify: [method], impl: [method]}
  roleDefaults:
    skillLoadingByStage:
      verify: {method: on_demand}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec: {version: "1.0", project: {name: demo}}
""")
    roles = {r.name: r for r in load_config(path).roles}
    assert all(
        roles[f"verify-lane-{i}"].skill_loading == {"method": "on_demand"}
        for i in (0, 1)
    )
    assert all(roles[f"dev-lane-{i}"].skill_loading == {} for i in (0, 1))


def test_task_stage_retries_keep_their_policy_when_project_rolls_back(tmp_path):
    from dataclasses import replace
    from tests.test_role_lifecycle_runtime import _runtime
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.core.task.schema import Task
    from zf.runtime.task_pipeline_runtime import (
        _effective_pipeline_role,
        _role_config_digest,
    )

    runtime, base, _ = _runtime(tmp_path)
    default = replace(base, skills=["method"])
    trial = replace(default, skill_loading={"method": "on_demand"})
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path
    )

    def route(task_id, role):
        return _effective_pipeline_role(
            runtime,
            role,
            workflow_run_id="run",
            task_map_generation="g",
            policy={},
            stage="verify",
            task=Task(id=task_id, title="Verify"),
        )

    for task_id, start, changed in [("old", default, trial), ("pilot", trial, default)]:
        chosen = route(task_id, start)
        task = Task(id=task_id, title="Verify")
        registry.bind_task_stage_session(
            workflow_run_id="run",
            task_id=task_id,
            stage="verify",
            rework_affinity_id="g:verify",
            role_instance=chosen.instance_id,
            role_config_digest=_role_config_digest(runtime, chosen, task),
            workspace_generation=1,
            placement_epoch=1,
            skill_loading=chosen.skill_loading,
        )
        retry = route(task_id, changed)
        assert retry.skill_loading == start.skill_loading
        assert _role_config_digest(runtime, retry, task) == _role_config_digest(
            runtime, chosen, task
        )
    assert route("new-after-rollback", default).skill_loading == {}


@pytest.mark.parametrize(
    "mode, expected", [("on_demand", "load-on-demand"), ("required", "auto-inject")]
)
def test_native_task_stage_activation_writes_the_frozen_mode(tmp_path, mode, expected):
    from dataclasses import replace
    from tests.test_role_lifecycle_runtime import _runtime
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.core.task.schema import Task
    from zf.runtime.task_pipeline_runtime import (
        _activate_task_stage_binding,
        _role_config_digest,
    )

    runtime, base, _ = _runtime(tmp_path)
    source = tmp_path / "skills/method/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "---\nname: method\ndescription: Test method\n---\nMethod body.\n"
    )
    role = replace(base, skills=["method"], skill_loading={"method": mode})
    workspace = tmp_path / "worker"
    workspace.mkdir()
    task = Task(id="TASK-verify", title="Verify")
    _activate_task_stage_binding(
        runtime,
        role=role,
        task=task,
        workflow_run_id="run",
        task_map_generation="g",
        stage="verify",
        workspace_generation=1,
        placement_epoch=1,
        role_config_digest=_role_config_digest(runtime, role, task),
        project_path=workspace,
        workspace_root=tmp_path,
    )
    instructions = (
        runtime.state_dir / "instructions" / f"{role.instance_id}.md"
    ).read_text()
    assert f"mode: {expected}" in next(
        line for line in instructions.splitlines() if line.startswith("- `/method`")
    )
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path
    )
    assert registry.task_stage_binding(
        workflow_run_id="run",
        task_id=task.id,
        stage="verify",
        rework_affinity_id="g:verify",
    )["skill_loading"] == {"method": mode}
