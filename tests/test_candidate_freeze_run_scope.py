from types import SimpleNamespace
import pytest
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts

def admit_contexts(log, rows):
    for task_id, row in rows.items():
        log.append(ZfEvent(type="task.pipeline.generation.admitted", origin="kernel",
            payload={"schema_version": "task-pipeline-generation.v1", "task_ids": [task_id],
                     "task_map_generation": row["generation_id"], **row}))
    return task_pipeline_generation_contexts(log.read_all())

from zf.runtime.task_pipeline_terminal import reconcile_task_pipeline_freeze


@pytest.mark.parametrize("older_status", ["backlog", "in_progress", "cancelled"])
def test_incremental_generation_does_not_freeze_while_inherited_task_unfinished(
    tmp_path, monkeypatch, older_status
):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="OLD", status=older_status))
    store.add(Task(id="GAP", status="done"))
    runtime = SimpleNamespace(
        task_store=store,
        state_dir=tmp_path,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )
    frozen = []
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_terminal._freeze_generation",
        lambda runtime, **kw: frozen.append(kw["generation_id"]) or True,
    )
    contexts = {
        "OLD": {"generation_id": "g1", "workflow_run_id": "run-1"},
        "GAP": {"generation_id": "g2", "workflow_run_id": "run-1"},
    }
    assert reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts) == []
    assert frozen == []


def test_unfinished_other_run_does_not_block_freeze(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="OTHER", status="backlog"))
    store.add(Task(id="GAP", status="done"))
    runtime = SimpleNamespace(
        task_store=store,
        state_dir=tmp_path,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )
    frozen = []
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_terminal._freeze_generation",
        lambda runtime, **kw: frozen.append(kw["generation_id"]) or True,
    )
    contexts = {
        "OTHER": {"generation_id": "g1", "workflow_run_id": "other"},
        "GAP": {"generation_id": "g2", "workflow_run_id": "run-1"},
    }
    contexts = admit_contexts(runtime.event_log, contexts)
    reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts)
    assert frozen == ["g2"]


def test_historical_incomplete_freeze_cancels_only_its_reader_fanout(tmp_path):
    from zf.core.events.model import ZfEvent
    from zf.runtime.workflow_operation import reduce_workflow_operations
    from tests.test_reader_fanout_runtime import _state

    state, log, transport, orch = _state(tmp_path)
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        origin="kernel",
        correlation_id="trace-1",
        payload={
            "schema_version": "candidate-freeze-receipt.v1",
            "workflow_run_id": "trace-1",
            "pdd_id": "F-11111111",
            "task_ids": ["GAP"],
        },
    )
    log.append(trigger)
    orch.run_once(events=[trigger])
    from zf.runtime.call_result_runtime import workflow_operation_service

    service = workflow_operation_service(orch)
    stopped = []
    transport.terminate = lambda role: stopped.append(role)
    transport.is_alive = lambda role: role not in stopped
    for dispatched in [
        e for e in log.read_all() if e.type == "fanout.child.dispatched"
    ]:
        role = dispatched.payload["role_instance"]
        result = service.ensure_operation(
            workflow_run_id="trace-1",
            operation_id="op-" + role,
            operation_type="fanout_reader_child",
            request={},
            role_instance=role,
        )
        orch.event_writer.append(
            ZfEvent(
                type="fanout.child.dispatched",
                payload={
                    **dispatched.payload,
                    "operation_id": result.operation_id,
                    "request_hash": result.request_hash,
                },
            )
        )
    orch.task_store.add(Task(id="OLD", status="backlog"))
    contexts = {"OLD": {"generation_id": "g1", "workflow_run_id": "trace-1"}}
    assert reconcile_task_pipeline_freeze(orch, generation_contexts=contexts) == []
    events = log.read_all()
    cancelled = [e for e in events if e.type == "fanout.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == "candidate_freeze_incomplete_run_scope"
    ops = reduce_workflow_operations(events)
    assert ops and all(op["status"] == "cancelled" for op in ops.values())
    assert sorted(stopped) == ["review-a", "review-b"]
    reconcile_task_pipeline_freeze(orch, generation_contexts=contexts)
    assert len([e for e in log.read_all() if e.type == "fanout.cancelled"]) == 1


@pytest.mark.parametrize(
    "schema,run", [("candidate-freeze-receipt.v1", "other"), ("legacy", "trace-1")]
)
def test_scope_recovery_preserves_other_run_and_legacy_candidates(
    tmp_path, schema, run
):
    from zf.core.events.model import ZfEvent
    from tests.test_reader_fanout_runtime import _state

    state, log, transport, orch = _state(tmp_path)
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        origin="kernel",
        correlation_id=run,
        payload={
            "schema_version": schema,
            "workflow_run_id": run,
            "pdd_id": "F-11111111",
        },
    )
    log.append(trigger)
    orch.run_once(events=[trigger])
    orch.task_store.add(Task(id="OLD", status="backlog"))
    reconcile_task_pipeline_freeze(
        orch,
        generation_contexts={
            "OLD": {"generation_id": "g1", "workflow_run_id": "trace-1"}
        },
    )
    assert not any(e.type == "fanout.cancelled" for e in log.read_all())


def test_authorized_supersession_does_not_hold_successor_freeze(tmp_path, monkeypatch):
    from zf.core.events.model import ZfEvent

    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="OLD", status="cancelled"))
    store.add(Task(id="GAP", status="done"))
    log = EventLog(tmp_path / "events.jsonl")
    admit_contexts(log, {"OLD": {"generation_id": "g1", "workflow_run_id": "run-1"}})
    log.append(
        ZfEvent(
            type="task.superseded",
            task_id="OLD",
            origin="kernel",
            correlation_id="run-1",
            payload={
                "source": "writer_task_map_adoption",
                "status": "cancelled",
                "superseded_by_task_map_ref": "artifacts/amended.json",
            },
        )
    )
    log.append(
        ZfEvent(
            type="task.pipeline.generation.admitted",
            payload={
                "workflow_run_id": "run-1",
                "task_map_ref": "artifacts/amended.json",
            },
        )
    )
    runtime = SimpleNamespace(task_store=store, state_dir=tmp_path, event_log=log)
    frozen = []
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_terminal._freeze_generation",
        lambda runtime, **kw: frozen.append(kw["generation_id"]) or True,
    )
    reconcile_task_pipeline_freeze(
        runtime,
        generation_contexts=admit_contexts(log, {
            "GAP": {"generation_id": "g2", "workflow_run_id": "run-1", "task_map_ref": "artifacts/amended.json"},
        }),
    )
    assert frozen == ["g2"]
