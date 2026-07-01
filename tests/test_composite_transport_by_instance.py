"""Tests for G-INST-3: CompositeTransport routes by instance_id.

Previously CompositeTransport kept a dict keyed by role.name. With
multi-instance support, two replicas of dev (dev-1, dev-2) must route
to independent transport entries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.transport import (
    AttachHandle,
    CompositeTransport,
    DispatchContext,
    TransportAdapter,
    make_transport,
)


class _FakeTransport(TransportAdapter):
    """Records which instance_id each call was routed to."""

    def __init__(self, label: str):
        self.label = label
        self.spawned: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.contexts: list[DispatchContext | None] = []
        self.alive_queries: list[str] = []
        self.init_count = 0
        self.shutdown_count = 0

    def init(self, *, exclude_roles: set[str] | None = None) -> None:
        self.init_count += 1

    def is_session_running(self) -> bool:
        return True

    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd=None,
    ) -> None:
        # Use instance_id so two replicas of same name don't collide
        self.spawned.append(role.instance_id)

    def is_alive(self, role_name: str) -> bool:
        self.alive_queries.append(role_name)
        return True

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return True

    def send_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: DispatchContext | None = None,
    ) -> None:
        self.sent.append((role_name, prompt))
        self.contexts.append(context)

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return f"[{self.label}] log for {role_name}"

    def poll_events(self) -> list[ZfEvent]:
        return []

    def attach_handle(self, role_name: str | None) -> AttachHandle:
        return AttachHandle()

    def terminate(self, role_name: str) -> None:
        pass

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        self.shutdown_count += 1


class TestCompositeRoutesByInstance:
    def test_two_replicas_each_get_own_transport_entry(self, tmp_path: Path):
        t1 = _FakeTransport("t1")
        t2 = _FakeTransport("t2")
        router = CompositeTransport({"dev-1": t1, "dev-2": t2})

        router.send_task("dev-1", tmp_path / "b.md", "work on T1")
        router.send_task("dev-2", tmp_path / "b.md", "work on T2")

        assert t1.sent == [("dev-1", "work on T1")]
        assert t2.sent == [("dev-2", "work on T2")]

    def test_capture_log_routes_by_instance(self, tmp_path: Path):
        t1 = _FakeTransport("t1")
        t2 = _FakeTransport("t2")
        router = CompositeTransport({"dev-1": t1, "dev-2": t2})

        assert "t1" in router.capture_log("dev-1")
        assert "t2" in router.capture_log("dev-2")

    def test_is_alive_routes_by_instance(self):
        t1 = _FakeTransport("t1")
        t2 = _FakeTransport("t2")
        router = CompositeTransport({"dev-1": t1, "dev-2": t2})

        router.is_alive("dev-1")
        router.is_alive("dev-2")
        assert t1.alive_queries == ["dev-1"]
        assert t2.alive_queries == ["dev-2"]

    def test_send_task_forwards_dispatch_context(self, tmp_path: Path):
        t1 = _FakeTransport("t1")
        router = CompositeTransport({"dev-1": t1})
        context = DispatchContext(
            trace_id="trace-1",
            run_id="sess-1",
            task_id="T1",
            role_name="dev",
            instance_id="dev-1",
            backend="claude-code",
            briefing_path=tmp_path / "b.md",
        )

        router.send_task("dev-1", tmp_path / "b.md", "work", context=context)

        assert t1.contexts == [context]

    def test_init_and_shutdown_can_exclude_dedicated_run_manager_transport(self):
        workflow = _FakeTransport("workflow")
        run_manager = _FakeTransport("run-manager")
        router = CompositeTransport({
            "dev-1": workflow,
            "verify-1": workflow,
            "run-manager": run_manager,
        })

        router.init(exclude_roles={"run-manager"})
        router.shutdown(exclude_roles={"run-manager"})

        assert workflow.init_count == 1
        assert workflow.shutdown_count == 1
        assert run_manager.init_count == 0
        assert run_manager.shutdown_count == 0

    def test_shared_transport_is_not_excluded_when_only_run_manager_is_excluded(self):
        shared = _FakeTransport("shared")
        router = CompositeTransport({"dev": shared, "run-manager": shared})

        router.shutdown(exclude_roles={"run-manager"})

        assert shared.shutdown_count == 1


class TestMakeTransportWithReplicas:
    def test_make_transport_creates_one_entry_per_replica(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".zf").mkdir()
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", replicas=3)],
        )
        # After ZfConfig.__post_init__, cfg.roles has 3 entries with
        # instance_ids dev-1/dev-2/dev-3
        assert len(cfg.roles) == 3
        transport = make_transport(cfg, dry_run=True)
        # All 3 instance_ids must route successfully
        for inst_id in ("dev-1", "dev-2", "dev-3"):
            # is_alive should not raise — it just needs a transport to delegate to
            transport.is_alive(inst_id)

    def test_single_replica_uses_name_as_instance_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".zf").mkdir()
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev")],  # replicas defaults to 1
        )
        transport = make_transport(cfg, dry_run=True)
        # "dev" should be routable
        transport.is_alive("dev")
