from zf.core.events.model import ZfEvent
from zf.runtime.run_manager_progress import task_evidence_signals


def event(kind, attempt="new", **payload):
    return ZfEvent(type=kind, task_id="T", origin="kernel", payload={
        "workflow_run_id": "run", "attempt_id": attempt,
        "dispatch_id": "d-" + attempt, **payload,
    })


def test_late_attempt_cannot_refresh_current_task_activity_or_result():
    dispatch = event("task.pipeline.stage.dispatched")
    current = event("worker.heartbeat")
    rows = task_evidence_signals([
        dispatch, current, event("agent.usage", "old"), event("dev.build.done", "old"),
    ], ["T"])["T"]
    assert rows["activity"]["event_id"] == current.id
    assert rows["result_signal"] is None


def test_new_dispatch_does_not_inherit_previous_progress():
    rows = task_evidence_signals([
        event("task.dispatched", "old"), event("dev.build.done", "old"),
        event("task.dispatched"),
    ], ["T"])["T"]
    assert rows["result_signal"] is None


def test_input_work_verification_and_admission_are_separate_signals():
    kinds = {
        "input": "artifact.input.observed",
        "work_record": "worker.work_record.published",
        "verification": "verify.passed",
        "accepted_result": "workflow.call.result.admitted",
    }
    events = [event("task.pipeline.stage.dispatched"), *map(event, kinds.values())]
    rows = task_evidence_signals(events, ["T"])["T"]
    for key, kind in kinds.items():
        assert rows[key]["type"] == kind
    assert rows["activity"] is None


def test_identified_dispatch_does_not_accept_unattributed_heartbeat():
    rows = task_evidence_signals([
        event("task.pipeline.stage.dispatched"), ZfEvent(type="worker.heartbeat", task_id="T"),
    ], ["T"])["T"]
    assert rows["activity"] is None


def test_input_observation_without_dispatch_id_uses_matching_attempt():
    observed = ZfEvent(type="artifact.input.observed", task_id="T", payload={
        "workflow_run_id": "run", "attempt_id": "new", "kind": "recovery",
    })
    rows = task_evidence_signals([event("task.dispatched"), observed], ["T"])["T"]
    assert rows["input"]["event_id"] == observed.id
    assert rows["input"]["observation_kind"] == "recovery"


def test_matching_attempt_cannot_override_conflicting_run():
    observed = event("artifact.input.observed", workflow_run_id="other")
    rows = task_evidence_signals([event("task.dispatched"), observed], ["T"])["T"]
    assert rows["input"] is None


def test_worker_admission_claim_is_not_admitted_result():
    from dataclasses import replace
    rows = task_evidence_signals([
        event("task.dispatched"), replace(event("workflow.call.result.admitted"), origin="worker"),
    ], ["T"])["T"]
    assert rows["accepted_result"] is None
