"""ZF-TEST-087：上报源码设置不得污染已选定执行项目的角色工作目录。"""

import pytest

from zf.core.config.schema import RoleConfig, SelfIssueConfig
from zf.core.config.self_issue_policy import _external_issue_workflow_document
from zf.core.events.model import ZfEvent
from zf.core.workflow.workflow_kind import compile_workflow_kind
from zf.runtime.orchestrator import Orchestrator
from tests.test_role_lifecycle_runtime import _runtime
from tests.test_workdirs import _git, _init_repo


@pytest.fixture
def roots(tmp_path, monkeypatch):
    project, canonical = tmp_path / "execution", tmp_path / "report-only"
    for root in (project, canonical):
        root.mkdir()
        _init_repo(root)
        (root / "identity.txt").write_text(root.name)
        _git(root, "add", "identity.txt")
        _git(root, "commit", "-qm", "source identity")
    monkeypatch.setattr("zf.runtime.self_issue_target.zaofu_v7_root", lambda: canonical)
    runtime, _, transport = _runtime(project)
    managed = compile_workflow_kind(
        _external_issue_workflow_document()["spec"],
    ).generic_contract
    runtime.config.self_issue = SelfIssueConfig(
        enabled=True, target_locked=True, target_project="owner/zaofu",
        source_mode="explicit_target", source_root=str(canonical),
        # 入队之后即使下一次执行的设置发生变化，当前运行器也不重新路由。
        execution_project_root=str(canonical),
    )
    runtime.config.workflow.generic_workflows = [managed]
    runtime.config.roles = [
        RoleConfig(name="external-issue-triage", backend="mock", role_kind="reader"),
        RoleConfig(name="product-writer", backend="mock", role_kind="writer"),
    ]
    runtime.config.runtime.workdirs.enabled = True
    runtime.config.runtime.workdirs.mode = "worktree"
    runtime.config.runtime.workdirs.provision_paths = []
    return runtime, transport, project, canonical


@pytest.mark.parametrize("mode", ["current_project", "zaofu_self_issue", "explicit_target", "issue_repository"])
def test_runtime_spawn_and_reopen_preserve_each_role_source(roots, mode):
    runtime, transport, project, canonical = roots
    runtime.config.self_issue.source_mode = mode
    for runtime in (
        runtime,
        Orchestrator(runtime.state_dir, runtime.config, transport, project_root=project),
    ):
        assert runtime.project_root == project
        for role in runtime.config.roles:
            cwd = runtime._role_spawn_cwd(role, source="respawn")
            assert cwd is not None
            assert (cwd / "identity.txt").read_text() == project.name
    assert (canonical / "identity.txt").read_text() == canonical.name
    assert any(e.type == "workdir.prepared" for e in runtime.event_log.read_all())


def test_disabled_workdirs_do_not_override_execution_cwd(roots):
    runtime, _, project, _ = roots
    runtime.config.runtime.workdirs.enabled = False
    managed, ordinary = runtime.config.roles
    assert runtime._role_spawn_cwd(managed, source="respawn") is None
    assert runtime._role_spawn_cwd(ordinary, source="respawn") is None
    assert runtime.project_root == project


def test_reader_guard_preserves_execution_and_report_working_trees(roots):
    runtime, _, project, canonical = roots
    role = runtime.config.roles[0]
    cwd = runtime._role_spawn_cwd(role, source="respawn")
    (cwd / "identity.txt").write_text("unauthorized reader edit")
    (project / "identity.txt").write_text("unrelated product change")
    (canonical / "identity.txt").write_text("unrelated report source change")
    runtime._check_reader_write_violation(ZfEvent(type="review.approved", actor=role.instance_id))
    assert (cwd / "identity.txt").read_text() == project.name
    assert (project / "identity.txt").read_text() == "unrelated product change"
    assert (canonical / "identity.txt").read_text() == "unrelated report source change"
    assert any(e.type == "reader.write_violation" for e in runtime.event_log.read_all())


def test_idle_recovery_uses_role_root_for_briefing_and_instructions(roots, monkeypatch):
    runtime, _, project, _ = roots
    role = runtime.config.roles[0]
    captured = []

    def capture(*args, **kwargs):
        captured.append(kwargs["project_root"])
        return "recovery context"

    monkeypatch.setattr("zf.runtime.orchestrator_lifecycle.build_recovery_briefing", capture)
    monkeypatch.setattr("zf.runtime.orchestrator_lifecycle.generate_role_instructions", capture)
    monkeypatch.setattr(runtime, "_record_skill_provenance", lambda **kwargs: [])
    assert runtime._inject_recovery_briefing(role, inject_idle_prompt=False)
    assert captured == [project, project]
