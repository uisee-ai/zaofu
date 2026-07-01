from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from zf.cli.workflow import _run_gates, _run_hooks
from zf.core.config.schema import ProjectConfig, RoleConfig, WorkflowConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.workspace import stable_project_id
from zf.runtime.gate_projection import project_gate_projection
from zf.runtime.hook_registry import project_hook_registry


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="demo", state_dir=".zf"),
        workflow=WorkflowConfig(harness_profile="strict"),
        roles=[
            RoleConfig(
                name="dev",
                instance_id="dev-1",
                backend="codex",
                permission_mode="allowlist",
                allowed_tools=["Read", "Edit"],
                skills=["zf-cr"],
            ),
            RoleConfig(
                name="review",
                instance_id="review-1",
                backend="claude-code",
                permission_mode="bypass",
            ),
        ],
    )


def _state(root: Path) -> Path:
    state_dir = root / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    return state_dir


def _write_zf_yaml(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "zf.yaml").write_text(
        "\n".join([
            'version: "1.0"',
            "project:",
            "  name: demo",
            "  state_dir: .zf",
            "workflow:",
            "  harness_profile: strict",
            "roles:",
            "  - name: dev",
            "    backend: codex",
            "    permission_mode: allowlist",
            "    allowed_tools: [Read, Edit]",
            "  - name: review",
            "    backend: claude-code",
        ])
        + "\n",
        encoding="utf-8",
    )


def test_hook_registry_projects_configured_wired_and_unwired_hooks(tmp_path: Path) -> None:
    root = tmp_path / "project"
    state_dir = _state(root)
    codex_dir = root / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text("{}", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="codex.hook.stop", actor="codex"))
    log.append(
        ZfEvent(
            type="hook.write_failed",
            actor="zf",
            payload={"error": "receiver failed"},
        )
    )

    registry = project_hook_registry(
        state_dir,
        config=_config(),
        project_root=root,
    )

    assert registry["schema_version"] == "hook-registry.v1"
    assert registry["summary"]["configured"] >= 5
    assert registry["summary"]["wired"] >= 1
    assert registry["summary"]["experimental_unwired"] == 4
    assert registry["recent_failures"][-1]["error"] == "receiver failed"
    assert any(
        row["id"] == "codex.stop"
        and row["status"] == "configured"
        and row["last_event_id"]
        for row in registry["hooks"]
    )
    assert any(
        row["source"] == "task_lifecycle_hooks"
        and row["status"] == "experimental_unwired"
        and row["enabled"] is False
        for row in registry["hooks"]
    )


def test_gate_projection_summarizes_roles_providers_hooks_and_web_gate(tmp_path: Path) -> None:
    root = tmp_path / "project"
    state_dir = _state(root)

    projection = project_gate_projection(
        state_dir,
        config=_config(),
        project_root=root,
        allowed_actions=["chat-orchestrator", "create-task"],
        web_token_configured=True,
        web_authorization_available=True,
        web_mutation_mode="token",
    )

    assert projection["schema_version"] == "gate-projection.v1"
    assert projection["summary"]["roles"] == 2
    assert projection["summary"]["providers"] >= 5
    assert projection["summary"]["hooks"] >= 1
    assert projection["summary"]["warnings"] >= 1
    role_gate = next(row for row in projection["gates"] if row["id"] == "role.dev-1")
    assert role_gate["permission_mode"] == "allowlist"
    assert role_gate["blocking"] is True
    web_gate = next(row for row in projection["gates"] if row["id"] == "web.mutation")
    assert web_gate["status"] == "enabled"
    assert web_gate["mutation_mode"] == "token"
    assert web_gate["allowed_action_count"] == 2
    assert any(
        row["id"].startswith("hook.task-lifecycle.")
        and row["status"] == "unwired"
        for row in projection["gates"]
    )


def test_workflow_cli_exposes_gate_and_hook_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "project"
    state_dir = _state(root)
    _write_zf_yaml(root)
    monkeypatch.chdir(root)

    gate_args = argparse.Namespace(format="json", state_dir=str(state_dir))
    hook_args = argparse.Namespace(format="json", state_dir=str(state_dir))

    assert _run_gates(gate_args) == 0
    gate_out = json.loads(capsys.readouterr().out)
    assert gate_out["schema_version"] == "gate-projection.v1"
    assert gate_out["summary"]["roles"] == 2

    assert _run_hooks(hook_args) == 0
    hook_out = json.loads(capsys.readouterr().out)
    assert hook_out["schema_version"] == "hook-registry.v1"
    assert hook_out["summary"]["experimental_unwired"] == 4


def test_gate_and_hook_projection_web_endpoints_are_project_scoped(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    root = tmp_path / "project"
    state_dir = _state(root)
    config = _config()
    client = TestClient(create_app(state_dir, config=config, project_root=root))
    project_id = stable_project_id(name="demo", root=root)

    gate = client.get(f"/api/projects/{project_id}/gate-projection")
    hook = client.get(f"/api/projects/{project_id}/hook-registry")
    legacy_gate = client.get("/api/gate-projection")
    snapshot = client.get("/api/snapshot")

    assert gate.status_code == 200
    assert gate.json()["schema_version"] == "gate-projection.v1"
    assert hook.status_code == 200
    assert hook.json()["schema_version"] == "hook-registry.v1"
    assert legacy_gate.status_code == 200
    assert legacy_gate.json()["summary"]["roles"] == 2
    assert snapshot.status_code == 200
    assert snapshot.json()["gate_projection"]["schema_version"] == "gate-projection.v1"
    assert snapshot.json()["hook_registry"]["schema_version"] == "hook-registry.v1"
