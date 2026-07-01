from __future__ import annotations

import subprocess
from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleAutoscaleConfig,
    RoleConfig,
    ZfConfig,
)
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _Transport:
    def __init__(self) -> None:
        self.spawned: list[tuple[str, Path | None]] = []
        self.registered: list[str] = []
        self.terminated: list[str] = []
        self.sent: list[tuple[str, str]] = []

    def init(self) -> None:
        pass

    def is_session_running(self) -> bool:
        return True

    def register_role(self, role: RoleConfig, *, parent_instance_id: str | None = None) -> None:
        self.registered.append(role.instance_id)

    def spawn(self, role: RoleConfig, argv: list[str], *, cwd: Path | None = None) -> None:
        self.spawned.append((role.instance_id, cwd))

    def is_alive(self, role_name: str) -> bool:
        return True

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return True

    def send_task(self, role_name: str, briefing_path: Path, prompt: str, *, context=None) -> None:
        self.sent.append((role_name, prompt))

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list[ZfEvent]:
        return []

    def attach_handle(self, role_name: str | None):
        return None

    def terminate(self, role_name: str) -> None:
        self.terminated.append(role_name)

    def shutdown(self) -> None:
        pass


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
    event_log_from_project(state_dir).append(ZfEvent(type="loop.started", actor="zf-cli"))
    return state_dir


def _config(*, min_replicas: int = 1, max_replicas: int = 3) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        roles=[
            RoleConfig(
                name="dev",
                backend="python",
                triggers=["task.assigned"],
                autoscale=RoleAutoscaleConfig(
                    enabled=True,
                    min_replicas=min_replicas,
                    max_replicas=max_replicas,
                    cooldown_seconds=0,
                ),
            ),
        ],
    )


def test_autoscale_adds_runtime_worker_when_backlog_exceeds_pool(tmp_path: Path):
    state_dir = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    for index in range(3):
        store.add(Task(id=f"TASK-AUTO-{index}", title=f"Task {index}", status="backlog"))
    transport = _Transport()
    orchestrator = Orchestrator(state_dir, _config(), transport)

    decisions = orchestrator.run_once()

    assert any(decision.action == "scale_up" for decision in decisions)
    assert "dev-auto-0001" in [role.instance_id for role in orchestrator.all_roles()]
    # autoscaled instances live in _runtime_roles, not the zf.yaml view.
    assert "dev-auto-0001" in orchestrator._runtime_roles
    assert "dev-auto-0001" not in [role.instance_id for role in orchestrator.config.roles]
    assert transport.spawned and transport.spawned[0][0] == "dev-auto-0001"
    events = event_log_from_project(state_dir).read_all()
    assert any(event.type == "role.instance.allocated" for event in events)
    assert any(event.type == "autoscale.scale_up.completed" for event in events)


def test_autoscaled_worker_receives_backlog_after_static_worker_is_busy(tmp_path: Path):
    state_dir = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    for index in range(3):
        store.add(Task(id=f"TASK-POOL-{index}", title=f"Task {index}", status="backlog"))
    transport = _Transport()
    orchestrator = Orchestrator(state_dir, _config(max_replicas=2), transport)

    orchestrator.run_once()

    sent_roles = [role for role, _prompt in transport.sent]
    assert sent_roles == ["dev", "dev-auto-0001"]


def test_exact_instance_lookup_does_not_route_to_autoscale_pool(tmp_path: Path):
    state_dir = _state(tmp_path)
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    ).update_instance_meta(
        "dev-auto-0001",
        origin="autoscale",
        parent_role="dev",
        role_name="dev",
        backend="python",
        status="active",
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-STATIC",
        title="Static task",
        status="in_progress",
        assigned_to="dev",
    ))
    orchestrator = Orchestrator(
        state_dir,
        _config(min_replicas=2, max_replicas=2),
        _Transport(),
    )

    assert orchestrator._find_role_by_instance("dev").instance_id == "dev"


def test_stuck_recovery_uses_autoscaled_worker_own_task(tmp_path: Path):
    state_dir = _state(tmp_path)
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    ).update_instance_meta(
        "dev-auto-0001",
        origin="autoscale",
        parent_role="dev",
        role_name="dev",
        backend="python",
        status="active",
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-STATIC",
        title="Static task",
        status="in_progress",
        assigned_to="dev",
    ))
    store.add(Task(
        id="TASK-AUTO",
        title="Autoscaled task",
        status="in_progress",
        assigned_to="dev-auto-0001",
    ))
    transport = _Transport()
    orchestrator = Orchestrator(state_dir, _config(max_replicas=2), transport)
    auto_role = next(
        role for role in orchestrator.all_roles()
        if role.instance_id == "dev-auto-0001"
    )

    orchestrator._report_stuck_worker(auto_role)

    assert store.get("TASK-STATIC").status == "in_progress"
    recovered = store.get("TASK-AUTO")
    assert recovered.status == "backlog"
    assert recovered.assigned_to == "dev-auto-0001"


def test_respawn_preserves_worker_worktree_cwd(tmp_path: Path):
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "ZaoFu Test"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "zaofu-test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    state_dir = _state(tmp_path)
    config = _config(max_replicas=1)
    config.runtime.workdirs.enabled = True
    config.runtime.workdirs.mode = "worktree"
    config.runtime.git.writer_branch_prefix = "zf-test"
    transport = _Transport()
    orchestrator = Orchestrator(state_dir, config, transport)

    orchestrator._respawn_instance(orchestrator.config.roles[0])

    assert transport.spawned[-1] == (
        "dev",
        tmp_path / ".zf" / "workdirs" / "dev" / "project",
    )


def test_autoscale_restores_runtime_worker_from_role_sessions(tmp_path: Path):
    state_dir = _state(tmp_path)
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    ).update_instance_meta(
        "dev-auto-0001",
        origin="autoscale",
        parent_role="dev",
        role_name="dev",
        backend="python",
        status="active",
    )
    transport = _Transport()

    orchestrator = Orchestrator(state_dir, _config(max_replicas=2), transport)

    assert "dev-auto-0001" in [role.instance_id for role in orchestrator.all_roles()]
    # autoscaled instances live in _runtime_roles, not the zf.yaml view.
    assert "dev-auto-0001" in orchestrator._runtime_roles
    assert "dev-auto-0001" not in [role.instance_id for role in orchestrator.config.roles]
    assert "dev-auto-0001" in transport.registered


def test_worker_drain_request_retires_idle_autoscaled_worker(tmp_path: Path):
    state_dir = _state(tmp_path)
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    ).update_instance_meta(
        "dev-auto-0001",
        origin="autoscale",
        parent_role="dev",
        role_name="dev",
        backend="python",
        status="active",
    )
    writer = EventWriter(event_log_from_project(state_dir))
    writer.append(ZfEvent(
        type="worker.drain.requested",
        actor="web",
        payload={"instance_id": "dev-auto-0001", "reason": "test"},
    ))
    transport = _Transport()
    orchestrator = Orchestrator(
        state_dir,
        _config(min_replicas=2, max_replicas=2),
        transport,
    )

    decisions = orchestrator.run_once()

    assert any(decision.action == "drain" for decision in decisions)
    assert any(decision.action == "scale_down" for decision in decisions)
    assert "dev-auto-0001" in transport.terminated
    meta = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    ).instance_meta()["dev-auto-0001"]
    assert meta["status"] == "retired"
