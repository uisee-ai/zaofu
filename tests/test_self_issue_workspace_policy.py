"""Workspace propagation tests for the centrally managed Self-Issue target."""

from __future__ import annotations

import json
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
from zf.core.workspace.registry import WorkspaceRegistry
from zf.web.server import create_app


_CENTRAL_POLICY = """\
self_issue:
  enabled: true
  provider: gitlab
  authorization_domain: gitlab.com
  target_project: runze.wu/zaofu-selfissue
  target_locked: true
  oauth_client_id: public-client-id
  oauth_redirect_uri: http://127.0.0.1:8002/
  default_publication_mode: gitlab
  delivery:
    enabled: true
    provider: github
    repository: rzwu-uisee/zaofu-self-issue
    remote_url: https://github.com/rzwu-uisee/zaofu-self-issue.git
    base_branch: dev
    branch_prefix: review
    merge_strategy: squash
    pr_sync_mode: manual_refresh
    auto_close_issue: false
  targets:
    gitlab:
      authorization_domain: gitlab.com
      project: runze.wu/zaofu-selfissue
      oauth_client_id: public-client-id
      oauth_redirect_uri: http://127.0.0.1:8002/
      auth_mode: oauth_pkce
    github:
      authorization_domain: github.com
      project: rzwu-uisee/zaofu-self-issue
      oauth_client_id: github-public-client-id
      auth_mode: device_flow
"""

_OLD_PROJECT_POLICY = """\
self_issue:
  enabled: true
  provider: gitlab
  authorization_domain: gitlab.com
  target_project: old-owner/old-project
  target_locked: true
"""


def _make_project(root: Path, *, name: str, extra_config: str = "") -> Path:
    root.mkdir(parents=True)
    (root / "zf.yaml").write_text(
        f"version: '1.0'\nproject:\n  name: {name}\n  state_dir: .zf\n"
        f"{extra_config}",
        encoding="utf-8",
    )
    state_dir = root / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="test"),
    )
    return state_dir


def test_registered_project_inherits_web_servers_locked_self_issue_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    host_root = tmp_path / "host"
    child_root = tmp_path / "child"
    host_state = _make_project(host_root, name="host", extra_config=_CENTRAL_POLICY)
    child_state = _make_project(
        child_root, name="child", extra_config=_OLD_PROJECT_POLICY,
    )
    child = WorkspaceRegistry().upsert_context(
        resolve_project_context(cwd=child_root),
    )
    app = create_app(
        host_state,
        config=load_config(host_root / "zf.yaml"),
        project_root=host_root,
    )
    client = TestClient(app)
    envelope = {
        "project_id": child.project_id,
        "idempotency_key": "capture-child-issue",
        "payload": {"description": "Child project failure"},
    }

    response = client.post(
        f"/api/projects/{child.project_id}/actions/self-issue-capture",
        headers={"x-zf-web-token": "test-token"},
        json=envelope,
    )

    assert response.status_code == 200, response.text
    intake = response.json()["intake"]
    assert response.json()["status"] == "intake_collecting"
    assert intake["target_binding"] == {
        "provider": "gitlab",
        "project": "runze.wu/zaofu-selfissue",
    }
    restored = client.post(
        f"/api/projects/{child.project_id}/actions/self-issue-get",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": child.project_id,
            "idempotency_key": "get-child-issue",
            "payload": {},
        },
    )
    assert restored.status_code == 200, restored.text
    target_policy = restored.json()["intake"]["target_policy"]
    assert target_policy["allowed_modes"] == ["gitlab", "github", "both"]
    assert target_policy["targets"]["github"]["project"] == (
        "rzwu-uisee/zaofu-self-issue"
    )
    assert json.loads(
        (child_state / "self-issues" / "intakes.json").read_text(encoding="utf-8")
    )[-1]["target_binding"]["project"] == "runze.wu/zaofu-selfissue"


def test_web_created_project_materializes_policy_for_its_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    host_root = tmp_path / "host"
    host_state = _make_project(host_root, name="host", extra_config=_CENTRAL_POLICY)
    app = create_app(
        host_state,
        config=load_config(host_root / "zf.yaml"),
        project_root=host_root,
    )
    client = TestClient(app)
    target = tmp_path / "created-project"

    initialized = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(target),
            "workspace": "default",
            "kind": "multi",
            "name": "created-project",
        },
    )

    assert initialized.status_code == 201, initialized.text
    config = load_config(target / "zf.yaml")
    assert config.self_issue.enabled is True
    assert config.self_issue.target_locked is True
    assert config.self_issue.target_project == "runze.wu/zaofu-selfissue"
    assert config.self_issue.automatic_detection_enabled is True
    assert config.self_issue.browser_capture_enabled is True
    assert config.self_issue.delivery.enabled is True
    assert config.self_issue.delivery.repository == "rzwu-uisee/zaofu-self-issue"
    assert config.self_issue.delivery.base_branch == "dev"
    assert config.self_issue.delivery.auto_close_issue is False
    assert {
        role.name
        for role in config.roles
    } >= {
        "orchestrator",
        "external-issue-triage",
        "external-issue-triage-verifier",
    }
    assert {
        stage.id
        for stage in config.workflow.stages
    } >= {
        "external-issue-triage",
        "external-issue-triage-verify",
    }

    preset_target = tmp_path / "preset-project"
    preset_initialized = client.post(
        "/api/workspace/projects/init",
        headers={"x-zf-web-token": "test-token"},
        json={
            "root": str(preset_target),
            "workspace": "default",
            "preset": "minimal",
        },
    )
    assert preset_initialized.status_code == 201, preset_initialized.text
    preset_config = load_config(preset_target / "zf.yaml")
    assert preset_config.self_issue.target_locked is True
    assert preset_config.self_issue.target_project == "runze.wu/zaofu-selfissue"
    assert preset_config.self_issue.delivery.repository == (
        "rzwu-uisee/zaofu-self-issue"
    )
    assert {
        stage.id
        for stage in preset_config.workflow.stages
    } >= {
        "external-issue-triage",
        "external-issue-triage-verify",
    }

    monkeypatch.chdir(target)
    assert main(["issue", "report", "Created project CLI failure"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "intake_collecting"
    assert result["intake"]["target_binding"]["project"] == (
        "runze.wu/zaofu-selfissue"
    )


def test_cli_project_init_inherits_locked_policy_from_invocation_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    host_root = tmp_path / "host"
    _make_project(host_root, name="host", extra_config=_CENTRAL_POLICY)
    target = host_root / "cli-project"
    monkeypatch.chdir(host_root)

    assert main([
        "project", "init", "--kind", "multi", "--name", "cli-project",
        "--root", str(target), "--create", "--no-workspace-register", "--json",
    ]) == 0
    capsys.readouterr()

    config = load_config(target / "zf.yaml")
    assert config.self_issue.target_locked is True
    assert config.self_issue.target_project == "runze.wu/zaofu-selfissue"
