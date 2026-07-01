from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.operation_projection import project_operation, project_task_operations
from zf.runtime.progress_projection import phase_regression, project_task_progress
from zf.runtime.provider_health import project_provider_health
from zf.web.server import create_app


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(state_dir / "events.jsonl"))


class _StubTransport:
    def send_task(self, role_name: str, briefing_path: Path, prompt: str, **kwargs) -> None:
        pass

    def is_alive(self, role_name: str) -> bool:
        return True

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list[ZfEvent]:
        return []


def _orchestrator(state_dir: Path) -> Orchestrator:
    config = ZfConfig(
        project=ProjectConfig(name="demo", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock", role_kind="reader"),
            RoleConfig(name="test", backend="mock", role_kind="reader"),
        ],
    )
    return Orchestrator(state_dir, config, _StubTransport())


def _write_active_fanout_manifest(
    state_dir: Path,
    fanout_id: str,
    *,
    task_id: str = "TASK-FAN",
) -> None:
    fanout_dir = state_dir / "fanouts" / fanout_id
    fanout_dir.mkdir(parents=True, exist_ok=True)
    (fanout_dir / "manifest.json").write_text(
        json.dumps({
            "fanout_id": fanout_id,
            "status": "running",
            "aggregate": {"status": ""},
            "children": [{
                "child_id": "review",
                "role_instance": "review",
                "status": "dispatched",
                "task_id": task_id,
            }],
        }),
        encoding="utf-8",
    )


def _fanout_started_event(
    fanout_id: str,
    *,
    task_id: str = "TASK-FAN",
) -> ZfEvent:
    return ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        task_id=task_id,
        payload={
            "fanout_id": fanout_id,
            "stage_id": f"task-fanout-{task_id}",
            "topology": "kernel_mediated",
            "target_ref": "candidate/F-1",
            "expected_children": [{"child_id": "review"}],
        },
    )


def test_provider_health_projects_provider_stop_and_redacts_secrets(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    writer.append(ZfEvent(
        type="agent.api_blocked",
        actor="dev-1",
        task_id="TASK-1",
        payload={
            "backend": "claude-code",
            "role": "dev",
            "instance_id": "dev-1",
            "dispatch_id": "disp-1",
            "reason": "auth_error",
            "message": "Authorization: Bearer sk-test ~/.claude.json",
        },
    ))

    projection = project_provider_health(state_dir)

    assert projection["status"] == "blocked"
    provider = projection["providers"][0]
    assert provider["status"] == "blocked"
    assert provider["requires_operator"] is True
    text = json.dumps(provider, ensure_ascii=False)
    assert "sk-test" not in text
    assert "~/.claude" not in text


def test_task_progress_tracks_structured_progress_and_ignores_regression(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    writer.append(ZfEvent(
        type="phase.progressed",
        actor="dev-1",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "role": "dev",
            "instance_id": "dev-1",
            "phase": "review",
            "source": "worker",
        },
    ))
    writer.append(ZfEvent(
        type="worker.progress",
        actor="dev-1",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "role": "dev",
            "instance_id": "dev-1",
            "phase": "implement",
            "message": "patching",
            "source": "worker",
            "percent": 50,
        },
    ))

    events = EventLog(state_dir / "events.jsonl").read_all()
    regressed, current = phase_regression(
        events,
        task_id="TASK-1",
        attempted_phase="implement",
    )
    projection = project_task_progress(state_dir, "TASK-1")

    assert regressed is True
    assert current == "review"
    assert projection["current_phase"] == "review"
    assert projection["latest_progress"]["message"] == "patching"
    assert projection["diagnostics"]


def test_operation_projection_groups_dispatch_timeline_and_web_routes(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="Implement operation", status="in_progress"),
    )
    writer = _writer(state_dir)
    writer.append(ZfEvent(
        type="task.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "dispatch_id": "disp-1",
            "role": "dev",
            "instance_id": "dev-1",
            "backend": "codex",
        },
    ))
    writer.append(ZfEvent(
        type="worker.progress",
        actor="dev-1",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "role": "dev",
            "instance_id": "dev-1",
            "phase": "implement",
            "message": "working",
            "source": "worker",
        },
    ))
    writer.append(ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-1",
        payload={
            "dispatch_id": "disp-1",
            "files_touched": ["src/zf/runtime/operation_projection.py"],
        },
    ))

    operation = project_operation(state_dir, "disp-1")
    task_operations = project_task_operations(state_dir, "TASK-1")

    assert operation["task_id"] == "TASK-1"
    assert operation["state"] == "progressed"
    assert len(operation["timeline"]) == 3
    assert operation["evidence_refs"][0]["kind"] == "files_touched"
    assert task_operations["operations"][0]["dispatch_id"] == "disp-1"

    client = TestClient(create_app(state_dir))
    assert client.get("/api/provider-health").status_code == 200
    assert client.get("/api/operations/disp-1").json()["task_id"] == "TASK-1"
    detail = client.get("/api/tasks/TASK-1").json()
    assert detail["progress_projection"]["latest_progress"]["message"] == "working"
    assert detail["operations"]["operations"][0]["dispatch_id"] == "disp-1"


def test_kernel_mediated_fanout_request_accepts_and_emits_children(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-FAN",
            title="Needs specialists",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
            contract=TaskContract(scope=["src/a.py"]),
        ),
    )
    orch = _orchestrator(state_dir)

    decision = orch._on_task_fanout_requested(ZfEvent(  # type: ignore[attr-defined]
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-FAN",
        correlation_id="trace-1",
        payload={
            "task_id": "TASK-FAN",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "需要独立 reviewer",
            "scope": ["src/a.py"],
            "requested_specialists": ["review", "test"],
            "expected_output": "review/test notes",
            "risk": "medium",
        },
    ))

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert decision is not None
    assert decision.action == "fanout"
    assert [event.type for event in events].count("fanout.child.dispatched") == 2
    requested = next(event for event in events if event.type == "fanout.requested")
    assert requested.payload["source_event_id"]
    assert requested.payload["dispatch_id"] == "disp-1"


def test_kernel_mediated_fanout_rejects_nested_active_manifest(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-FAN",
            title="Needs specialists",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
        ),
    )
    _write_active_fanout_manifest(state_dir, "fanout-active")
    orch = _orchestrator(state_dir)

    decision = orch._on_task_fanout_requested(ZfEvent(  # type: ignore[attr-defined]
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-FAN",
        payload={
            "task_id": "TASK-FAN",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "nested fanout",
            "scope": ["src/a.py"],
            "requested_specialists": ["review"],
            "expected_output": "review notes",
        },
    ))

    events = EventLog(state_dir / "events.jsonl").read_all()
    rejected = next(event for event in events if event.type == "task.fanout.rejected")
    assert decision is not None
    assert decision.action == "block"
    assert rejected.payload["reason"] == "nested fanout denied"
    assert rejected.payload["actual_dispatch_id"] == "fanout-active"
    assert not any(event.type == "fanout.child.dispatched" for event in events)


def test_kernel_mediated_fanout_ignores_superseded_active_manifest(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-FAN",
            title="Needs specialists",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
        ),
    )
    writer = _writer(state_dir)
    writer.append(_fanout_started_event("fanout-old"))
    _write_active_fanout_manifest(state_dir, "fanout-old")
    writer.append(_fanout_started_event("fanout-current"))
    orch = _orchestrator(state_dir)

    decision = orch._on_task_fanout_requested(ZfEvent(  # type: ignore[attr-defined]
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-FAN",
        payload={
            "task_id": "TASK-FAN",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "replacement fanout",
            "scope": ["src/a.py"],
            "requested_specialists": ["review"],
            "expected_output": "review notes",
        },
    ))

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert decision is not None
    assert decision.action == "fanout"
    assert not [
        event for event in events
        if event.type == "task.fanout.rejected"
        and event.payload.get("reason") == "nested fanout denied"
    ]
    assert [event.type for event in events].count("fanout.child.dispatched") == 1


def test_kernel_mediated_fanout_rejects_stale_dispatch(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-FAN",
            title="Needs specialists",
            status="in_progress",
            active_dispatch_id="disp-current",
        ),
    )
    orch = _orchestrator(state_dir)

    decision = orch._on_task_fanout_requested(ZfEvent(  # type: ignore[attr-defined]
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-FAN",
        payload={
            "task_id": "TASK-FAN",
            "dispatch_id": "disp-stale",
            "requested_by": "dev",
            "reason": "stale",
            "scope": ["src/a.py"],
            "requested_specialists": ["review"],
            "expected_output": "notes",
            "risk": "low",
        },
    ))

    rejected = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "task.fanout.rejected"
    ]
    assert decision is not None
    assert decision.action == "block"
    assert rejected[0].payload["expected_dispatch_id"] == "disp-current"


def test_kernel_mediated_fanout_serializes_exclusive_overlap(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-FAN",
            title="Needs specialists",
            status="in_progress",
            active_dispatch_id="disp-1",
            contract=TaskContract(exclusive_files=["src/shared.py"]),
        ),
    )
    orch = _orchestrator(state_dir)

    decision = orch._on_task_fanout_requested(ZfEvent(  # type: ignore[attr-defined]
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-FAN",
        payload={
            "task_id": "TASK-FAN",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "parallel write risk",
            "scope": ["src/shared.py"],
            "requested_specialists": ["dev"],
            "expected_output": "patch",
            "risk": "high",
        },
    ))

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert decision is not None
    assert decision.action == "serialize"
    assert any(event.type == "fanout.serialize" for event in events)
    assert not any(event.type == "fanout.child.dispatched" for event in events)
