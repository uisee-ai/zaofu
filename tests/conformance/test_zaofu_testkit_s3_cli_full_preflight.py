"""Startup admission must agree with the actual Task Pipeline dispatch contract.

All preflight implementations run unchanged; only physical tmux/worker effects
and the endless watcher loop are bounded. This is not a real-provider test.
"""

import json

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _blocking_runtime_at
from zf.cli.main import main
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import RoleLifecycleConfig
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def blocking_runtime(monkeypatch):
    with TemporaryDirectory(prefix="zf-k476-preflight-") as directory:
        project = Path(directory)
        project.rmdir()
        yield _blocking_runtime_at(project, monkeypatch)


class PreflightAdmitsUndispatchablePipeline(AssertionError):
    """Startup succeeds although configured Task Pipeline roles cannot dispatch."""


def test_cli_full_preflight_rejects_eager_task_pipeline_before_start(
    blocking_runtime, monkeypatch, capsys, record_property,
):
    runtime, transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.session.tmux_session = "zf-k476-preflight"
    config.project.state_dir = "runtime-state"
    for role in config.roles:
        role.lifecycle = RoleLifecycleConfig(mode="eager")
    (project / "zf.yaml").write_text(
        yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
    )

    cli_transport = TmuxTransport(TmuxSession(config.session.tmux_session, dry_run=True))
    cli_transport.send_task = transport.send_task  # type: ignore[method-assign]
    monkeypatch.setattr(cli_transport, "is_alive", lambda _role: False)
    for factory in (
        "zf.cli.start.make_transport",
        "zf.runtime.transport.make_transport",
    ):
        monkeypatch.setattr(factory, lambda *a, **kw: cli_transport)

    def bounded_watcher(watcher, **_kwargs):
        watcher.poll_once()
        watcher.stop()

    monkeypatch.setattr("zf.runtime.watcher.EventWatcher.run", bounded_watcher)
    monkeypatch.chdir(project)

    rc = main(["start", "--simulation"])
    output = capsys.readouterr()
    events = runtime.event_log.read_all()
    assert not (state / "loop.lock").exists()
    assert transport.contexts == []
    if rc != 0:
        assert rc == 1
        assert "on_demand" in output.err
        assert not any(e.type == "session.started" for e in events)
        assert not any(e.type == "task.pipeline.stage.dispatch_deferred" for e in events)
        return
    assert (state / "artifacts" / "workflow-inspect" / "inspect.json").is_file()
    assert any(e.type == "session.started" for e in events)
    deferrals = [e.payload for e in events if e.type == "task.pipeline.stage.dispatch_deferred"]
    assert deferrals, [(e.type, e.payload.get("reason")) for e in events]
    assert any("must be on_demand" in str(item) for item in deferrals), deferrals
    record_property("preflight_pipeline_observation", json.dumps({
        "exit_code": rc, "dispatch_count": len(transport.contexts), "deferrals": deferrals,
    }))
    raise PreflightAdmitsUndispatchablePipeline(
        f"start exited {rc} and emitted session.started; no dispatch; {deferrals!r}"
    )


def test_cli_start_full_preflight_rejects_mock_on_demand_role_without_skip(
    blocking_runtime, monkeypatch, capsys,
):
    runtime, _transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.session.tmux_session = "zf-k476-preflight-reject"
    config.project.state_dir = "runtime-state"
    (project / "zf.yaml").write_text(
        yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
    )
    monkeypatch.chdir(project)

    assert main(["start", "--simulation"]) == 1
    output = capsys.readouterr()
    assert "static dispatch preflight failed" in output.err.lower()
    assert "native session resume" in output.err.lower()
    assert not any(
        event.type == "session.started"
        for event in runtime.event_log.read_all()
    )
