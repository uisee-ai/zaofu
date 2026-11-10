"""Literal copies use the real PreToolUse chain and retain all write guards."""

from pathlib import Path
import shlex

import pytest

from tests.test_hook_literal_handoff import check
from tests.test_hook_task_workspace import assignment  # noqa: F401


def source_file(assignment):
    state, _, workspace, *_ = assignment
    source = state.parent / "read-only evidence.txt"
    source.write_text("independent counterexample\n")
    (workspace / "src").mkdir()
    return source


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("end_options", [False, True])
def test_copy_external_evidence_to_assigned_scope(assignment, monkeypatch, backend, absolute, end_options):
    source = source_file(assignment)
    _, _, workspace, log, *_ = assignment
    target = workspace / "src/test notes.mjs" if absolute else Path("src/test notes.mjs")
    command = f"cp {'-- ' if end_options else ''}{shlex.quote(str(source))} {shlex.quote(str(target))}"
    assert check(assignment, monkeypatch, command, backend) == 0
    assert not [e for e in log.read_all() if e.type == "worker.scope_write.rejected"]
    assert not (workspace / "src/test notes.mjs").exists()  # Hook never executes cp.


@pytest.mark.parametrize("kind", [
    "root", "unrelated_root", "scope", "traversal", "scope_traversal", "other_task",
    "old_generation", "cwd", "leaf_symlink", "parent_symlink", "scope_symlink",
    "directory", "directory_leaf_symlink", "hardlink", "source_directory", "missing_source",
])
def test_copy_rejects_unproven_or_unauthorized_destinations(assignment, monkeypatch, tmp_path, kind):
    source = source_file(assignment)
    state, _, workspace, log, *_ = assignment
    outside = state.parent / "outside.txt"
    outside.write_text("unchanged")
    local_source = workspace / "src/source.txt"
    local_source.write_text("source")
    target, cwd = workspace / "src/destination.txt", workspace
    if kind == "root":
        source, target = local_source, outside  # Allowed source must not mask an outside destination.
    elif kind == "unrelated_root":
        target = tmp_path.parent / "not-the-project.txt"
    elif kind == "scope":
        target = workspace / "outside.txt"
    elif kind == "traversal":
        target = Path("../../g0/project/src/out.txt")
    elif kind == "scope_traversal":
        target = Path("src/../outside.txt")
    elif kind == "other_task":
        target = state / "workdirs/tasks/run-1/OTHER/g1/project/src/out.txt"
    elif kind == "old_generation":
        target = workspace.parent.parent / "g0/project/src/out.txt"
    elif kind == "cwd":
        target, cwd = Path("src/destination.txt"), state.parent
    elif kind == "leaf_symlink":
        target.symlink_to(outside)
    elif kind in {"parent_symlink", "scope_symlink"}:
        link = workspace / "src/link"
        destination = state.parent if kind == "parent_symlink" else workspace / "other"
        destination.mkdir(exist_ok=True)
        link.symlink_to(destination, target_is_directory=True)
        target = link / "out.txt"
    elif kind in {"directory", "directory_leaf_symlink"}:
        target = workspace / "src"
        if kind == "directory_leaf_symlink":
            (target / source.name).symlink_to(outside)
    elif kind == "hardlink":
        target.hardlink_to(outside)
    elif kind == "source_directory":
        source = state.parent
    elif kind == "missing_source":
        source = state.parent / "missing"
    assert check(assignment, monkeypatch, f"cp {shlex.quote(str(source))} {shlex.quote(str(target))}", cwd=cwd) == 2
    assert outside.read_text() == "unchanged"
    assert any(e.type == "worker.scope_write.rejected" for e in log.read_all())


@pytest.mark.parametrize("template", [
    "cp -t {root} {source}", "cp --target-directory={root} {source}",
    "cp -T {source} {target}", "cp -r {source} {target}", "cp -a {source} {target}",
    "cp -l {source} {target}", "cp -s {source} {target}",
    "cp --backup --suffix=.bak {source} {target}", "cp --parents {source} {target}",
    "cp --unknown {source} {target}", "cp {source} {source} {target}",
    "cp {source} src/*.txt", "cp {source} $TARGET", "cp {source} ~/target",
    'cp {source} "$(echo {target})"', "cp <(cat {source}) {target}",
    "cp {source} {target}; printf bad > {root}/outside.txt",
    "cp {source} {target}\nprintf bad > {root}/outside.txt",
    "cp {source} {target} && true", "cp {source} {target} | cat",
    "cp {source} {target} > {root}/log", "cp {source} 'unclosed",
    "env cp {source} {target}", "command cp {source} {target}",
    "FLAG=1 cp {source} {target}", "/bin/cp {source} {target}",
    "bash -c 'cp {source} {target}'", "c\\p {source} {target}",
])
def test_unsupported_copy_syntax_fails_closed_even_with_assigned_path(assignment, monkeypatch, template):
    source_file(assignment)
    state, _, workspace, log, *_ = assignment
    source = workspace / "src/source.txt"
    source.write_text("source")
    command = template.format(root=state.parent, source=source, target=workspace / "src/target.txt")
    assert check(assignment, monkeypatch, command) == 2
    assert any(e.type == "worker.scope_write.rejected" for e in log.read_all())


def test_copy_preserves_runtime_state_guard_and_read_only_source(assignment, monkeypatch):
    source_file(assignment)
    state, _, workspace, log, *_ = assignment
    assert check(assignment, monkeypatch, f"cp {state}/kanban.json src/evidence.json") == 0
    assert check(assignment, monkeypatch, f"cp {state}/kanban.json {state}/feature_list.json") == 2
    assert any(e.type == "worker.runtime_write.rejected" for e in log.read_all())


def test_copy_does_not_acquire_signed_scratch_update_exception(assignment, monkeypatch):
    source = source_file(assignment)
    state, *_ = assignment
    monkeypatch.setattr(
        "zf.cli.hook_workdir_guard.is_authorized_result_scratch_target", lambda *a, **k: True,
    )
    assert check(assignment, monkeypatch, f"cp {shlex.quote(str(source))} {state}/scratch.json") == 2


def test_copy_allows_existing_unlinked_regular_destination(assignment, monkeypatch):
    source = source_file(assignment)
    _, _, workspace, *_ = assignment
    (workspace / "src/out.txt").write_text("old")
    assert check(assignment, monkeypatch, f"cp {shlex.quote(str(source))} src/out.txt") == 0


def test_copy_to_settled_workspace_does_not_reuse_old_assignment(assignment, monkeypatch):
    from zf.core.events.model import ZfEvent

    source = source_file(assignment)
    _, _, _, _, writer, prepared = assignment
    writer.append(ZfEvent(type="workflow.operation.settled", task_id="T-NEW", payload={
        "operation_id": prepared.operation_id, "workflow_run_id": "run-1",
    }))
    assert check(assignment, monkeypatch, f"cp {shlex.quote(str(source))} src/out.txt") == 2


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("command", [
    "rg -n cp app", "printf '%s\\n' cp", "python -c 'print(\"cp\")'", "cat /bin/cp",
    "printf ';' cp", "printf 'cp /outside/source /outside/target'",
    "bash -c 'printf cp'", "env printf cp", "command -- printf cp",
])
def test_copy_word_in_non_command_data_keeps_existing_read_behavior(assignment, monkeypatch, backend, command):
    assert check(assignment, monkeypatch, command, backend) == 0


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("target", ["other/project/src/out.txt", "other/project/project/src/out.txt"])
def test_copy_scope_uses_authorized_root_not_last_project_segment(assignment, monkeypatch, backend, target):
    source = source_file(assignment)
    _, _, workspace, *_ = assignment
    assert check(assignment, monkeypatch, f"cp {shlex.quote(str(source))} {workspace / target}", backend) == 2


@pytest.mark.parametrize("target", ["src/project/out.txt", "project/src/out.txt"])
def test_copy_legitimate_nested_project_directory_has_actual_relative_scope(assignment, monkeypatch, target):
    from zf.core.task.store import TaskStore

    source = source_file(assignment)
    state, _, workspace, *_ = assignment
    store = TaskStore(state / "kanban.json")
    task = store.get("T-NEW")
    task.contract.scope = [target.rsplit("/", 1)[0] + "/**"]
    store.update(task.id, contract=task.contract)
    assert check(assignment, monkeypatch, f"cp {shlex.quote(str(source))} {workspace / target}") == 0


def test_copy_cwd_cannot_rebase_authorized_scope(assignment, monkeypatch):
    source = source_file(assignment)
    _, _, workspace, *_ = assignment
    cwd = workspace / "other/project"
    cwd.mkdir(parents=True)
    assert check(assignment, monkeypatch, f"cp {shlex.quote(str(source))} src/out.txt", cwd=cwd) == 2


@pytest.mark.parametrize("template", [
    "printf ready; cp {source} {target}", "rg pattern app && cp {source} {target}",
    "bash -c 'printf ready; cp {source} {target}'", "echo $(cp {source} {target})",
    "sudo -u worker cp {source} {target}", "timeout 5 cp {source} {target}",
    "env -u NAME cp {source} {target}", "if true; then cp {source} {target}; fi",
])
def test_copy_in_wrapper_or_later_command_is_still_unproven(assignment, monkeypatch, template):
    source_file(assignment)
    _, _, workspace, *_ = assignment
    source = workspace / "src/source.txt"
    source.write_text("source")
    command = template.format(source=source, target=workspace / "src/out.txt")
    assert check(assignment, monkeypatch, command) == 2
