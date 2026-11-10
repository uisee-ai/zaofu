"""Contract paths are relative to the kernel workspace, never a directory name."""

import pytest

from tests.test_hook_recv_codex import _invoke
from tests.test_hook_task_workspace import assignment  # noqa: F401


def native(assignment, monkeypatch, backend, tool, target, cwd=None):
    state, _, workspace, *_ = assignment
    return _invoke(
        state, event=f"{'codex' if backend == 'codex' else 'claude'}.hook.pre_tool_use",
        backend=backend, monkeypatch=monkeypatch, payload={
            "session_id": "8ec85a68-8d54-4eee-9101-b0669f3ea098",
            "cwd": str(cwd or workspace), "tool_name": tool,
            "tool_input": {"file_path": str(target), "content": "not executed"},
        },
    )


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative,expected", [
    ("src/out.txt", 0), ("other/out.txt", 2), ("other/project/src/out.txt", 2),
])
def test_native_shared_frame_matches_real_relative_path(assignment, monkeypatch, backend, tool, relative, expected):
    _, _, workspace, *_ = assignment
    target = workspace / relative
    assert native(assignment, monkeypatch, backend, tool, target) == expected
    assert not target.exists()


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_native_cwd_cannot_replace_authorized_root(assignment, monkeypatch, backend, tool):
    _, _, workspace, *_ = assignment
    cwd = workspace / "other/project"
    cwd.mkdir(parents=True)
    assert native(assignment, monkeypatch, backend, tool, "src/out.txt", cwd=cwd) == 2


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_native_relative_parent_path_normalizes_within_authorized_scope(assignment, monkeypatch, tool):
    _, _, workspace, *_ = assignment
    cwd = workspace / "other"
    cwd.mkdir()
    assert native(assignment, monkeypatch, "codex", tool, "../src/out.txt", cwd=cwd) == 0


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_native_contract_can_explicitly_own_nested_project_directory(assignment, monkeypatch, tool):
    from zf.core.task.store import TaskStore

    state, _, workspace, *_ = assignment
    store = TaskStore(state / "kanban.json")
    task = store.get("T-NEW")
    task.contract.scope = ["other/project/src/**"]
    store.update(task.id, contract=task.contract)
    assert native(assignment, monkeypatch, "codex", tool, workspace / "other/project/src/out.txt") == 0
