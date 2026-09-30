"""STO-03: late ordinary worker facts cannot reopen an archived terminal Task."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from tests.conformance.test_zaofu_testkit_s3 import orchestration_context as orchestration_context
from tests.test_scheduler_task_attempt import _RecordingTransport
from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import ModelEvidence
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


@pytest.mark.parametrize("terminal", ["done", "cancelled"])
@pytest.mark.parametrize("event_type", ["dev.build.done", "review.rejected"])
def test_late_worker_event_cannot_reopen_terminal_store(
    tmp_path: Path, request, orchestration_context, terminal: str, event_type: str,
) -> None:
    project, store, log, runtime = orchestration_context
    state = project.state_dir
    attach_runtime_case_evidence(
        request, state,
        given=f"terminal task status is {terminal} before late {event_type}",
        when="append one late worker event and reconcile twice",
        then="canonical terminal task and attempt state remain unchanged",
        forbidden=["terminal task reopen", "dispatch", "goal completion"],
        task_id=project.task_id,
        inputs={"terminal": terminal, "event_type": event_type},
    )
    transport = _RecordingTransport(state)
    runtime.transport = transport
    store.add(Task(id=project.task_id, title="terminal seed", status="in_progress", assigned_to="dev"))
    store.update(project.task_id, status=terminal, active_dispatch_id="old-dispatch")
    before = asdict(store.get(project.task_id))
    canonical = {p: p.read_bytes() for p in [store.path, *(state / "kanban").glob("*.json")]}
    attempts = TaskAttemptStore(state / "task_attempts.json").load()
    model = ModelEvidence(
        root=tmp_path, model_name="FixedTerminalStoreOwner", source=Path(__file__),
        settings="done/cancelled x completion/rejection; real run_once twice; mock transport",
        reference=lambda: {"task": before, "attempts": attempts, "dispatches": 0},
    )
    with model.guard("late-terminal-fact", {"terminal": terminal, "event_type": event_type}):
        late = log.append(ZfEvent(
            id="late-event", type=event_type, task_id=project.task_id,
            actor="dev" if event_type == "dev.build.done" else "review",
            payload={"dispatch_id": "old-dispatch", "result": "late output", "reason": "late rejection"},
        ))
        # EventLog.append returns None; the occurrence is read from the real ledger.
        assert late is None
        event = next(row for row in log.read_all() if row.id == "late-event")
        decisions = [*runtime.run_once(events=[event]), *runtime.run_once(events=[event])]
        actual = asdict(TaskStore(store.path).get(project.task_id))
        write_json_atomic(tmp_path / "observed.json", {
            "task": actual, "decisions": [asdict(row) for row in decisions],
            "transport_calls": len(transport.contexts),
        })
        assert actual == before
        assert all(p.read_bytes() == body for p, body in canonical.items())
        assert TaskAttemptStore(state / "task_attempts.json").load() == attempts
        assert not transport.contexts
        assert not any(row.action == "dispatch" for row in decisions)
        forbidden = {"task.dispatched", "task.attempt.started", "task.rework.requested", "run.goal.completed"}
        assert not any(row.type in forbidden for row in log.read_all())
