from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_schedule import reconcile_schedules


def setup(tmp_path, **extra):
    d = {
        "id": "inspect",
        "revision": "1",
        "owner": "RM",
        "goal": "inspect frozen run",
        "workflow_run_id": "r1",
        "action": "inspect",
        "enabled": True,
        "effective_at": "2026-10-01T00:00:00Z",
        "cron": "0 * * * *",
        "timezone": "UTC",
        "max_occurrences": 5,
        **extra,
    }
    log = EventLog(tmp_path / "events.jsonl")
    return SimpleNamespace(
        config=SimpleNamespace(
            workflow=SimpleNamespace(flow_metadata={"orchestration_schedules": [d]})
        ),
        state_dir=tmp_path,
        event_writer=EventWriter(log),
        event_log=log,
    ), d


def test_restart_concurrent_sweeps_and_manual_identity(tmp_path):
    runtime, d = setup(tmp_path)
    now = datetime.fromisoformat("2026-10-04T22:30:00Z")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: reconcile_schedules(runtime, now=now), range(3)))
    claims, finish = runtime.event_log.read_all()
    assert claims.payload["scheduled_at"] == "2026-10-04T22:00:00+00:00"
    assert finish.payload["claim_id"] == claims.id
    report = hydrate_sidecar_ref(tmp_path, finish.payload["report_ref"]).payload
    assert report["workflow_run_id"] == "r1" and report["authorizes_execution"] is False
    assert (
        len(
            reconcile_schedules(
                runtime, now=now, manual_id="m", definition_id="inspect"
            )
        )
        == 2
    )
    assert (
        reconcile_schedules(runtime, now=now, manual_id="m", definition_id="inspect")
        == []
    )
    d["enabled"] = False
    assert reconcile_schedules(runtime, now=now) == []
    d["goal"] = "changed without authorization revision"
    with pytest.raises(ValueError, match="revision"):
        reconcile_schedules(runtime, now=now)


def test_wait_arbitration_uses_bound_source_and_no_wake_for_stale_attempt(tmp_path):
    runtime, _ = setup(
        tmp_path,
        action="notify",
        deadline="2026-10-01T03:00:00Z",
        operation_id="op",
        request_hash="req",
        attempt_id="a1",
        actor="reader",
        event_type="gate.passed",
        event_actor="kernel-gate",
    )
    for name, run_id, actor, at in (
        ("wrong-run", "r2", "kernel-gate", "01:00"),
        ("wrong-actor", "r1", "outsider", "01:30"),
        ("won", "r1", "kernel-gate", "02:00"),
        ("late", "r1", "kernel-gate", "04:00"),
    ):
        runtime.event_log.append(
            ZfEvent(
                id=name,
                type="gate.passed",
                origin="kernel",
                actor=actor,
                ts=f"2026-10-01T{at}:00Z",
                payload={"workflow_run_id": run_id, "operation_id": "op"},
            )
        )
    now = datetime.fromisoformat("2026-10-01T05:00:00Z")
    claimed, finished = reconcile_schedules(runtime, now=now)
    assert claimed.payload["trigger_ref"] == "won"
    assert finished.payload["status"] == "stale"
    assert not any(
        e.type == "worker.reply.requested" for e in runtime.event_log.read_all()
    )
    assert not reconcile_schedules(runtime, now=now)


@pytest.mark.parametrize(
    "change,reason",
    [("revision", "definition_superseded"), ("delete", "definition_removed")],
)
def test_crash_after_claim_then_owner_updates_or_removes_job(
    tmp_path, monkeypatch, change, reason
):
    from zf.runtime import workflow_schedule as jobs

    runtime, d = setup(tmp_path)
    now = datetime.fromisoformat("2026-10-04T22:30:00Z")
    execute = jobs._execute

    def crash(*args):
        raise InterruptedError("unlaunched")

    d["max_attempts"] = 2
    monkeypatch.setattr(jobs, "_execute", crash)
    reconcile_schedules(runtime, now=now)
    claim = runtime.event_log.read_all()[0]
    if change == "revision":
        d["revision"] = "2"
    else:
        runtime.config.workflow.flow_metadata["orchestration_schedules"] = []
    monkeypatch.setattr(jobs, "_execute", execute)
    reconcile_schedules(runtime, now=now)
    reconcile_schedules(runtime, now=now)
    retired = [
        e.payload
        for e in runtime.event_log.read_all()
        if e.type == jobs.PREFIX + "finished" and e.payload["claim_id"] == claim.id
    ]
    assert len(retired) == 1 and retired[0]["reason"] == reason


def test_live_wait_sends_one_continuation_and_rechecks_owner_before_send(
    tmp_path, monkeypatch
):
    from tests.test_worker_reply_binding import _runtime
    from zf.runtime.task_attempt_continuation import validate_task_continuation

    runtime, _, identity, sends = _runtime(tmp_path, monkeypatch)
    role = "dev-lane-2"
    job = {
        "id": "wait",
        "revision": "1",
        "enabled": True,
        "action": "notify",
        "owner": "RM",
        "goal": "reread decision",
        "workflow_run_id": "run-1",
        "operation_id": "op-1",
        "request_hash": identity["request_hash"],
        "actor": role,
        "attempt_id": "attempt-1",
        "effective_at": "2026-10-01T12:00:00Z",
        "deadline": "2026-10-01T18:00:00Z",
    }
    runtime.config.workflow.flow_metadata["orchestration_schedules"] = [job]
    now = datetime.fromisoformat(job["deadline"])
    assert len(reconcile_schedules(runtime, now=now)) == 2
    assert reconcile_schedules(runtime, now=now) == []
    wake = next(
        e for e in runtime.event_log.read_all() if e.type == "worker.reply.requested"
    )
    monkeypatch.setattr(runtime.transport, "is_alive", lambda _: True)
    assert runtime._apply_worker_reply_request(
        wake, instance_id=role, payload=wake.payload
    )
    ctx = sends[0][-1]
    assert ctx.continuation and ctx.attempt_id == "attempt-1"
    validate_task_continuation(runtime, role, ctx)
    assert len(sends) == 1
    job["enabled"] = False
    with pytest.raises(ValueError, match="disabled or superseded"):
        validate_task_continuation(runtime, role, ctx)
