from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run


def _event(event_type: str, run_id: str, event_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        type=event_type,
        id=event_id,
        correlation_id="",
        payload={"workflow_run_id": run_id},
    )


def test_invoke_without_admission_or_terminal_is_explicitly_incomplete() -> None:
    invoke = _event("workflow.invoke.requested", "run-1", "invoke-1")
    summary = summarize_flow_run([invoke], invoke)
    assert summary == {
        "run_id": "run-1",
        "admission_status": "missing",
        "admission_event_id": "",
        "terminal_status": "missing",
        "terminal_event_id": "",
        "terminal_event_observed": False,
    }


def test_queued_run_is_not_reported_as_terminal_closure() -> None:
    invoke = _event("workflow.invoke.requested", "run-2", "invoke-2")
    events = [invoke, _event("run.admission.queued", "run-2", "queued-2")]
    summary = summarize_flow_run(events, invoke)
    assert summary["admission_status"] == "queued"
    assert summary["terminal_status"] == "missing"
    assert summary["terminal_event_observed"] is False


def test_admitted_completed_run_records_event_not_delivery_proof() -> None:
    invoke = _event("workflow.invoke.requested", "run-3", "invoke-3")
    events = [
        invoke,
        _event("run.admission.admitted", "run-3", "admit-3"),
        _event("run.goal.completed", "run-3", "terminal-3"),
    ]
    summary = summarize_flow_run(events, invoke)
    assert summary["admission_status"] == "admitted"
    assert summary["terminal_status"] == "completed"
    assert summary["terminal_event_observed"] is True
    assert "terminal_closure" not in summary


def test_admitted_blocked_run_keeps_terminal_reason_class() -> None:
    invoke = _event("workflow.invoke.requested", "run-4", "invoke-4")
    events = [
        invoke,
        _event("run.admission.admitted", "run-4", "admit-4"),
        _event("run.goal.blocked", "run-4", "terminal-4"),
    ]
    summary = summarize_flow_run(events, invoke)
    assert summary["terminal_status"] == "blocked"
    assert summary["terminal_event_observed"] is True


def test_later_admission_replaces_queued_observation() -> None:
    invoke = _event("workflow.invoke.requested", "run-1", "invoke")
    events = [invoke, _event("run.admission.queued", "run-1", "queue"),
              _event("run.admission.admitted", "run-1", "admit")]
    assert summarize_flow_run(events, invoke)["admission_event_id"] == "admit"


@pytest.mark.parametrize("damage", ["different-run", "alias-conflict", "source-conflict", "before-invoke"])
def test_unrelated_or_stale_admission_does_not_count(damage) -> None:
    invoke = _event("workflow.invoke.requested", "run-1", "invoke")
    admission = _event("run.admission.admitted", "run-1", "admit")
    events = [invoke, admission]
    if damage == "different-run":
        admission.payload["workflow_run_id"] = "other"
    elif damage == "alias-conflict":
        admission.payload["run_id"] = "other"
        admission.correlation_id = "run-1"
    elif damage == "source-conflict":
        admission.payload["source_event_id"] = "old-invoke"
    else:
        events.reverse()
    assert summarize_flow_run(events, invoke)["admission_status"] == "missing"


def test_simulation_cleanup_and_other_run_terminal_are_not_completion() -> None:
    invoke = _event("workflow.invoke.requested", "run-1", "invoke")
    events = [invoke, _event("run.goal.completed", "other", "terminal"),
              _event("simulation.done", "run-1", "cleanup")]
    assert summarize_flow_run(events, invoke)["terminal_status"] == "missing"
