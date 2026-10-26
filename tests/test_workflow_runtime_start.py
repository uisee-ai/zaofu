from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock
import json
import os

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime import workflow_runtime_start as runtime
from zf.runtime.workflow_runtime_start import ensure_project_runtime


def _context(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    state = root / ".custom-state"
    state.mkdir()
    return root, state, EventWriter(EventLog(state / "events.jsonl"))


def test_existing_runtime_is_reused_without_spawning(tmp_path, monkeypatch):
    root, state, writer = _context(tmp_path)
    spawn = Mock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(runtime, "_ready_owner", lambda _: 777)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    result = ensure_project_runtime(project_root=root, state_dir=state, writer=writer)
    assert result == {"ok": True, "status": "running", "pid": 777, "launched": False}
    assert writer.event_log.read_all() == []


def test_launch_is_detached_project_scoped_and_waits_for_readiness(tmp_path, monkeypatch):
    root, state, writer = _context(tmp_path)
    monkeypatch.setenv("ZF_WORKFLOW_ACTION_TOKEN", "operator-only")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "web-only")
    monkeypatch.setenv("ZF_PROJECT_ROOT", "/outer/project")
    monkeypatch.setenv("ZF_STATE_DIR", "/outer/project/.runtime")
    monkeypatch.setenv("ZF_TMUX_SESSION", "outer-session")
    monkeypatch.setattr(runtime, "_ready_owner", Mock(side_effect=[0, 0, 777]))
    monkeypatch.setattr(runtime, "_runtime_owner", lambda _: {})
    monkeypatch.setattr(runtime, "_process_identity", lambda _: "birth-777")
    child = Mock(pid=777, poll=Mock(return_value=None))
    spawn = Mock(return_value=child)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    result = ensure_project_runtime(project_root=root, state_dir=state, writer=writer)
    assert result["ok"] and result["launched"]
    kwargs = spawn.call_args.kwargs
    assert kwargs["cwd"] == root
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == runtime.subprocess.DEVNULL
    assert "ZF_WORKFLOW_ACTION_TOKEN" not in kwargs["env"]
    assert "ZF_WEB_ACTION_TOKEN" not in kwargs["env"]
    assert kwargs["env"]["ZF_PROJECT_ROOT"] == str(root)
    assert kwargs["env"]["ZF_STATE_DIR"] == str(state)
    assert "ZF_TMUX_SESSION" not in kwargs["env"]
    assert spawn.call_args.args[0][-1] == "start"
    assert Path(result["log_ref"]).stat().st_mode & 0o777 == 0o600
    assert [e.type for e in writer.event_log.read_all()] == ["runtime.start.requested"]


@pytest.mark.parametrize("exit_code, expected_status", [(3, "failed"), (None, "starting")])
def test_failed_or_slow_start_does_not_report_running(tmp_path, monkeypatch, exit_code, expected_status):
    root, state, writer = _context(tmp_path)
    monkeypatch.setattr(runtime, "_ready_owner", lambda _: 0)
    monkeypatch.setattr(runtime, "_runtime_owner", lambda _: {})
    monkeypatch.setattr(runtime, "_process_identity", lambda _: "birth")
    monkeypatch.setattr(runtime.subprocess, "Popen", Mock(return_value=Mock(pid=777, poll=Mock(return_value=exit_code))))
    result = ensure_project_runtime(project_root=root, state_dir=state, writer=writer, timeout_seconds=0)
    assert result["ok"] is False
    assert result["status"] == expected_status
    assert writer.event_log.read_all()[-1].type == (
        "runtime.start.failed" if exit_code is not None else "runtime.start.requested"
    )


def test_slow_start_keeps_accepted_submission_pending(tmp_path, workflow_runtime_launcher):
    from tests.test_web_workflow_proposals import _write_config

    path = tmp_path / "zf.yaml"
    _write_config(path)
    workflow_runtime_launcher.return_value = {
        "ok": False, "status": "starting", "pid": 777, "launched": True,
        "reason": "project runtime is still starting", "log_ref": "startup.log",
    }
    accepted = {"ok": True, "status": "accepted", "workflow_invoke_event_id": "invoke-one",
                "workflow_invoke_status": "pending_consumer"}
    result = runtime.ensure_submitted_workflow_runtime(accepted, config_path=path)
    assert result["ok"] is True
    assert result["status"] == "accepted"
    assert result["_status_code"] == 202
    assert result["workflow_invoke_event_id"] == "invoke-one"
    assert result["workflow_invoke_status"] == "pending_consumer"
    assert result["runtime_start"]["status"] == "starting"
    assert not result.get("blockers")
    assert "ready" not in result["next_action"]


def test_concurrent_requests_share_one_launcher(tmp_path, monkeypatch):
    root, state, writer = _context(tmp_path)
    ready = []
    def spawn(*args, **kwargs):
        ready.append(777)
        return Mock(pid=777, poll=Mock(return_value=None))
    launch = Mock(side_effect=spawn)
    monkeypatch.setattr(runtime, "_ready_owner", lambda _: ready[0] if ready else 0)
    monkeypatch.setattr(runtime, "_runtime_owner", lambda _: {})
    monkeypatch.setattr(runtime, "_process_identity", lambda _: "birth")
    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ensure_project_runtime(project_root=root, state_dir=state, writer=writer), range(4)))
    assert all(r["ok"] for r in results)
    assert launch.call_count == 1


def test_retry_waits_for_existing_start_and_does_not_duplicate(tmp_path, monkeypatch):
    root, state, writer = _context(tmp_path)
    record = state / "processes" / "workflow-runtime-start.json"
    record.parent.mkdir()
    record.write_text(json.dumps({"pid": 777, "process_identity": "birth"}))
    monkeypatch.setattr(runtime, "_ready_owner", Mock(side_effect=[0, 777]))
    monkeypatch.setattr(runtime, "_runtime_owner", lambda _: {})
    monkeypatch.setattr(runtime, "_process_identity", lambda _: "birth")
    spawn = Mock(side_effect=AssertionError("existing startup must be reused"))
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    result = ensure_project_runtime(project_root=root, state_dir=state, writer=writer)
    assert result["ok"] and not result["launched"]


def test_readiness_is_bound_to_the_live_watcher_epoch(tmp_path, monkeypatch):
    _, state, _ = _context(tmp_path)
    processes = state / "processes"
    processes.mkdir()
    guard = {"owner_pid": os.getpid(), "epoch": "current"}
    (processes / "watcher.pid.json").write_text(json.dumps(guard))
    monkeypatch.setattr(runtime, "_runtime_owner", lambda _: guard)
    (processes / "runtime-ready.json").write_text(json.dumps({**guard, "epoch": "stale"}))
    assert runtime._ready_owner(state) == 0
    runtime.mark_runtime_ready(state)
    assert runtime._ready_owner(state) == os.getpid()


def test_rejected_submit_never_starts_runtime(tmp_path, workflow_runtime_launcher):
    result = {"status": "STOP", "blockers": [{"kind": "invalid_source"}]}
    assert runtime.ensure_submitted_workflow_runtime(result, config_path=tmp_path / "missing.yaml") == result
    workflow_runtime_launcher.assert_not_called()


def test_failed_start_preserves_proposal_for_retry_without_duplicate_invoke(
    tmp_path, monkeypatch, capsys, workflow_runtime_launcher,
):
    from tests.e2e.test_light_dual_entry_mock_e2e import _setup
    from zf.cli.main import main
    from zf.runtime.workflow_start import WorkflowStartService

    service, task, _ = _setup(tmp_path, "kanban")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZF_WORKFLOW_ACTION_TOKEN", "retry-test-only")
    start = WorkflowStartService(service.state_dir, service.config, project_root=tmp_path)
    route = next(r for r in start.routes(task_id=task.id)["routes"] if r.get("entry_class") == "light_adapter")
    proposed = start.propose(service.writer, {
        "task_id": task.id, "route_id": route["route_id"], "objective": task.contract.behavior,
        "parameters": {"target_root": str(tmp_path)},
    }, actor="operator", origin="cli")
    assert proposed["ok"], proposed
    workflow_runtime_launcher.assert_not_called()
    workflow_runtime_launcher.side_effect = [
        {"ok": False, "status": "failed", "reason": "tmux unavailable", "log_ref": "startup.log"},
        {"ok": True, "status": "running", "pid": 123, "launched": True},
    ]
    args = ["workflow", "start", "--apply", "--proposal-event-id", proposed["proposal_event_id"],
            "--authorization-ref", "operator:test", "--authorization-token", "retry-test-only", "--format", "json"]
    assert main(args) != 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["runtime_start"]["status"] == "failed"
    assert failed["ok"] is False
    tampered = {**proposed["payload"], "parameters": {**proposed["payload"]["parameters"], "target_root": "/different"}}
    assert start.preview(tampered, require_bindings=True)["ok"] is False
    assert main(args) == 0
    retried = json.loads(capsys.readouterr().out)
    assert retried["runtime_start"]["status"] == "running"
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "already_resolved"
    assert workflow_runtime_launcher.call_count == 2
    assert sum(e.type == "workflow.invoke.requested" for e in service.writer.event_log.read_all()) == 1
