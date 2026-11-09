"""Deterministic Add/Open Project admission and default initialization."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.config.loader import load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.workspace.project_admission import inspect_project_admission
from zf.core.workspace.onboarding import apply_action
from zf.core.workspace.registry import WorkspaceRegistry
from zf.web.server import create_app


@pytest.fixture(autouse=True)
def _workspace_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))


def _write_config(
    root: Path,
    *,
    state_dir: str = ".zf",
    name: str = "project",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "zf.yaml"
    path.write_text(
        (
            'version: "1.0"\n'
            "project:\n"
            f"  name: {name}\n"
            f"  state_dir: {state_dir}\n"
        ),
        encoding="utf-8",
    )
    return path


def _make_ready_state(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "kanban.json").write_text("[]\n", encoding="utf-8")
    (path / "events.jsonl").write_text("", encoding="utf-8")


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state_dir = tmp_path / "server-state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    return TestClient(create_app(state_dir))


def test_admission_classifies_missing_and_bare_roots(tmp_path: Path) -> None:
    missing = inspect_project_admission(tmp_path / "new" / "nested")
    assert missing["admission"]["action"] == "initialize_project"
    assert missing["status"] == "missing"
    assert missing["can_create"] is True

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "README.md").write_text("# existing code\n", encoding="utf-8")
    (bare / "pyproject.toml").write_text(
        '[project]\nname = "existing-code"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    existing = inspect_project_admission(bare)
    assert existing["admission"]["action"] == "initialize_project"
    assert existing["status"] == "valid"
    assert existing["can_create"] is False
    assert existing["project_profile"]["languages"] == ["python"]
    assert existing["project_profile"]["confidence"] == "high"


def test_admission_uses_configured_state_and_opens_registered_project(
    tmp_path: Path,
) -> None:
    root = tmp_path / "configured"
    _write_config(root, state_dir=".runtime", name="configured")

    missing_state = inspect_project_admission(root)
    assert missing_state["admission"]["action"] == "initialize_state"
    assert missing_state["state_dir_resolved"] == str((root / ".runtime").resolve())

    _make_ready_state(root / ".runtime")
    unregistered = inspect_project_admission(root)
    assert unregistered["admission"]["action"] == "register"
    assert unregistered["state_ready"] is True

    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    registered = inspect_project_admission(root)
    assert registered["admission"] == {
        "action": "open",
        "label": "Open Project",
        "reason": "registered_project_ready",
        "project_id": project.project_id,
    }


def test_admission_blocks_invalid_config_and_partial_state(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "zf.yaml").write_text("project: [\n", encoding="utf-8")
    invalid_result = inspect_project_admission(invalid)
    assert invalid_result["admission"]["action"] == "blocked"
    assert invalid_result["admission"]["reason"] == "config_invalid"

    partial = tmp_path / "partial"
    _write_config(partial)
    state_dir = partial / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    partial_result = inspect_project_admission(partial)
    assert partial_result["admission"]["action"] == "blocked"
    assert partial_result["admission"]["reason"] == "state_dir_partial"
    assert partial_result["missing_truth_files"] == ["kanban.json"]


def test_implicit_init_generates_v5_config_without_workflow_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_action(
        "complete",
        backend="claude-code",
        mixed_enabled=True,
        now="2026-07-28T00:00:00+00:00",
    )
    client = _client(tmp_path, monkeypatch)
    target = tmp_path / "new-project"

    response = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(target),
            "workspace": "default",
            "name": "delivery-harness",
            "description": "Durable project context",
            "stack": "python",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "prd"
    assert body["config_generated"] == "registered_controller"
    assert body["controller_id"] == "zaofu-prd-v5-production-codex"
    config = load_config(target / "zf.yaml")
    assert config.project.name == "delivery-harness"
    assert config.project.description == "Durable project context"
    assert config.orchestrator.backend == "claude-code"
    assert {
        role.backend
        for role in config.roles
        if "verify-lane" in role.name
    } == {"codex"}
    assert {
        role.backend
        for role in config.roles
        if role.role_kind == "writer"
    } == {"claude-code"}
    assert body["provider_policy"] == {
        "primary_backend": "claude-code",
        "mixed_enabled": True,
        "verify_backend": "codex",
    }
    assert body["project_metadata"] == {
        "name": "delivery-harness",
        "description": "Durable project context",
    }
    assert "backend: mixed" not in (target / "zf.yaml").read_text(encoding="utf-8")
    agents_text = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert "Durable project context" in agents_text
    assert "ZF:PROJECT-CONTEXT:START" in agents_text
    assert "confidence: declared" in agents_text
    assert "test: `pytest`" in agents_text
    assert "Durable project context" not in (
        target / "CLAUDE.md"
    ).read_text(encoding="utf-8")
    assert body["instruction_docs"]["profile"]["languages"] == ["python"]
    assert body["instruction_docs"]["profile"]["confidence"] == "declared"
    assert (target / "README.md").is_file()
    assert (target / "src" / ".gitkeep").is_file()
    assert (target / "tests" / ".gitkeep").is_file()
    head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    assert body["git_readiness"] == {
        "created": True,
        "head": head,
        "ready": True,
    }
    assert subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout == ""
    assert config.version == "5.0.1"
    assert config.workflow.kind_routes["prd"].pattern_id == "prd-scan"
    event_types = {
        event.type for event in EventLog(Path(body["state_dir"]) / "events.jsonl").read_all()
    }
    assert "workflow.invoke.requested" not in event_types
    assert not any(event_type.startswith("workflow.intake.") for event_type in event_types)


def test_implicit_init_does_not_take_git_ownership_of_existing_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    target = tmp_path / "existing-code"
    target.mkdir()
    (target / "README.md").write_text("# existing code\n", encoding="utf-8")
    (target / "app.py").write_text("print('existing')\n", encoding="utf-8")

    response = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(target),
            "workspace": "default",
            "name": "existing-code",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["git_readiness"] == {
        "created": False,
        "head": "",
        "ready": False,
    }
    assert not (target / ".git").exists()
    assert (target / "app.py").read_text(encoding="utf-8") == "print('existing')\n"


def test_implicit_init_preserves_existing_config_and_blocks_unsafe_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    target = tmp_path / "existing"
    config_path = _write_config(target, state_dir=".runtime")
    before = hashlib.sha256(config_path.read_bytes()).hexdigest()

    initialized = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(target),
            "workspace": "default",
            "skip_instruction_docs": True,
        },
    )

    assert initialized.status_code == 201, initialized.text
    assert initialized.json()["kind"] == ""
    assert initialized.json()["config_generated"] == "existing"
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == before
    assert (target / ".runtime" / "kanban.json").is_file()

    unsafe = tmp_path / "unsafe"
    unsafe_config = _write_config(unsafe)
    unsafe_state = unsafe / ".zf"
    unsafe_state.mkdir()
    (unsafe_state / "events.jsonl").write_text("", encoding="utf-8")
    unsafe_before = hashlib.sha256(unsafe_config.read_bytes()).hexdigest()
    blocked = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={"root": str(unsafe), "workspace": "default"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["status"] == "admission_blocked"
    assert blocked.json()["inspection"]["admission"]["reason"] == "state_dir_partial"
    assert hashlib.sha256(unsafe_config.read_bytes()).hexdigest() == unsafe_before
    assert not (unsafe_state / "kanban.json").exists()


@pytest.mark.parametrize(
    ("metadata", "reason"),
    [
        (
            {
                "name": "../outside",
                "description": "invalid name must fail before config write",
            },
            "path-safe label",
        ),
        ({"name": 42}, "project name must be a string"),
        ({"description": ["invalid"]}, "project description must be a string"),
    ],
)
def test_implicit_init_rejects_invalid_project_metadata_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict,
    reason: str,
) -> None:
    client = _client(tmp_path, monkeypatch)
    target = tmp_path / "unsafe-name"

    response = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(target),
            "workspace": "default",
            **metadata,
        },
    )

    assert response.status_code == 422
    assert response.json()["status"] == "invalid_payload"
    assert reason in response.json()["reason"]
    assert not target.exists()
