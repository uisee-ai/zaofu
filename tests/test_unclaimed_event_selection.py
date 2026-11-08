"""Periodic liveness checks retain claims without copying unrelated bodies."""
from datetime import datetime, timezone
from types import SimpleNamespace

from zf.core.events import log as log_module
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin


def test_periodic_unclaimed_scan_retains_all_claims_and_ignores_large_bodies(
    tmp_path, monkeypatch,
):
    log = EventLog(tmp_path / "events.jsonl")
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    created_ts = now.replace(hour=11).isoformat()
    claims = {
        "dispatched": "task.dispatched",
        "fanout": "fanout.child.dispatched",
        "built": "dev.build.done",
        "evidence": "task.done.evidence",
        "warned": "task.unclaimed.warning",
    }
    tasks = [Task(id=name, title=name, status="backlog") for name in ["open", *claims]]
    tasks += [Task(id="closed", title="closed", status="done"), Task(
        id="managed", title="managed", status="backlog",
        contract=TaskContract(evidence_contract={"execution_owner": "workflow"}),
    )]
    for task in tasks:
        log.append(ZfEvent(type="task.created", task_id=task.id, ts=created_ts))
    for task_id, event_type in claims.items():
        # Payload-only task IDs are part of the existing fanout contract.
        log.append(ZfEvent(type=event_type, payload={"task_id": task_id}))
    log.append(ZfEvent(type="agent.text", payload={
        "unrelated_body": ["provider transcript"] * 2000,
    }))
    copied_noise = []
    original = log_module.deepcopy

    def observe_copy(payload):
        if isinstance(payload, dict) and "unrelated_body" in payload:
            copied_noise.append(payload)
        return original(payload)

    monkeypatch.setattr(log_module, "deepcopy", observe_copy)
    emitted = []
    runtime = SimpleNamespace(
        event_log=log,
        task_store=SimpleNamespace(list_all=lambda: tasks),
        event_writer=SimpleNamespace(append=lambda event: (emitted.append(event), log.append(event))),
        _now=lambda: now.timestamp(),
    )
    # First cold read, then a warm read after the newly emitted warning.
    LifecycleManagerMixin._check_unclaimed_new_tasks(runtime)
    LifecycleManagerMixin._check_unclaimed_new_tasks(runtime)
    assert [(event.type, event.task_id) for event in emitted] == [
        ("task.unclaimed.warning", "open"),
    ]
    assert emitted[0].payload["age_s"] == 3600
    assert copied_noise == []

    # Claim appended after a warm scan still suppresses a new task warning.
    tasks.append(Task(id="late", title="late", status="backlog"))
    log.append(ZfEvent(type="task.created", task_id="late", ts=created_ts))
    log.append(ZfEvent(type="task.dispatched", task_id="late"))
    LifecycleManagerMixin._check_unclaimed_new_tasks(runtime)
    assert len(emitted) == 1
    assert copied_noise == []
