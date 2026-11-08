"""A superseded package cannot keep an operator approval wait alive."""
from copy import deepcopy

import pytest

from zf.cli.plan_approval import _pending
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.plan_approval_replay import pending_plan_requests
from zf.runtime.plan_approval_state import decide_pending_plan, has_pending_plan_approval
from tests.test_goal_idle_driver import _cfg, _goal_started, _state, _tick_until_fire, _Writer


def _events():
    old = ZfEvent(type="plan.approval.requested", payload={
        "plan_id": "old-plan", "workflow_run_id": "r-1",
        "plan_artifact_package_ref": "artifacts/old.json",
        "plan_artifact_package_digest": "a" * 64,
    })
    admitted = ZfEvent(type="plan.artifact_package.admitted", origin="kernel", causation_id="ready-new",
                      payload={"workflow_run_id": "r-1", "status": "admitted", "package_slot": "execution_plan",
                               "package_ref": "artifacts/new.json", "package_digest": "b" * 64})
    superseded = ZfEvent(type="plan.artifact_package.superseded", origin="kernel", causation_id="ready-new",
                        payload={"workflow_run_id": "r-1", "status": "superseded", "package_slot": "execution_plan",
                                 "package_ref": "artifacts/old.json", "package_digest": "a" * 64,
                                 "superseded_by_package_ref": "artifacts/new.json",
                                 "superseded_by_package_digest": "b" * 64})
    return [old, admitted, superseded]


@pytest.mark.parametrize("late", [False, True])
def test_superseded_request_no_longer_blocks_cli_or_idle(late):
    events = _events()
    if late:
        events = events[1:] + events[:1]
    assert pending_plan_requests(iter(events)) == []
    assert not has_pending_plan_approval(events, workflow_run_id="r-1")
    assert _pending(events, "r-1") == []
    writer = _Writer()
    assert _tick_until_fire([_goal_started(), *events], _cfg(), _state(), writer) == "rescan"


@pytest.mark.parametrize("mutation", ["run", "digest", "ref", "slot", "origin", "admission_origin", "replacement_digest", "missing_admission", "cause", "missing_identity"])
def test_incomplete_or_foreign_replacement_does_not_retire(mutation):
    events = _events()
    if mutation == "missing_admission":
        events.pop(1)
    elif mutation == "origin":
        events[2].origin = "worker"
    elif mutation == "admission_origin":
        events[1].origin = "worker"
    elif mutation == "replacement_digest":
        events[2].payload["superseded_by_package_digest"] = "f" * 64
    elif mutation == "cause":
        events[2].causation_id = "unrelated"
    elif mutation == "missing_identity":
        events[0].payload.pop("plan_artifact_package_ref")
    else:
        field = {"run": "workflow_run_id", "digest": "package_digest", "ref": "package_ref", "slot": "package_slot"}[mutation]
        events[2].payload[field] = "f" * 64 if mutation == "digest" else "foreign"
    assert pending_plan_requests(events) == [events[0]]
    assert has_pending_plan_approval(events, workflow_run_id="r-1")


def test_new_plan_and_other_run_still_require_approval():
    events = _events()
    new = ZfEvent(type="plan.approval.requested", payload={
        "plan_id": "new-plan", "workflow_run_id": "r-1",
        "plan_artifact_package_ref": "artifacts/new.json", "plan_artifact_package_digest": "b" * 64,
    })
    other = deepcopy(events[0]); other.id = "request-other"; other.payload = {**other.payload, "plan_id": "other-plan", "workflow_run_id": "r-2"}
    events += [new, other]
    assert pending_plan_requests(events) == [new, other]
    assert has_pending_plan_approval(events, workflow_run_id="r-1")
    assert _tick_until_fire([_goal_started(), *events], _cfg(), _state(), _Writer(), ticks=20) == ""


@pytest.mark.parametrize("event_type", ["plan.approved", "plan.rejected"])
def test_decision_entry_does_not_write_for_retired_request(tmp_path, event_type):
    log = EventLog(tmp_path / "events.jsonl"); writer = EventWriter(log)
    for event in _events():
        writer.append(event)
    before = log.read_all()
    result, reason = decide_pending_plan(writer, state_dir=tmp_path, plan_id="old-plan",
                                         event_type=event_type, payload={})
    assert result is None and "not awaiting review" in reason
    assert log.read_all() == before


def test_explicit_readmission_allows_new_request_for_same_immutable_package():
    events = _events()
    admitted = deepcopy(events[1]); admitted.payload.update(package_ref="artifacts/old.json", package_digest="a" * 64)
    reopened = deepcopy(events[0]); reopened.id = "request-reopened"
    events += [admitted, reopened]
    assert pending_plan_requests(events) == [reopened]
