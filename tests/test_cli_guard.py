from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _project(tmp_path: Path, yaml_extra: str = "") -> Path:
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        f"{yaml_extra}",
        encoding="utf-8",
    )
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state


def test_guard_ownership_passes_for_current_assignee(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    state = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    TaskStore(state / "kanban.json").add(
        Task(id="TASK-1", title="demo", assigned_to="dev")
    )

    rc = main(["guard", "ownership", "--task", "TASK-1", "--actor", "dev"])

    assert rc == 0
    assert "owned by dev" in capsys.readouterr().out


def test_guard_ownership_rejects_stale_actor(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    state = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    TaskStore(state / "kanban.json").add(
        Task(id="TASK-1", title="demo", assigned_to="dev-2")
    )

    rc = main([
        "guard",
        "ownership",
        "--task",
        "TASK-1",
        "--actor",
        "dev-1",
        "--json",
    ])

    assert rc == 3
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "actor_not_assigned"
    assert payload["expected"] == "dev-2"
    assert payload["actual"] == "dev-1"


def test_guard_ownership_accepts_role_name_instance_equivalence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = _project(
        tmp_path,
        yaml_extra=(
            "roles:\n"
            "  - name: dev\n"
            "    replicas: 2\n"
            "    backend: codex\n"
        ),
    )
    monkeypatch.chdir(tmp_path)
    TaskStore(state / "kanban.json").add(
        Task(id="TASK-1", title="demo", assigned_to="dev")
    )

    rc = main(["guard", "ownership", "--task", "TASK-1", "--actor", "dev-1"])

    assert rc == 0


def test_guard_ownership_unknown_task_exits_2(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = main([
        "guard",
        "ownership",
        "--task",
        "TASK-missing",
        "--actor",
        "dev",
    ])

    assert rc == 2
    assert "not found" in capsys.readouterr().err
