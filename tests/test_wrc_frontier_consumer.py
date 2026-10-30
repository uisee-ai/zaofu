from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime import task_pipeline_runtime
from zf.runtime.wrc_frontier_consumer import consume_normal_frontier


class _Log:
    def __init__(self) -> None:
        self.events: list[ZfEvent] = []

    def read_all(self) -> list[ZfEvent]:
        return list(self.events)


class _Writer:
    def __init__(self, log: _Log) -> None:
        self.log = log

    def append(self, event: ZfEvent) -> ZfEvent:
        self.log.events.append(event)
        return event


def _runtime(*, mode: str = "blocking") -> SimpleNamespace:
    log = _Log()
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            flow_metadata={
                "task_pipeline": {
                    "mode": mode,
                    "profile_id": "prd-flow-v4-task-pipeline",
                }
            },
            orchestration=SimpleNamespace(normal_frontier_owner="wrc"),
        )
    )
    return SimpleNamespace(
        config=config,
        event_log=log,
        event_writer=_Writer(log),
    )


def test_wrc_consumer_calls_reconciler_and_fences_legacy(monkeypatch) -> None:
    runtime = _runtime()
    calls: list[object] = []
    monkeypatch.setattr(
        task_pipeline_runtime,
        "reconcile_task_pipeline_runtime",
        lambda value: calls.append(value) or ["decision"],
    )

    decisions, managed = consume_normal_frontier(
        runtime,
        candidate_task_ids=["TASK-2", "TASK-1"],
    )

    assert decisions == ["decision"]
    assert managed == {"TASK-1", "TASK-2"}
    assert calls == [runtime]
    reconciled = [
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.normal_frontier.wrc.reconciled"
    ]
    assert len(reconciled) == 1
    assert reconciled[0].payload["owner"] == "wrc"

    consume_normal_frontier(runtime, candidate_task_ids=["TASK-1", "TASK-2"])
    assert len([
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.normal_frontier.wrc.reconciled"
    ]) == 1


def test_wrc_consumer_fails_closed_when_pipeline_is_shadow() -> None:
    runtime = _runtime(mode="shadow")

    decisions, managed = consume_normal_frontier(
        runtime,
        candidate_task_ids=["TASK-1"],
    )

    assert decisions == []
    assert managed == {"TASK-1"}
    blocked = [
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.normal_frontier.owner_blocked"
    ]
    assert len(blocked) == 1
    assert blocked[0].payload["reason"] == "wrc_requires_blocking_task_pipeline"


def test_wrc_owner_dispatch_entry_does_not_fall_back_to_legacy(monkeypatch) -> None:
    runtime = _runtime()
    runtime._dispatch_globally_paused = lambda: False
    monkeypatch.setattr(
        task_pipeline_runtime,
        "reconcile_task_pipeline_runtime",
        lambda value: [],
    )

    decisions, managed = task_pipeline_runtime.prepare_task_pipeline_dispatch(
        runtime,
        candidate_task_ids=["TASK-1"],
    )

    assert decisions == []
    assert managed == {"TASK-1"}
    assert not any(
        event.type == "task.dispatched"
        for event in runtime.event_log.read_all()
    )
