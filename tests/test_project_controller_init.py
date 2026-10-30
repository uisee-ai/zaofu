"""Dashboard initialization materializes the versioned production controller."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from tests.test_project_admission import _client, _workspace_home  # noqa: F401
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog


def test_dashboard_default_uses_v5_with_local_assets(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    target = tmp_path / "new-project"
    response = client.post("/api/workspace/projects/init", headers={"x-zf-web-token": "test-token"},
                           json={"root": str(target), "name": "expense", "backend": "codex",
                                 "state_dir": ".runtime", "skip_instruction_docs": True})
    assert response.status_code == 201, response.text
    config = load_config(target / "zf.yaml")
    assert config.version == "5.0.1"
    assert config.project.state_dir == ".runtime"
    assert config.workflow.orchestration.normal_frontier_owner == "wrc"
    assert config.workflow.flow_metadata["task_pipeline"]["mode"] == "blocking"
    assert config.workflow.flow_metadata["task_pipeline"]["pools"]["impl"]["capacity"] == 8
    assert config.workflow.execution_profiles["bounded-direct-v1"].limits.token_budget == 0
    assert config.workflow.run_limits.token_budget == 0
    assert config.runtime.git.candidate_base_ref == config.runtime.git.ship_target_branch == "main"
    assert config.runtime.git.auto_ship_on_judge_passed is False
    docs = list(yaml.safe_load_all((target / "zf.yaml").read_text()))
    flow = next(d for d in docs if d["kind"] == "PrdFlow")
    assert flow["metadata"]["name"] == "zaofu-prd/v5-production"
    assert flow["spec"]["lanes"] == 8
    assert {source.path for source in config.skill_sources} == {"skills"}
    spec = next(d["spec"] for d in docs if d["kind"] == "ZfConfig")
    assert all(not Path(ref).is_absolute() and (target / ref).is_file() for ref in spec["profile_sources"])
    assert all((target / "skills" / skill / "SKILL.md").is_file() for role in config.roles for skill in role.skills)
    assert not any(e.type == "workflow.invoke.requested" for e in EventLog(target / ".runtime/events.jsonl").read_all())
    moved = tmp_path / "relocated"
    shutil.move(target, moved)
    from zf.core.skills import validate_skill_sources
    relocated = load_config(moved / "zf.yaml")
    assert validate_skill_sources(config=relocated, project_root=moved) == []
    assert relocated.workflow.flow_metadata == config.workflow.flow_metadata


@pytest.mark.parametrize("backend,mixed,verify", [
    ("claude-code", False, "claude-code"), ("codex", True, "claude-code"),
])
def test_v5_initialization_honors_provider_selection(tmp_path, monkeypatch, backend, mixed, verify):
    target = tmp_path / "provider-project"
    response = _client(tmp_path, monkeypatch).post(
        "/api/workspace/projects/init", headers={"x-zf-web-token": "test-token"},
        json={"root": str(target), "backend": backend, "mixed_enabled": mixed,
              "stack": "python", "skip_instruction_docs": True},
    )
    assert response.status_code == 201, response.text
    config = load_config(target / "zf.yaml")
    assert config.orchestrator.backend == backend
    assert {r.backend for r in config.roles if r.role_kind == "writer"} == {backend}
    assert {r.backend for r in config.roles if "verify-lane" in r.name} == {verify}
    assert all(not r.model for r in config.roles if r.backend == "claude-code")
    assert config.quality_gates["static"].enabled
    assert config.quality_gates["static"].required_checks == ["ruff check .", "pytest"]


def test_v5_existing_project_keeps_named_git_branch_and_code(tmp_path, monkeypatch):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "app.py").write_text("print('existing')\n")
    subprocess.run(["git", "init", "-b", "feature/export", str(target)], check=True, capture_output=True)
    response = _client(tmp_path, monkeypatch).post(
        "/api/workspace/projects/init", headers={"x-zf-web-token": "test-token"},
        json={"root": str(target), "skip_instruction_docs": True},
    )
    assert response.status_code == 201, response.text
    config = load_config(target / "zf.yaml")
    assert config.runtime.git.candidate_base_ref == config.runtime.git.ship_target_branch == "feature/export"
    assert not response.json()["git_readiness"]["created"]
    assert (target / "app.py").read_text() == "print('existing')\n"


@pytest.mark.parametrize("failure", ["missing_template", "different_lanes"])
def test_invalid_default_controller_fails_before_creating_project(tmp_path, monkeypatch, failure):
    import zf.cli.project_controller as controller
    target = tmp_path / "invalid"
    payload = {"root": str(target)}
    if failure == "missing_template":
        monkeypatch.setattr(controller, "read_flow_yaml", lambda _: None)
    else:
        payload["lanes"] = 2
    response = _client(tmp_path, monkeypatch).post(
        "/api/workspace/projects/init", headers={"x-zf-web-token": "test-token"}, json=payload,
    )
    assert response.status_code == 422, response.text
    assert response.json()["status"] == "invalid_payload"
    assert not target.exists()
