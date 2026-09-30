"""Restart must not spawn an active Task-stage session in a role worktree."""

import json
from pathlib import Path

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _blocking_runtime_at
from zf.cli.main import main
from zf.core.config.render import renderable_config_to_primitive
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.session_tailer import claude_session_path


@pytest.mark.parametrize("damage", [None, "missing-workspace"])
def test_cli_restart_preserves_task_stage_spawn_cwd(tmp_path, monkeypatch, damage):
    provider_home = tmp_path / "provider-home"
    provider_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: provider_home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    runtime, _transport, _ = _blocking_runtime_at(tmp_path / "project", monkeypatch)
    runtime.config.session.tmux_session = f"zf-k543-offline-{tmp_path.name}"
    role = runtime.config.roles[0]
    role.backend = "claude-code"
    role.permission_mode = "allowlist"
    role.allowed_tools = ["Read"]
    runtime.run_once()
    [dispatch] = [e for e in runtime.event_log.read_all()
                  if e.type == "task.pipeline.stage.dispatched"]
    workdir = Path(dispatch.payload["workdir"])
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml",
                                   str(runtime.project_root))
    binding = registry.task_stage_bindings()[dispatch.payload["task_stage_session_binding"]]
    registry.mark_spawned(role.instance_id)
    transcript = claude_session_path(str(workdir), binding["session_id"])
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": binding["session_id"]}) + "\n")
    before = transcript.read_bytes()
    (runtime.project_root / "zf.yaml").write_text(
        yaml.safe_dump(renderable_config_to_primitive(runtime.config)))
    if damage:
        workdir.rename(workdir.with_name("missing-project"))
    monkeypatch.chdir(runtime.project_root)
    result = main(["start", "--dry-run", "--skip-workflow-inspect"])
    launch_path = runtime.state_dir / "workdirs" / role.instance_id / "runtime" / "launch.json"
    if damage:
        assert result != 0
        assert not launch_path.exists()
        assert not (runtime.state_dir / "loop.lock").exists()
    else:
        assert result == 0
        launch = json.loads(launch_path.read_text())
        assert launch["cwd"] == str(workdir)
        assert launch["is_resume"] is True
        assert launch["session_id"] == binding["session_id"]
    assert transcript.read_bytes() == before
