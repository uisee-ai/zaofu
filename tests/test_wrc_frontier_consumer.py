from types import SimpleNamespace

import pytest

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
        _dispatch_globally_paused=lambda: False,
    )


def test_wrc_respects_pause_then_resumes_without_losing_fence(monkeypatch) -> None:
    runtime = _runtime()
    runtime._dispatch_globally_paused = lambda: True
    calls = []
    monkeypatch.setattr(task_pipeline_runtime, "reconcile_task_pipeline_runtime",
                        lambda value: calls.append(value) or ["dispatch"])
    assert task_pipeline_runtime.prepare_task_pipeline_dispatch(
        runtime, candidate_task_ids=["TASK-1"]
    ) == ([], {"TASK-1"})
    assert calls == []
    runtime._dispatch_globally_paused = lambda: False
    assert task_pipeline_runtime.prepare_task_pipeline_dispatch(
        runtime, candidate_task_ids=["TASK-1"]
    ) == (["dispatch"], {"TASK-1"})


def test_wrc_continuation_does_not_dispatch_while_paused(monkeypatch):
    from zf.runtime.workflow_frontier_consumer import consume_wrc_normal_frontier
    runtime = _runtime()
    runtime._dispatch_globally_paused = lambda: True
    monkeypatch.setattr("zf.runtime.workflow_frontier_consumer.pending_read_only_continuation_actions",
                        lambda *a, **kw: pytest.fail("paused frontier was queried"))
    assert consume_wrc_normal_frontier(runtime) == []


@pytest.mark.parametrize("admitted,configured", [("run_manager", "wrc"), ("wrc", "run_manager")])
def test_active_generation_owner_cannot_change_on_restart(monkeypatch, admitted, configured):
    runtime = _runtime()
    runtime.config.workflow.orchestration.normal_frontier_owner = configured
    runtime.event_log.events.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-1",
        "task_map_generation": "gen-1", "task_ids": ["TASK-1"], "frontier_owner": admitted,
    }))
    calls = []
    monkeypatch.setattr(task_pipeline_runtime, "reconcile_task_pipeline_runtime",
                        lambda runtime: calls.append(True) or [])
    decisions, managed = task_pipeline_runtime.prepare_task_pipeline_dispatch(
        runtime, candidate_task_ids=[])
    assert decisions == []
    assert managed == {"TASK-1"}
    assert calls == []
    assert any("generation_frontier_owner_changed" in str(e.payload) for e in runtime.event_log.events)


def test_legacy_owner_requires_matching_dispatch_evidence():
    from zf.runtime.workflow_frontier_owner import generation_owner_error
    runtime = _runtime()
    runtime.event_log.events.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-1",
        "task_map_generation": "gen-1", "task_ids": ["TASK-1"],
    }))
    assert generation_owner_error(runtime.config, runtime.event_log.read_all()) == "generation_frontier_owner_unknown"
    runtime.event_log.events.append(ZfEvent(type="task.pipeline.stage.dispatched", payload={
        "workflow_run_id": "run-1", "task_map_generation": "gen-1", "frontier_owner": "wrc",
    }))
    assert generation_owner_error(runtime.config, runtime.event_log.read_all()) == ""


def test_pre_owner_legacy_dispatch_is_not_reassigned_to_wrc():
    from zf.runtime.workflow_frontier_owner import generation_owner_error
    runtime = _runtime()
    runtime.event_log.events.extend([
        ZfEvent(type="task.pipeline.generation.admitted", payload={
            "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-1",
            "task_map_generation": "gen-1", "task_ids": ["TASK-1"],
        }),
        ZfEvent(type="task.pipeline.stage.dispatched", payload={
            "workflow_run_id": "run-1", "task_map_generation": "gen-1",
        }),
    ])
    assert generation_owner_error(runtime.config, runtime.event_log.read_all()) == "generation_frontier_owner_changed"
    runtime.config.workflow.orchestration.normal_frontier_owner = "run_manager"
    assert generation_owner_error(runtime.config, runtime.event_log.read_all()) == ""


def test_conflicting_same_generation_dispatch_owners_fail_closed():
    from zf.runtime.workflow_frontier_owner import generation_owner_error
    runtime = _runtime()
    runtime.event_log.events.extend([
        ZfEvent(type="task.pipeline.generation.admitted", payload={
            "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-1",
            "task_map_generation": "gen-1", "task_ids": ["TASK-1"], "frontier_owner": "wrc",
        }),
        ZfEvent(type="task.pipeline.stage.dispatched", payload={
            "workflow_run_id": "run-1", "task_map_generation": "gen-1", "frontier_owner": "run_manager",
        }),
    ])
    assert generation_owner_error(runtime.config, runtime.event_log.read_all()) == "generation_frontier_owner_conflict"


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
