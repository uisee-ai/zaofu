"""Blocked compilation remains visible without becoming executable."""

from pathlib import Path

from tests.test_web_workflow_proposals import _client
from zf.core.events import EventLog, EventWriter
from zf.runtime import workflow_delivery
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_requests import revise_workflow_request


def test_blocked_yaml_is_visible_but_not_approvable_or_reused_after_revision(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    intake = build_flow_intake(
        kind="issue", objective="Repair session expiry", backend="mock",
        request_id="blocked-preview", output=tmp_path / "docs/intake/request.md",
    )
    writer = EventWriter(EventLog(state / "events.jsonl"))
    current = revise_workflow_request(
        state, Path(intake["workflow_input_manifest_ref"]),
        actor="test", confirm=True, writer=writer,
    )
    original = workflow_delivery.build_flow_preflight_report

    def blocked(*args, **kwargs):
        report = original(*args, **kwargs)
        return {**report, "status": "STOP", "blockers": [
            {"severity": "STOP", "kind": "missing_capability", "message": "Required skill is absent"},
        ]}

    monkeypatch.setattr(workflow_delivery, "build_flow_preflight_report", blocked)
    preview = workflow_delivery.build_flow_submit_preview(
        config_path=tmp_path / "zf.yaml", intake_path=Path(intake["intake_ref"]),
        flow_kind="issue", allow_missing_env=True,
    )
    assert preview["status"] == "STOP"
    detail = client.get("/api/projects/default/workflow-requests/blocked-preview").json()
    assert detail["result"]["status"] == "ready"
    assert not detail["result"].get("proposal_ref")
    assert detail["proposal"]["approval_status"] == "blocked"
    assert detail["proposal"]["blockers"][0]["kind"] == "missing_capability"
    assert "kind: IssueFlow" in detail["artifacts"]["effective_config"]["yaml"]
    response = client.post(
        "/api/projects/default/actions/workflow-submit",
        headers={"x-zf-web-token": "test-token"},
        json={"project_id": "default", "idempotency_key": "blocked-submit", "payload": {
            "request_id": "blocked-preview", "proposal_ref": preview["proposal_ref"],
            "proposal_digest": preview["proposal"]["proposal_digest"],
            "intake_ref": intake["intake_ref"], "kind": "issue", "allow_missing_env": True,
        }},
    )
    assert response.status_code == 409
    assert not any(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all())
    revise_workflow_request(
        state, Path(intake["workflow_input_manifest_ref"]), actor="test",
        expected_revision=current["revision"], objective="Repair updated session policy",
        confirm=True, writer=writer,
    )
    changed = client.get("/api/projects/default/workflow-requests/blocked-preview").json()
    assert changed["proposal"] == {}
    assert changed["artifacts"] == {}
