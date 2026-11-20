"""A verified work record can advance a nudge frontier after reads complete."""
from types import SimpleNamespace

from zf.core.events import EventLog, ZfEvent
from zf.runtime.attempt_read_progress import progress_nudge_state
from zf.runtime.sidecar_refs import write_sidecar_json


def case(tmp_path, monkeypatch):
    monkeypatch.setattr("zf.runtime.task_pipeline_attempt_recovery.live_task_pipeline_attempt_for_role",
        lambda *a: {"task_id": "T", "dispatch_id": "d", "attempt_id": "a", "operation_id": "o", "status": "sent"})
    monkeypatch.setattr("zf.runtime.attempt_read_progress.required_reads_for_attempt", lambda *a, **k: [])
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="worker.stuck.recovered", actor="dev", task_id="T", payload={
        "dispatch_id": "d", "attempt_id": "a", "input_read_progress": 0,
        "recovery_action": "completion_nudge_requested", "prompt_injected": True,
    }))
    body = {"schema_version": "worker-work-record.v1", "binding": {
        "task_id": "T", "attempt_id": "a", "operation_id": "o", "dispatch_id": "d", "actor": "dev"},
        "payload_digest": "unique-report", "claims_status": "worker_report_unverified", "evidence_refs": [], "next_step": "finish checks"}
    ref = write_sidecar_json(tmp_path, "artifacts/work-records/next.json", body, kind="worker_work_record",
        schema_version="worker-work-record.v1", created_by="dev", required=True)
    log.append(ZfEvent(type="worker.work_record.published", origin="kernel", actor="dev", task_id="T",
        payload={**body["binding"], "payload_digest": body["payload_digest"], "work_record_ref": ref}))
    return SimpleNamespace(state_dir=tmp_path, event_log=log), ref


def check(runtime):
    return progress_nudge_state(runtime, role_instance="dev", task_id="T", dispatch_id="d")[2]


def test_work_record_after_last_nudge_allows_continuation_once(tmp_path, monkeypatch):
    runtime, _ = case(tmp_path, monkeypatch)
    runtime.event_log.append(ZfEvent(type="provider.turn.closed", actor="dev"))
    assert check(runtime)
    runtime.event_log.append(ZfEvent(type="worker.stuck.recovered", actor="dev", task_id="T", payload={
        "dispatch_id": "d", "attempt_id": "a", "input_read_progress": 0,
        "recovery_action": "completion_nudge_requested", "prompt_injected": True,
    }))
    assert not check(runtime)


def test_late_closed_turn_never_interrupts_a_new_live_turn(tmp_path, monkeypatch):
    runtime, _ = case(tmp_path, monkeypatch)
    runtime.event_log.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="dev",
        payload={"session_id": "s", "turn_id": "new"}))
    runtime.event_log.append(ZfEvent(type="provider.turn.closed", actor="dev",
        payload={"backend": "codex", "turn_id": "old"}))
    assert not check(runtime)


def test_corrupt_record_cannot_advance_continuation(tmp_path, monkeypatch):
    runtime, ref = case(tmp_path, monkeypatch)
    (tmp_path / ref["ref"]).write_text("{}")
    runtime.event_log.append(ZfEvent(type="provider.turn.closed", actor="dev"))
    assert not check(runtime)


def test_republishing_same_report_does_not_count_as_new_progress(tmp_path, monkeypatch):
    from dataclasses import replace
    from uuid import uuid4
    runtime, _ = case(tmp_path, monkeypatch)
    record = runtime.event_log.read_all()[-1]
    runtime.event_log.append(ZfEvent(type="worker.stuck.recovered", actor="dev", task_id="T", payload={
        "dispatch_id": "d", "attempt_id": "a", "input_read_progress": 0,
        "recovery_action": "completion_nudge_requested", "prompt_injected": True,
    }))
    runtime.event_log.append(replace(record, id=str(uuid4())))
    runtime.event_log.append(ZfEvent(type="provider.turn.closed", actor="dev"))
    assert not check(runtime)


def test_user_pause_blocks_even_new_work_record_continuation(tmp_path, monkeypatch):
    runtime, _ = case(tmp_path, monkeypatch)
    runtime.event_log.append(ZfEvent(type="provider.turn.closed", actor="dev"))
    runtime.event_log.append(ZfEvent(type="dispatch.paused", actor="operator"))
    assert not check(runtime)
