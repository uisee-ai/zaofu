"""Isolated, identity-stable scenarios for ZaoFu tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


@dataclass
class IsolatedProject:
    root: Path
    state_dir: Path
    run_id: str = "run-testkit"
    task_id: str = "TASK-TESTKIT"
    operation_id: str = "OP-TESTKIT"
    attempt: int = 1
    generation: int = 1
    environment: dict[str, str] = field(default_factory=dict)

    @property
    def events_path(self) -> Path:
        return self.state_dir / "events.jsonl"

    def event_log(self) -> EventLog:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return EventLog(self.events_path)

    def event_writer(self, *, origin: str = "kernel") -> EventWriter:
        return EventWriter(
            self.event_log(),
            correlation_id=self.run_id,
            default_origin=origin,
        )

    def emit(
        self,
        event_type: str,
        *,
        actor: str = "testkit",
        payload: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        causation_id: str | None = None,
    ) -> ZfEvent:
        event = ZfEvent(
            type=event_type,
            actor=actor,
            task_id=task_id or self.task_id,
            payload=dict(payload or {}),
            causation_id=causation_id,
            correlation_id=self.run_id,
        )
        return self.event_writer().append(event)


def isolated_project(
    root: Path,
    *,
    run_id: str = "run-testkit",
    task_id: str = "TASK-TESTKIT",
    operation_id: str = "OP-TESTKIT",
    attempt: int = 1,
    generation: int = 1,
) -> IsolatedProject:
    """Create only test-owned directories; never touches the checkout."""
    project_root = root.resolve()
    state_dir = project_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    environment = {
        "ZF_TEST_PROJECT_ROOT": str(project_root),
        "ZF_TEST_STATE_DIR": str(state_dir),
    }
    return IsolatedProject(
        root=project_root,
        state_dir=state_dir,
        run_id=run_id,
        task_id=task_id,
        operation_id=operation_id,
        attempt=attempt,
        generation=generation,
        environment=environment,
    )


def event_identity(event: ZfEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "task_id": event.task_id,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "origin": event.origin,
    }
