from pathlib import Path

import pytest

from tests.test_workflow_proposal import _config, _request
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.operator_inbox import build_operator_inbox, build_runtime_requests
from zf.runtime.workflow_proposal import build_workflow_proposal
from zf.runtime.workflow_requests import mark_workflow_request, reject_workflow_proposal
from zf.web.projections import read_model


def seed(tmp_path: Path):
    config = tmp_path / "zf.yaml"
    _config(config)
    state = tmp_path / "state"
    request = _request(state)
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    proposal, ref = build_workflow_proposal(
        state, request=request, base_config_path=config,
        preflight={"status": "GO", "blockers": []}, flow_kind="issue", writer=writer,
    )
    return state, log, writer, proposal, ref


def test_workflow_approval_is_bound_to_source_and_sqlite_agrees(tmp_path: Path) -> None:
    state, log, writer, proposal, ref = seed(tmp_path)
    before = log.read_all()
    inbox = build_operator_inbox(state, before)
    item = inbox["pending"][0]
    assert item["kind"] == "approval" and item["owner_route"] == "workflow_proposal"
    assert item["summary"] == "Repair the confirmed issue"
    assert item["workflow_proposal_ref"] == ref
    assert item["proposal_digest"] == proposal["proposal_digest"]
    assert item["deep_link"] == "?page=workflows&request=req-1"
    assert item["actions"] == []  # Review the exact configuration at the existing source.
    writer.emit("inbox.item.read", payload={"item_id": item["id"], "read_version": item["read_version"]})
    raw = build_operator_inbox(state, log.read_all())
    indexed = read_model.operator_inbox(state)
    assert raw["items"] == indexed["items"]
    assert raw["summary"]["pending"] == 1 and raw["summary"]["unread"] == 0
    assert len(log.read_all()) == len(before) + 1


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_original_workflow_decision_closes_inbox_without_claiming_completion(tmp_path: Path, decision: str) -> None:
    state, log, writer, proposal, ref = seed(tmp_path)
    if decision == "rejected":
        reject_workflow_proposal(state, request_id="req-1", proposal_ref=ref,
                                 proposal_digest=proposal["proposal_digest"], actor="web", writer=writer, reason="Needs revision")
    else:
        mark_workflow_request(state, "req-1", status="approved", actor="web", writer=writer,
                              event_type="workflow.request.approved")
        mark_workflow_request(state, "req-1", status="submitted", actor="web", writer=writer,
                              event_type="workflow.request.submitted")
    inbox = build_operator_inbox(state, log.read_all())
    assert inbox["pending"] == [] and inbox["summary"]["unread"] == 0
    assert inbox["items"][0]["status"] == decision
    assert inbox["items"][0]["actions"] == []
    if decision == "approved":
        assert inbox["items"][0]["execution_status"] == "submitted"
    assert inbox["items"] == read_model.operator_inbox(state)["items"]


def test_revised_requirement_supersedes_old_approval_and_old_read_cannot_cover_new_request(tmp_path: Path) -> None:
    state, log, writer, _, _ = seed(tmp_path)
    first = build_operator_inbox(state, log.read_all())["pending"][0]
    writer.emit("workflow.request.updated", payload={"request_id": "req-1", "revision": 2, "status": "clarifying"})
    writer.emit("inbox.item.read", payload={"item_id": first["id"], "read_version": first["read_version"]})
    inbox = build_operator_inbox(state, log.read_all())
    assert inbox["pending"] == [] and inbox["items"][0]["status"] == "superseded"


def test_tampered_workflow_sidecar_cannot_produce_an_approval(tmp_path: Path) -> None:
    state, log, _, _, ref = seed(tmp_path)
    path = state / ref["ref"]
    path.write_text("{}")
    assert build_operator_inbox(state, log.read_all())["items"] == []


@pytest.mark.parametrize("terminal", ["approval.resolved", "approval.expired", "approval.rejected_by_policy"])
def test_actionable_replan_has_history_but_unsupported_approval_stays_in_operations(tmp_path: Path, terminal: str) -> None:
    source = ZfEvent(type="approval.requested", payload={
        "approval_ref": "review", "owner_route": "plan_synth_owner_decision",
        "proposal_ref": "proposal.json", "eval_ref": "eval.json",
        "approve_action": "replan-approve", "reject_action": "replan-reject",
    })
    unsupported = ZfEvent(type="approval.requested", payload={"approval_ref": "legacy", "approve_action": "unknown"})
    events = [source, unsupported]
    assert len(build_operator_inbox(tmp_path, events)["pending"]) == 1
    events.append(ZfEvent(type=terminal, payload={"approval_ref": "review"}))
    result = build_operator_inbox(tmp_path, events)
    assert result["pending"] == [] and result["summary"]["unread"] == 0
    assert result["items"][0]["actions"] == []
    assert build_runtime_requests(tmp_path, events)[0]["approval_ref"] == "legacy"
