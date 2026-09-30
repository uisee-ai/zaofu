"""S5 conformance cases for provider/transport boundary facts."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.testkit.providers import ProviderReply, ScriptedMockProvider
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.transport import DispatchContext
from zf.runtime.transport_stream_json import StreamJsonTransport

from tests.test_fanout_hardening import _RecordingTransport, _state


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (ProviderReply(status="completed", output="ok"), "completed"),
        (ProviderReply(status="failed", output="error", exit_code=1), "failed"),
        (ProviderReply(status="timed_out", timed_out=True), "timed_out"),
        (ProviderReply(status="malformed", output="not-json", exit_code=2), "malformed"),
        (ProviderReply(status="completed", output="duplicate-output"), "completed"),
    ],
    ids=["success", "failure", "timeout", "malformed", "duplicate-output"],
)
def test_scripted_provider_returns_declared_fact_without_kernel_mutation(
    tmp_path,
    reply: ProviderReply,
    expected: str,
) -> None:
    provider = ScriptedMockProvider([reply])
    briefing = tmp_path / "briefing.md"
    briefing.write_text("Active task: TASK-S5\n", encoding="utf-8")

    receipt = provider.invoke(briefing)

    assert receipt.status == expected
    assert receipt.briefing == str(briefing)
    assert receipt.call_index == 0
    assert provider.receipts == [receipt]


def test_scripted_provider_repetition_is_observable_and_bounded(tmp_path) -> None:
    provider = ScriptedMockProvider(
        [
            ProviderReply(status="completed", output="same"),
            ProviderReply(status="completed", output="same"),
        ],
    )
    briefing = tmp_path / "briefing.md"
    briefing.write_text("same task\n", encoding="utf-8")

    first = provider.invoke(briefing)
    second = provider.invoke(briefing)

    assert [item.call_index for item in provider.receipts] == [0, 1]
    assert first.output == second.output == "same"
    with pytest.raises(RuntimeError, match="provider exhausted"):
        provider.invoke(briefing)


def test_transport_provider_event_is_ledgered_before_completion_audit(
    tmp_path: Path,
) -> None:
    """A provider fact enters EventLog; it cannot directly close a Task."""
    state_dir, log, _transport, orchestrator = _state(tmp_path)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-S5-PROVIDER",
        title="provider fact",
        status="in_progress",
        assigned_to="review-a",
    ))
    provider_event = ZfEvent(
        id="EV-S5-PROVIDER-COMPLETED",
        type="worker.completed",
        actor="provider",
        task_id="TASK-S5-PROVIDER",
        payload={"summary": "provider says done"},
    )

    class ProviderEventTransport(_RecordingTransport):
        def __init__(self) -> None:
            super().__init__()
            self.pending = [provider_event]

        def poll_events(self):  # noqa: ANN001
            pending, self.pending = self.pending, []
            return pending

    provider_transport = ProviderEventTransport()
    orchestrator.transport = provider_transport
    orchestrator._drain_transport_events()
    orchestrator.run_once()

    events = log.read_all()
    assert any(event.id == provider_event.id for event in events)
    assert TaskStore(state_dir / "kanban.json").get(
        "TASK-S5-PROVIDER"
    ).status == "in_progress"
    assert not [
        event for event in events
        if event.type in {"task.done", "run.goal.completed"}
        and event.task_id == "TASK-S5-PROVIDER"
    ]


def test_malformed_stream_output_becomes_blocked_fact_not_completion(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )

    async def malformed_query(*, prompt, options=None, transport=None):
        raise RuntimeError("MessageParseError: malformed JSON provider frame")
        yield

    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=malformed_query,
        background_dispatch=True,
    )
    transport.spawn(RoleConfig(name="malformed", backend="claude-code"), argv=[])
    transport.send_task(
        "malformed",
        state_dir / "briefings" / "malformed.md",
        "run",
        context=DispatchContext(
            trace_id="trace-malformed",
            task_id="TASK-S5-MALFORMED",
            instance_id="malformed",
        ),
    )

    events: list[ZfEvent] = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not events:
        events = transport.poll_events()
        time.sleep(0.01)
    try:
        blocked = next(event for event in events if event.type == "agent.api_blocked")
        assert blocked.task_id == "TASK-S5-MALFORMED"
        assert "malformed JSON" in blocked.payload["transport_error"]
        assert not any(event.type in {"task.done", "run.goal.completed"} for event in events)
    finally:
        transport.shutdown()
