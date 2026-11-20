from pathlib import Path

from tests.test_task_workspace_binding import _manager, _prepare, _git


def test_ignored_candidate_material_is_not_disposable_on_task_completion(tmp_path):
    manager, role, base = _manager(tmp_path)
    plan = _prepare(manager, role, base, "T")
    project = Path(plan.project_path)
    (project / ".gitignore").write_text("private-evidence/\n")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-qm", "declare private evidence")
    evidence = project / "private-evidence" / "qualification.json"
    evidence.parent.mkdir()
    evidence.write_text('{"result":"not yet delivered"}')
    result = manager.cleanup(plan, task_terminal=True, integrated_or_archived=True,
        active_attempts=0, active_sessions=0)
    assert result.status == "blocked"
    assert "ignored" in result.reason
    assert evidence.exists()


def test_declared_dependency_cache_can_be_cleaned_after_settlement(tmp_path):
    manager, role, base = _manager(tmp_path)
    manager.config.runtime.workdirs.provision_paths = ["node_modules"]
    plan = _prepare(manager, role, base, "T")
    project = Path(plan.project_path)
    (project / ".gitignore").write_text("node_modules/\n")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-qm", "declare dependencies")
    cache = project / "node_modules"
    cache.mkdir()
    (cache / "generated.js").write_text("dependency")
    result = manager.cleanup(plan, task_terminal=True, integrated_or_archived=True,
        active_attempts=0, active_sessions=0)
    assert result.removed


def test_outer_workspace_handoff_is_retained(tmp_path):
    manager, role, base = _manager(tmp_path)
    plan = _prepare(manager, role, base, "T")
    handoff = Path(plan.workdir) / "handoff.md"
    handoff.write_text("not yet delivered")
    result = manager.cleanup(plan, task_terminal=True, integrated_or_archived=True,
        active_attempts=0, active_sessions=0)
    assert result.status == "blocked"
    assert handoff.exists() and Path(plan.project_path).exists()
