"""Graceful shutdown must capture each transport instance before teardown."""

from types import SimpleNamespace

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.state.session import SessionStore
from zf.runtime.shutdown import GracefulShutdown
from zf.runtime.transport import CompositeTransport


@pytest.mark.parametrize("instances", [("impl",), ("impl-1",), ("impl-1", "impl-2")])
def test_graceful_shutdown_preserves_instance_logs(tmp_path, instances):
    state = tmp_path / "runtime-state"
    state.mkdir()
    SessionStore(state / "session.yaml").create(project_root=str(tmp_path))
    config = ZfConfig(project=ProjectConfig(name="stop-logs", state_dir=str(state)), roles=[
        RoleConfig(name="impl", instance_id=instance, backend="mock") for instance in instances
    ])
    alive, captured = set(instances), []

    def capture(instance, lines=200):
        # Physical pane/SDK handles are instance-addressed, including when a
        # CompositeTransport fallback resolves a single bare role to an adapter.
        if instance not in alive:
            raise KeyError(instance)
        captured.append(instance)
        return f"owned output from {instance}\n"

    adapter = SimpleNamespace(capture_log=capture, shutdown=lambda: alive.clear())
    transport = CompositeTransport({instance: adapter for instance in instances})
    GracefulShutdown(state, transport, config=config).execute()
    assert captured == list(instances)
    assert not alive
    assert {p.name: p.read_text() for p in (state / "logs").glob("*.log")} == {
        f"{instance}.log": f"owned output from {instance}\n" for instance in instances
    }
