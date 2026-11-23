from datetime import datetime
from threading import Event
from time import monotonic, sleep

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_schedule import reconcile_schedules
from tests.test_workflow_schedule import setup


def job(tmp_path):
    ref = write_immutable_json_sidecar(
        tmp_path,
        {"finding": "original"},
        root="job-inputs",
        kind="report",
        schema_version="demo.v1",
        created_by="owner",
    )
    return setup(
        tmp_path,
        action="agent_report",
        provider="ollama",
        model="local-test",
        port=11434,
        max_output_tokens=80,
        timeout_seconds=30,
        input_refs=[ref],
    )


def test_one_fresh_conversation_per_occurrence_and_no_retry_after_uncertain_send(
    tmp_path, monkeypatch
):
    from zf.runtime import scheduled_agent_job as jobs

    runtime, definition = job(tmp_path)
    entered, release = Event(), Event()
    calls = []

    def chat(profile, packet, instruction):
        calls.append(packet)
        entered.set()
        assert release.wait(5)
        assert packet["inputs"][0]["body"] == {"finding": "original"}
        return {"advice": "ok", "input_tokens": 12, "output_tokens": 3}

    monkeypatch.setattr(jobs, "local_chat", chat)
    now = datetime.fromisoformat("2026-10-01T03:00:00Z")
    reconcile_schedules(runtime, now=now)
    assert entered.wait(4)
    assert reconcile_schedules(runtime, now=now) == []
    release.set()
    until = monotonic() + 5
    while monotonic() < until:
        finished = reconcile_schedules(runtime, now=now)
        if finished:
            break
        sleep(0.01)
    result = finished[-1].payload
    assert result["status"] == "completed"
    body = hydrate_sidecar_ref(tmp_path, result["report_ref"]).payload
    assert body["reply"] == "ok" and not body["authorizes_execution"]
    assert len(calls) == 1 and not reconcile_schedules(runtime, now=now)
    # A controller died after persisting the send intent, before publishing a response.
    monkeypatch.setattr(
        jobs.Thread, "start", lambda self: (_ for _ in ()).throw(InterruptedError())
    )
    following = now.replace(hour=4)
    definition["enabled"] = True
    reconcile_schedules(runtime, now=following)
    result = reconcile_schedules(runtime, now=following)
    # Runtime may already have journaled the unknown on the original tick.
    last = [
        e
        for e in runtime.event_log.read_all()
        if e.type == "workflow.schedule.finished"
    ][-1]
    assert last.payload["status"] == "needs_owner"
    assert len(calls) == 1


def test_started_without_response_is_owned_and_never_resent(tmp_path, monkeypatch):
    from zf.runtime import workflow_schedule as scheduler
    from zf.runtime import scheduled_agent_job as jobs

    runtime, _ = job(tmp_path)
    now = datetime.fromisoformat("2026-10-01T03:00:00Z")

    def crash(runtime, d, claim, events):
        jobs._record(
            runtime,
            jobs.STARTED,
            claim,
            {"session_id": "original", "started_at": now.isoformat()},
        )
        raise SystemExit("simulated controller exit")

    import pytest

    with monkeypatch.context() as m:
        m.setattr(scheduler, "_execute", crash)
        with pytest.raises(SystemExit):
            reconcile_schedules(runtime, now=now)

    def forbidden(*args):
        raise AssertionError("unknown send must never be retried")

    monkeypatch.setattr(jobs, "local_chat", forbidden)
    (finished,) = reconcile_schedules(runtime, now=now)
    assert finished.payload["reason"] == "provider_outcome_unknown"
    assert finished.payload["session_id"] == "original"
    assert not reconcile_schedules(runtime, now=now)


def test_different_definitions_do_not_gain_input_access(tmp_path, monkeypatch):
    from zf.runtime import scheduled_agent_job as jobs

    runtime, d = job(tmp_path)
    d["input_refs"][0]["access_scope"] = {"actors": ["private-lane"]}
    monkeypatch.setattr(
        jobs, "local_chat", lambda *a: (_ for _ in ()).throw(AssertionError())
    )
    rows = reconcile_schedules(
        runtime, now=datetime.fromisoformat("2026-10-01T03:00:00Z")
    )
    assert rows[-1].payload["status"] == "stale"
    assert not any(e.type == jobs.STARTED for e in runtime.event_log.read_all())
