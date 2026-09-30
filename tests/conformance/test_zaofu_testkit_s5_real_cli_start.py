"""Opt-in real Claude CLI start/preflight/dispatch boundary.

This tier proves the CLI gate and first physical dispatch only.  It does not
claim semantic provider completion or candidate delivery; cleanup is always
performed through the real ``zf stop --force`` path.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _blocking_runtime_at
from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import (
    _assert_gone,
    _process_ids,
)
from zf.cli.main import main
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import RoleLifecycleConfig
from zf.core.events import ZfEvent
from zf.runtime.tmux import TmuxSession
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.transport import TmuxTransport


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_claude_cli_full_preflight_reaches_owned_dispatch_and_stops(
    monkeypatch, record_property,
):
    if os.environ.get("ZF_RUN_REAL_CLAUDE_START_PREFLIGHT", "").lower() not in {
        "1", "true", "yes",
    }:
        pytest.skip("set ZF_RUN_REAL_CLAUDE_START_PREFLIGHT=1 for the explicit CLI tier")
    if shutil.which("claude") is None or shutil.which("tmux") is None:
        pytest.skip("real CLI tier requires claude and tmux")
    with TemporaryDirectory(prefix="zf-k481-real-cli-") as directory:
        project = Path(directory)
        project.rmdir()
        original_activation = Orchestrator._ensure_role_active
        runtime, _fixture_transport, _admission = _blocking_runtime_at(project, monkeypatch)
        monkeypatch.setattr(Orchestrator, "_ensure_role_active", original_activation)
        state, config = runtime.state_dir, runtime.config
        session_name = f"zf-k481-{uuid.uuid4().hex}"
        config.session.tmux_session = session_name
        config.project.state_dir = "runtime-state"
        role = config.roles[0]
        role.backend = "claude-code"
        role.permission_mode = "allowlist"
        role.allowed_tools = []
        role.lifecycle = RoleLifecycleConfig(mode="on_demand")
        (project / "zf.yaml").write_text(
            yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
        )
        live_snapshot: dict[str, object] = {}
        observed_pids: set[str] = set()

        def bounded_watcher(watcher, **_kwargs):
            watcher.poll_once()
            tmux = TmuxSession(session_name)
            actual = TmuxTransport(tmux).lifecycle_snapshot("impl-1")
            live_snapshot.update(
                session_alive=tmux.has_session(),
                pane_pid=actual.pane_pid,
                process_alive=actual.alive,
                current_command=actual.current_command,
            )
            if tmux.has_session():
                observed_pids.update(_process_ids(tmux, "impl-1"))
            watcher.stop()

        monkeypatch.setattr("zf.runtime.watcher.EventWatcher.run", bounded_watcher)
        monkeypatch.chdir(project)
        try:
            rc = main(["start", "--simulation"])
            events = runtime.event_log.read_all()
            assert rc == 0
            assert any(event.type == "session.started" for event in events)
            dispatched = [
                event for event in events
                if event.type == "task.pipeline.stage.dispatched"
            ]
            assert len(dispatched) == 1
            assert live_snapshot["session_alive"] and live_snapshot["process_alive"], live_snapshot
            assert str(live_snapshot["pane_pid"]).isdigit(), live_snapshot
            assert live_snapshot["current_command"] not in {"", "bash", "sh", "zsh", "cat"}
            assert not TmuxSession(session_name).has_session()
            _assert_gone(observed_pids)
            snapshot = {
                "session": session_name,
                "dispatches": len(dispatched),
                "stage": dispatched[0].payload.get("task_pipeline_stage"),
                "live_snapshot": live_snapshot,
                "observed_pids": sorted(observed_pids),
                "post_simulation_session_alive": TmuxSession(session_name).has_session(),
                "event_types": sorted({event.type for event in events}),
            }
            record_property("real_cli_start", json.dumps(snapshot))
        finally:
            try:
                runtime.event_writer.append(ZfEvent(
                    type="simulation.done", actor="testkit",
                    payload={"reason": "bounded-start-probe-finished", "session": session_name},
                ))
            finally:
                # Use the real CLI stop route, even when startup assertions fail.
                assert main(["stop", "--force"]) == 0
                assert not TmuxSession(session_name).has_session()
                assert not (state / "loop.lock").exists()
                _assert_gone(observed_pids)
