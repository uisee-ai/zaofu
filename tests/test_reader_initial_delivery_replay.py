"""Simulate loss of the watcher at the first reader delivery, then run a new watcher."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_runtime import task_attempt_store
from tests.test_reader_fanout_runtime import _durable_reader_state, _RecordingTransport


class WatcherExit(BaseException):
    pass


def interrupted_reader(tmp_path, boundary, *, sibling=False):
    original_run = Orchestrator.run_once

    def interrupted_run(runtime, *args, **kwargs):
        runtime.config.workflow.task_attempt.mode = "enforce"
        if sibling:
            role = runtime.config.roles[0]
            stage = runtime.config.workflow.stages[0]
            runtime.config.roles.append(replace(role, name="other", instance_id="other"))
            stage.children.append(replace(stage.children[0], role_instance="other"))
        with pytest.raises(WatcherExit):
            original_run(runtime, *args, **kwargs)

    if boundary == "claimed":
        interrupt = patch.object(_RecordingTransport, "send_task", side_effect=WatcherExit)
    elif boundary == "running":
        interrupt = patch.object(Orchestrator, "_note_prompt_sent", side_effect=WatcherExit)
    else:
        interrupt = patch(
            "zf.runtime.call_result_runtime.mark_call_operation_started", side_effect=WatcherExit
        )
    with patch.object(Orchestrator, "run_once", interrupted_run), interrupt:
        state, log, transport, _, config, child = _durable_reader_state(tmp_path)
    return state, log, transport, config, child


@pytest.mark.parametrize("boundary", ["claimed", "sent", "running"])
def test_resumed_reader_preserves_original_delivery(tmp_path, boundary):
    state, log, transport, config, child = interrupted_reader(tmp_path, boundary)
    attempts = task_attempt_store(Orchestrator(state, config, transport))
    original = attempts.current_rows()
    assert len(original) == 1
    assert original[0]["status"] == ("delivering" if boundary == "claimed" else "sent")
    expected_sends = len(transport.sent)
    assert expected_sends == int(boundary != "claimed")
    fresh = Orchestrator(state, config, transport)
    for _ in range(2):
        fresh.run_once(events=[])
    assert len(transport.sent) == expected_sends
    assert attempts.current_rows() == original
    events = log.read_all()
    assert not [e for e in events if e.type == "fanout.child.failed"]
    dispatched = [e for e in events if e.type == "fanout.child.dispatched"]
    if boundary != "claimed":
        assert len(dispatched) == 1
        assert dispatched[0].payload["dispatch_id"] == original[0]["dispatch_id"]
        assert dispatched[0].payload["task_attempt_id"] == original[0]["attempt_id"]
        assert dispatched[0].payload["lease_id"] == original[0]["lease_id"]
        assert sum(e.type == "workflow.operation.started" for e in events) == 1
    else:
        assert not dispatched
        deferred = [
            e
            for e in events
            if e.type == "fanout.child.dispatch_deferred"
            and e.payload.get("reason") == "reader_delivery_unconfirmed"
        ]
        assert len(deferred) == 1
        assert deferred[0].payload["task_attempt_id"] == original[0]["attempt_id"]


@pytest.mark.parametrize("manager,cold", [(False, False), (True, False), (True, True)])
def test_unknown_delivery_deadline_requests_owned_diagnosis(tmp_path, manager, cold):
    from zf.runtime.task_attempt_runtime import reconcile_task_attempts

    state, log, transport, config, _ = interrupted_reader(tmp_path, "claimed")
    config.runtime.run_manager.resident_agent.enabled = manager
    fresh = Orchestrator(state, config, transport)
    if not cold:
        fresh.run_once(events=[])
    store = task_attempt_store(fresh)
    (row,) = store.current_rows()
    store.renew_lease(
        row["attempt_id"], updated_at=row["updated_at"], lease_expires_at="2000-01-01T00:00:00Z"
    )
    for _ in range(2):
        reconcile_task_attempts(fresh)
        fresh.run_once(events=[])
    rows = log.read_all()
    notices = [e for e in rows if e.type in {"human.escalate", "fanout.child.delivery_unconfirmed"}]
    assert len(notices) == 1
    notice = notices[0]
    assert notice.type == ("fanout.child.delivery_unconfirmed" if manager else "human.escalate")
    assert notice.payload["task_attempt_id"] == row["attempt_id"]
    assert notice.payload["retryable"] is False and notice.payload["delivery_status"] == "unknown"
    if manager:
        from zf.runtime.run_manager import build_run_manager_projection
        from zf.runtime.wake_patterns import WAKE_PATTERNS
        from zf.runtime.watcher import EventWatcher

        watcher = EventWatcher(state / "events.jsonl", lambda _: None, wake_patterns=list(WAKE_PATTERNS), event_log=log)
        assert watcher.is_wake_event(notice.to_json())
        deferred = next(e for e in rows if e.id == notice.causation_id)
        assert deferred.type == "fanout.child.dispatch_deferred" and not watcher.is_wake_event(deferred.to_json())
        assert notice.payload["lease_id"] == row["lease_id"] and notice.payload["recovery_owner"] == "run_manager"
        actions = build_run_manager_projection(state, events=rows, config=config, project_root=tmp_path)[
            "pending_actions"
        ]
        assert any(
            a["safe_resume_action"] == "diagnose_attention" and a.get("attempt_id") == row["attempt_id"]
            for a in actions
        )
        assert not any(a["safe_resume_action"] == "worker_lifecycle_recover" for a in actions)
    assert not transport.sent
    assert not any(e.type in {"fanout.child.failed", "task.attempt.retry_scheduled"} for e in rows)


def test_unrelated_reader_can_run_while_delivery_is_unknown(tmp_path):
    state, log, transport, config, _ = interrupted_reader(tmp_path, "claimed", sibling=True)
    fresh = Orchestrator(state, config, transport)
    fresh.run_once(events=[])
    assert [item[0] for item in transport.sent] == ["other"]
    assert [e.payload["role_instance"] for e in log.read_all() if e.type == "fanout.child.dispatched"] == [
        "other"
    ]


def test_changed_briefing_receipt_cannot_restore_a_dispatch(tmp_path):
    from pathlib import Path

    state, log, transport, config, _ = interrupted_reader(tmp_path, "sent")
    fresh = Orchestrator(state, config, transport)
    (attempt,) = task_attempt_store(fresh).current_rows()
    Path(attempt["briefing_ref"]).write_text("different card")
    for _ in range(2):
        fresh.run_once(events=[])
    assert len(transport.sent) == 1
    assert not [e for e in log.read_all() if e.type in {"fanout.child.dispatched", "fanout.child.failed"}]
    assert (
        len(
            [
                e
                for e in log.read_all()
                if e.type == "fanout.child.dispatch_deferred"
                and e.payload.get("reason") == "reader_delivery_unconfirmed"
            ]
        )
        == 1
    )


def test_proven_unsent_release_can_deliver_once(tmp_path):
    state, log, transport, config, _ = interrupted_reader(tmp_path, "claimed")
    fresh = Orchestrator(state, config, transport)
    fresh.run_once(events=[])
    store = task_attempt_store(fresh)
    (row,) = store.current_rows()
    # The transport's no-input path already uses this CAS to release the exact current claim.
    assert store.release_unsent_delivery(
        row["attempt_id"], updated_at=row["updated_at"], lease_expires_at=row["lease_expires_at"]
    )
    for _ in range(2):
        fresh.run_once(events=[])
    assert len(transport.sent) == 1
    assert len([e for e in log.read_all() if e.type == "fanout.child.dispatched"]) == 1


def test_stage_deadline_requests_receipt_diagnosis_without_new_generation(tmp_path, monkeypatch):
    state, log, transport, config, _ = interrupted_reader(tmp_path, "claimed")
    config.runtime.run_manager.resident_agent.enabled = True
    fresh = Orchestrator(state, config, transport)
    fresh.run_once(events=[])
    monkeypatch.setattr(fresh, "_fanout_timeout_seconds", lambda *a, **kw: 1)
    now = fresh._now()
    monkeypatch.setattr(fresh, "_now", lambda: now + 65)
    for _ in range(2):
        fresh._check_fanout_timeouts()
    rows = log.read_all()
    notices = [e for e in rows if e.type == "fanout.child.delivery_unconfirmed"]
    assert len(notices) == 1 and notices[0].payload["deadline"] == "stage_timeout"
    assert not any(e.type in {"fanout.timed_out", "fanout.child.failed"} for e in rows)
    assert not transport.sent
    from zf.runtime.run_manager import build_run_manager_projection
    from zf.runtime.supervisor_inspection import run_supervisor_inspection

    run_supervisor_inspection(state, config=config, project_root=tmp_path, emit_attention_events=True)
    actions = build_run_manager_projection(state, config=config, project_root=tmp_path)["pending_actions"]
    assert any(
        a["safe_resume_action"] == "diagnose_attention" and notices[0].id in a.get("source_event_ids", [])
        for a in actions
    )
