"""Workspace registry, Project resolver, and project-scoped Web routes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.verification.event_schema import (
    EventSchemaRegistry,
    assignment_event_schema_rules,
)
from zf.core.workspace.lifecycle import clear_project_lifecycle_cache, project_lifecycle
from zf.core.workspace.project_resolver import ProjectResolver, clear_project_resolver_cache
from zf.core.workspace.registry import WorkspaceRegistry, legacy_project_id
from zf.runtime.automation_projection import project_automations
from zf.web.server import create_app


def _make_project(
    root: Path,
    *,
    name: str,
    state_dir_name: str = ".zf",
    extra_config: str = "",
) -> Path:
    root.mkdir(parents=True)
    (root / "zf.yaml").write_text(
        (
            f'version: "1.0"\n'
            f"project:\n"
            f"  name: {name}\n"
            f"  state_dir: {state_dir_name}\n"
            f"{extra_config}"
        ),
        encoding="utf-8",
    )
    state_dir = root / state_dir_name
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli"),
    )
    return state_dir


def test_workspace_registry_resolver_reloads_state_dir_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    state_a = _make_project(root, name="proj", state_dir_name="state-a")
    context = resolve_project_context(cwd=root)
    project = WorkspaceRegistry().upsert_context(context)

    (root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: proj\n  state_dir: state-b\n',
        encoding="utf-8",
    )

    resolved = ProjectResolver().resolve(project.project_id)

    assert resolved.context.state_dir == (root / "state-b").resolve()
    assert resolved.project.state_dir_hint == str(state_a.resolve())


def test_project_resolver_uses_short_ttl_config_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    _make_project(root, name="proj", state_dir_name="state-a")
    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    clear_project_resolver_cache()
    calls = 0

    def counting_load_config(path: Path):
        nonlocal calls
        calls += 1
        return load_config(path)

    monkeypatch.setattr("zf.core.workspace.project_resolver.load_config", counting_load_config)
    resolver = ProjectResolver()

    first = resolver.resolve(project.project_id)
    second = resolver.resolve(project.project_id)

    assert first.context.state_dir == second.context.state_dir
    assert calls == 1

    (root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: proj\n  state_dir: state-b\n',
        encoding="utf-8",
    )

    changed = resolver.resolve(project.project_id)

    assert changed.context.state_dir == (root / "state-b").resolve()
    assert calls == 2


def test_project_lifecycle_uses_short_ttl_config_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    _make_project(root, name="proj", state_dir_name="state-a")
    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    clear_project_lifecycle_cache()
    calls = 0

    def counting_load_config(path: Path):
        nonlocal calls
        calls += 1
        return load_config(path)

    monkeypatch.setattr("zf.core.workspace.lifecycle.load_config", counting_load_config)

    first = project_lifecycle(project)
    second = project_lifecycle(project)

    assert first.state_dir_resolved == second.state_dir_resolved
    assert calls == 1

    (root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: proj\n  state_dir: state-b\n',
        encoding="utf-8",
    )

    changed = project_lifecycle(project)

    assert changed.state_dir_resolved == str((root / "state-b").resolve())
    assert calls == 2


def test_zf_init_can_register_or_skip_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "registered"
    root.mkdir()
    (root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: registered\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    assert main(["init", "--workspace-register"]) == 0
    assert len(WorkspaceRegistry().list_projects()) == 1

    other = tmp_path / "skipped"
    other.mkdir()
    (other / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: skipped\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(other)

    assert main(["init", "--no-workspace-register"]) == 0
    assert len(WorkspaceRegistry().list_projects()) == 1


def test_project_scoped_snapshot_action_and_automations_are_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    state_a = _make_project(root_a, name="alpha")
    state_b = _make_project(root_b, name="beta")
    TaskStore(state_a / "kanban.json").add(Task(id="TASK-A", title="Alpha task"))
    TaskStore(state_b / "kanban.json").add(Task(id="TASK-B", title="Beta task"))
    EventLog(state_b / "events.jsonl").append(
        ZfEvent(
            type="automation.run.started",
            actor="monitor",
            payload={
                "automation_id": "daily-brief",
                "project_id": "placeholder",
                "run_id": "run-1",
                "trigger": "manual",
                "source": "test",
            },
        ),
    )

    project_a = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_a))
    project_b = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_b))
    app = create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    client = TestClient(app)

    projects = client.get("/api/workspace/projects").json()["items"]
    assert {row["project_id"] for row in projects} >= {
        project_a.project_id,
        project_b.project_id,
    }

    snap_a = client.get(f"/api/projects/{project_a.project_id}/snapshot").json()
    snap_b = client.get(f"/api/projects/{project_b.project_id}/snapshot").json()
    assert [task["id"] for task in snap_a["tasks"]] == ["TASK-A"]
    assert [task["id"] for task in snap_b["tasks"]] == ["TASK-B"]

    mismatch = client.post(
        f"/api/projects/{project_b.project_id}/actions/create-task",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_a.project_id,
            "idempotency_key": "bad-project",
            "payload": {"title": "wrong"},
        },
    )
    assert mismatch.status_code == 422

    created = client.post(
        f"/api/projects/{project_b.project_id}/actions/create-task",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_b.project_id,
            "idempotency_key": "create-beta",
            "payload": {"title": "Created in beta"},
        },
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]
    assert TaskStore(state_b / "kanban.json").get(task_id) is not None
    assert TaskStore(state_a / "kanban.json").get(task_id) is None

    automations = client.get(f"/api/projects/{project_b.project_id}/automations").json()
    assert automations["schema_version"] == "project_automation.v1"
    assert {item["automation_id"] for item in automations["items"]} >= {
        "daily-brief",
        "weekly-review",
        "project-monitor",
    }
    assert project_automations(
        state_b,
        project_id=project_b.project_id,
    )["items"][0]["project_id"] == project_b.project_id


def test_workspace_overview_marks_duplicate_explicit_tmux_session_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    extra = "session:\n  tmux_session: shared-zf\n"
    state_a = _make_project(root_a, name="alpha", extra_config=extra)
    _make_project(root_b, name="beta", extra_config=extra)
    project_a = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_a))
    project_b = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_b))
    app = create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    client = TestClient(app)

    rows = client.get("/api/workspace/overview").json()["projects"]
    by_id = {row["project_id"]: row for row in rows}

    assert by_id[project_a.project_id]["runtime"]["state"] == "conflicted"
    assert by_id[project_b.project_id]["runtime"]["state"] == "conflicted"
    assert by_id[project_a.project_id]["runtime"]["tmux_session"] == "shared-zf"


def test_workspace_project_wizard_endpoints_register_and_initialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    root_a = tmp_path / "project-a"
    state_a = _make_project(root_a, name="alpha")
    app = create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    client = TestClient(app)

    valid = client.post(
        "/api/workspace/projects/validate-path",
        json={"root": str(root_a)},
    )
    assert valid.status_code == 200
    assert valid.json()["has_config"] is True

    denied = client.post(
        "/api/workspace/projects/register",
        json={"root": str(root_a)},
    )
    assert denied.status_code == 403

    registered = client.post(
        "/api/workspace/projects/register",
        headers={"x-zf-web-token": "test-token"},
        json={"root": str(root_a), "workspace": "qa"},
    )
    assert registered.status_code == 200
    assert registered.json()["project"]["root"] == str(root_a.resolve())

    root_b = tmp_path / "project-b"
    root_b.mkdir()
    initialized = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(root_b),
            "workspace": "qa",
            "preset": "safe-team",
            "state_dir": ".zf",
        },
    )
    assert initialized.status_code == 201
    assert (root_b / "zf.yaml").exists()
    assert (root_b / "AGENTS.md").exists()
    assert (root_b / "CLAUDE.md").exists()
    assert (root_b / ".zf" / "kanban.json").exists()
    assert "AGENTS.md" in initialized.json()["instruction_docs"]["created"]
    assert "CLAUDE.md" in initialized.json()["instruction_docs"]["created"]
    assert initialized.json()["project"]["root"] == str(root_b.resolve())

    root_c = tmp_path / "project-c"
    initialized_new_root = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(root_c),
            "workspace": "qa",
            "preset": "minimal",
            "state_dir": ".zf",
        },
    )
    assert initialized_new_root.status_code == 201
    assert root_c.exists()
    assert (root_c / "zf.yaml").exists()
    assert (root_c / "AGENTS.md").exists()
    assert (root_c / "CLAUDE.md").exists()
    assert (root_c / ".zf" / "kanban.json").exists()
    assert initialized_new_root.json()["project"]["root"] == str(root_c.resolve())


def test_project_scoped_operator_sessions_are_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    state_a = _make_project(root_a, name="alpha")
    state_b = _make_project(root_b, name="beta")
    project_a = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_a))
    project_b = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_b))
    app = create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    client = TestClient(app)

    try:
        started_a = client.post(
            f"/api/projects/{project_a.project_id}/operator/start",
            headers={"x-zf-web-token": "test-token"},
            json={"backend": "deterministic", "force": True},
        )
        started_b = client.post(
            f"/api/projects/{project_b.project_id}/operator/start",
            headers={"x-zf-web-token": "test-token"},
            json={"backend": "deterministic", "force": True},
        )
        assert started_a.status_code == 202
        assert started_b.status_code == 202

        session_a = client.get(
            f"/api/projects/{project_a.project_id}/operator/session",
        ).json()
        session_b = client.get(
            f"/api/projects/{project_b.project_id}/operator/session",
        ).json()

        assert session_a["state_dir"] == str(state_a.resolve())
        assert session_b["state_dir"] == str(state_b.resolve())
        assert session_a["session_id"] != session_b["session_id"]
        assert Path(session_a["transcript_path"]).is_relative_to(state_a.resolve())
        assert Path(session_b["transcript_path"]).is_relative_to(state_b.resolve())
    finally:
        client.post(
            f"/api/projects/{project_a.project_id}/operator/stop",
            headers={"x-zf-web-token": "test-token"},
            json={"reason": "test cleanup"},
        )
        client.post(
            f"/api/projects/{project_b.project_id}/operator/stop",
            headers={"x-zf-web-token": "test-token"},
            json={"reason": "test cleanup"},
        )


def test_assignment_intent_is_project_scoped_and_schema_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    state_a = _make_project(root_a, name="alpha")
    state_b = _make_project(root_b, name="beta")
    TaskStore(state_b / "kanban.json").add(Task(id="TASK-B", title="Beta task"))
    project_a = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_a))
    project_b = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_b))
    app = create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    client = TestClient(app)

    invalid_assignee_type = client.post(
        f"/api/projects/{project_b.project_id}/actions/assignment-propose",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_b.project_id,
            "payload": {
                "task_id": "TASK-B",
                "assignee_type": "robot",
                "assignee_id": "dev-1",
            },
        },
    )
    assert invalid_assignee_type.status_code == 422
    assert "assignee_type" in invalid_assignee_type.json()["reason"]

    proposed = client.post(
        f"/api/projects/{project_b.project_id}/actions/assignment-propose",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_b.project_id,
            "payload": {
                "task_id": "TASK-B",
                "assignee_type": "squad",
                "assignee_id": "ch-auth",
                "assignee_label": "Auth squad",
                "role": "dev-1",
                "backend": "codex-headless",
                "channel_id": "ch-auth",
                "supervisor": "lead",
                "reason": "parallel ownership proposal",
            },
        },
    )

    assert proposed.status_code == 202
    assert proposed.json()["status"] == "proposed"
    assert TaskStore(state_b / "kanban.json").get("TASK-B").assigned_to is None

    events_a = EventLog(state_a / "events.jsonl").read_all()
    events_b = EventLog(state_b / "events.jsonl").read_all()
    assert not any(event.type == "assignment.intent.proposed" for event in events_a)
    assignment = next(
        event for event in events_b if event.type == "assignment.intent.proposed"
    )
    assert assignment.payload["project_id"] == project_b.project_id
    assert assignment.payload["task_id"] == "TASK-B"
    assert assignment.payload["assignee_type"] == "squad"
    assert assignment.payload["assignee_id"] == "ch-auth"
    assert assignment.payload["assignee_label"] == "Auth squad"
    assert assignment.payload["dispatches"] is False
    assert not any(event.type == "task.dispatched" for event in events_b)

    created_agent_task = client.post(
        f"/api/projects/{project_b.project_id}/actions/create-task",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_b.project_id,
            "payload": {
                "title": "Agent-owned task",
                "assigned_to": "agent-dev-1",
                "assignee_type": "agent",
                "assignee_id": "agent-dev-1",
                "assignee_label": "Dev agent",
                "source": "web-new-task-draft",
            },
        },
    )
    assert created_agent_task.status_code == 201
    agent_task_id = created_agent_task.json()["task_id"]
    agent_task = TaskStore(state_b / "kanban.json").get(agent_task_id)
    assert agent_task is not None
    assert agent_task.assigned_to == "agent-dev-1"

    proposed_agent = client.post(
        f"/api/projects/{project_b.project_id}/actions/assignment-propose",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_b.project_id,
            "payload": {
                "task_id": agent_task_id,
                "assignee_type": "agent",
                "assignee_id": "agent-dev-1",
                "assignee_label": "Dev agent",
                "role": "agent-dev-1",
                "backend": "codex-headless",
                "reason": "agent ownership proposal",
            },
        },
    )
    assert proposed_agent.status_code == 202

    events_a = EventLog(state_a / "events.jsonl").read_all()
    events_b = EventLog(state_b / "events.jsonl").read_all()
    assert not any(event.type == "assignment.intent.proposed" for event in events_a)
    agent_assignment = next(
        event for event in events_b
        if event.type == "assignment.intent.proposed"
        and event.payload.get("task_id") == agent_task_id
    )
    assert agent_assignment.payload["assignee_type"] == "agent"
    assert agent_assignment.payload["assignee_id"] == "agent-dev-1"
    assert agent_assignment.payload["role"] == "agent-dev-1"
    assert agent_assignment.payload["dispatches"] is False
    assert not any(event.type == "task.dispatched" for event in events_b)

    registry = EventSchemaRegistry.from_dict(assignment_event_schema_rules())
    assert registry.validate(assignment) == []
    assert registry.validate(agent_assignment) == []
    assert project_a.project_id != project_b.project_id


def _error_detail(response) -> dict:
    data = response.json()
    detail = data.get("detail") if isinstance(data, dict) else None
    return detail if isinstance(detail, dict) else data


def test_workspace_lifecycle_and_uninitialized_project_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    root_a = tmp_path / "project-a"
    state_a = _make_project(root_a, name="alpha")
    root_b = tmp_path / "project-b"
    root_b.mkdir()
    (root_b / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: beta\n  state_dir: .zf\n',
        encoding="utf-8",
    )
    project_a = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_a))
    project_b = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_b))
    app = create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    client = TestClient(app)

    projects = client.get("/api/workspace/projects").json()["items"]
    by_id = {row["project_id"]: row for row in projects}

    assert by_id[project_a.project_id]["lifecycle"]["can_open_board"] is True
    assert by_id[project_b.project_id]["lifecycle"]["can_open_board"] is False
    assert by_id[project_b.project_id]["lifecycle"]["state_dir_exists"] is False

    snapshot = client.get(f"/api/projects/{project_b.project_id}/snapshot")
    assert snapshot.status_code == 409
    assert _error_detail(snapshot)["status"] == "project_uninitialized"

    action = client.post(
        f"/api/projects/{project_b.project_id}/actions/create-task",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_b.project_id,
            "payload": {"title": "must initialize first"},
        },
    )
    assert action.status_code == 409
    assert not (root_b / ".zf" / "kanban.json").exists()


def test_web_workspace_active_project_prefers_server_default_until_user_touch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    state_a = _make_project(root_a, name="alpha")
    _make_project(root_b, name="beta")
    project_a = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_a))
    project_b = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_b))

    client = TestClient(
        create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    )

    projects = client.get("/api/workspace/projects").json()
    assert projects["server_default_project_id"] == project_a.project_id
    assert projects["active_project_id"] == project_a.project_id
    assert projects["active_project_is_server_default"] is True

    touched = client.post(f"/api/workspace/projects/{project_b.project_id}/touch")
    assert touched.status_code == 200
    projects_after_touch = client.get("/api/workspace/projects").json()
    assert projects_after_touch["active_project_id"] == project_b.project_id
    assert projects_after_touch["active_project_is_server_default"] is False


def test_workspace_registry_uses_stable_project_id_with_legacy_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "stable-root"
    _make_project(root, name="alpha")
    registry = WorkspaceRegistry()
    project = registry.upsert_context(resolve_project_context(cwd=root))
    old_id = legacy_project_id(name="alpha", root=root)

    (root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: renamed\n  state_dir: .zf\n',
        encoding="utf-8",
    )
    renamed = registry.upsert_context(resolve_project_context(cwd=root))

    assert renamed.project_id == project.project_id
    assert old_id in renamed.aliases
    assert len(registry.list_projects()) == 1
    assert ProjectResolver().resolve(old_id).project_id == renamed.project_id


def test_workspace_registry_display_name_does_not_change_project_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "cj-min"
    _make_project(root, name="cj-min")
    registry = WorkspaceRegistry()
    context = resolve_project_context(cwd=root)

    base = registry.upsert_context(context)
    displayed = registry.upsert_context(context, display_name="cj-min-r37-a1b2")

    assert displayed.project_id == base.project_id
    assert displayed.name == "cj-min-r37-a1b2"
    assert len(registry.list_projects()) == 1


def test_workspace_touch_remove_and_project_scoped_resource_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    state_a = _make_project(root_a, name="alpha")
    state_b = _make_project(root_b, name="beta")
    project_a = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_a))
    project_b = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root_b))
    app = create_app(state_a, config=load_config(root_a / "zf.yaml"), project_root=root_a)
    client = TestClient(app)

    touched = client.post(f"/api/workspace/projects/{project_b.project_id}/touch")
    assert touched.status_code == 200
    assert touched.json()["project"]["last_opened_at"]

    runtime_b = client.get(f"/api/projects/{project_b.project_id}/runtime").json()
    runtime_default = client.get("/api/runtime").json()
    assert runtime_b["state_dir"] == str(state_b.resolve())
    assert runtime_default["state_dir"] == str(state_a.resolve())
    assert client.get(f"/api/projects/{project_b.project_id}/roles").status_code == 200
    assert client.get(f"/api/projects/{project_b.project_id}/agents").status_code == 200
    assert client.get(f"/api/projects/{project_b.project_id}/workdirs").status_code == 200
    assert client.get(f"/api/projects/{project_b.project_id}/workflow/graph").status_code == 200

    removed = client.delete(
        f"/api/workspace/projects/{project_b.project_id}",
        headers={"x-zf-web-token": "test-token"},
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "removed"
    assert (state_b / "kanban.json").exists()
    assert WorkspaceRegistry().get(project_b.project_id) is None
    assert WorkspaceRegistry().get(project_a.project_id) is not None


def test_workspace_overview_includes_resource_rollup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    state_dir = _make_project(root, name="alpha")
    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": "ch-alpha", "name": "# alpha"},
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.failed",
        actor="channel",
        payload={
            "channel_id": "ch-alpha",
            "request_id": "reply-1",
            "member_id": "architect",
            "reason": "provider missing",
        },
    ))
    log.append(ZfEvent(
        type="automation.proposal.created",
        actor="automation",
        payload={
            "automation_id": "project-monitor",
            "project_id": project.project_id,
            "proposal_id": "proposal-1",
            "summary": "retry failed channel reply",
        },
    ))
    app = create_app(state_dir, config=load_config(root / "zf.yaml"), project_root=root)
    client = TestClient(app)

    rows = client.get("/api/workspace/overview").json()["projects"]
    row = next(item for item in rows if item["project_id"] == project.project_id)

    assert row["resources"]["channels"]["count"] == 1
    assert row["resources"]["channels"]["failed_replies"] == 1
    assert row["resources"]["automations"]["proposals"] == 1
    assert "operator" in row["resources"]
