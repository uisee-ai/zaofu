"""Real watcher/tmux, mock Provider: all approval surfaces start execution."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from tests.e2e.test_light_dual_entry_mock_e2e import _setup
from zf.cli.main import main
from zf.core.events import EventLog
from zf.core.state.session import SessionStore
from zf.runtime import workflow_runtime_start
from zf.runtime.workflow_start import WorkflowStartService
from zf.web.server import create_app

pytestmark = [pytest.mark.host, pytest.mark.mock_e2e]


@pytest.mark.parametrize("surface", ["cli", "kanban", "channel"])
def test_approved_start_launches_runtime_and_dispatches_without_manual_start(
    monkeypatch, capsys, workflow_runtime_launcher, surface,
):
    if not shutil.which("tmux"):
        pytest.skip("tmux is required for the explicit runtime process tier")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(tempfile.mkdtemp(prefix=f"zf-autostart-{surface}-{stamp}-"))
    service = None
    try:
        service, task, authority = _setup(root, "channel" if surface == "channel" else "kanban")
        config_path = root / "zf.yaml"
        text = config_path.read_text().replace(
            "  project: {name: demo, state_dir: .zf}",
            f"  project: {{name: demo, state_dir: .zf}}\n  session: {{tmux_session: zf-autostart-{root.name[-8:]}}}",
        )
        config_path.write_text(text)
        from zf.core.config.loader import load_config
        service.config = load_config(config_path)
        assert {r.backend for r in service.config.roles} == {"mock"}
        SessionStore(service.state_dir / "session.yaml").create(project_root=str(root))
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text(".zf/\nartifacts/\ndocs/intake/\n")
        subprocess.run(["git", "-C", str(root), "add", "zf.yaml", ".gitignore", "requirement.md"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Runtime Test", "-c", "user.email=runtime@example.invalid",
                        "commit", "-qm", "test: initialize isolated mock project"], check=True)
        monkeypatch.chdir(root)
        monkeypatch.setattr(workflow_runtime_start, "ensure_project_runtime", workflow_runtime_launcher.real)
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "isolated-web-token")
        monkeypatch.setenv("ZF_WORKFLOW_ACTION_TOKEN", "isolated-cli-token")
        for key in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR", "ZF_TMUX_SESSION", "ZF_CLI_CMD"):
            monkeypatch.delenv(key, raising=False)
        start = WorkflowStartService(service.state_dir, service.config, project_root=root)
        route = next(r for r in start.routes(task_id=task.id)["routes"] if r.get("entry_class") == "light_adapter")
        payload = {"task_id": task.id, "route_id": route["route_id"], "objective": task.contract.behavior,
                   "parameters": {"target_root": str(root)}}
        if surface == "channel":
            payload.update(channel_id=authority["channel_id"], thread_id="main")
        proposed = start.propose(service.writer, payload, actor="operator", origin=surface)
        assert proposed["ok"], proposed
        assert not (service.state_dir / "processes" / "watcher.pid.json").exists()
        assert not any(e.type == "runtime.start.requested" for e in service.writer.event_log.read_all())
        if surface == "cli":
            args = ["workflow", "start", "--apply", "--proposal-event-id", proposed["proposal_event_id"],
                    "--authorization-ref", "operator:autostart-test", "--authorization-token", "isolated-cli-token", "--format", "json"]
            code = main(args)
            result = json.loads(capsys.readouterr().out)
            assert code == 0, result
            assert main(args) == 0
            capsys.readouterr()
        else:
            with TestClient(create_app(service.state_dir, config=service.config, project_root=root)) as client:
                approval = {**proposed["payload"], "proposal_event_id": proposed["proposal_event_id"]}
                response = client.post("/api/actions/workflow-start", json=approval, headers={"X-ZF-Web-Token": "isolated-web-token"})
                result = response.json()
                assert response.status_code < 300, result
                replay = client.post("/api/actions/workflow-start", json=approval, headers={"X-ZF-Web-Token": "isolated-web-token"})
                assert replay.status_code < 300, replay.json()
        assert result["runtime_start"]["status"] == "running", result
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            events = EventLog(service.state_dir / "events.jsonl").read_all()
            if any(e.type == "task.attempt.sent" for e in events):
                break
            time.sleep(0.2)
        assert any(e.type == "workflow.invoke.accepted" for e in events)
        assert any(e.type == "task.attempt.sent" for e in events), [e.type for e in events[-20:]]
        assert sum(e.type == "workflow.invoke.requested" for e in events) == 1
        assert sum(e.type == "runtime.start.requested" for e in events) == 1
    finally:
        if service is not None:
            service.writer.emit("simulation.done", actor="test", payload={"source": "workflow-runtime-autostart", "surface": surface})
            subprocess.run([sys.executable, "-m", "zf.cli.main", "stop", "--fast"], cwd=root,
                           env={**os.environ, "ZF_WORKSPACE_HOME": str(root / "workspace")},
                           capture_output=True, text=True, timeout=30)
            assert not workflow_runtime_start._runtime_owner(service.state_dir), f"runtime cleanup failed: {root}"
        shutil.rmtree(root)
