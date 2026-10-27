"""Blocked YAML is editable, but never becomes an approved or started Run."""
from pathlib import Path
import json

import pytest

from tests.test_workflow_preview_review import Agent, setup
from zf.runtime.workflow_delivery import build_flow_submit_preview
from zf.runtime.workflow_preview_review import PreviewReviewError
from zf.runtime.workflow_requests import load_workflow_request, revise_workflow_request


@pytest.fixture
def blocked(setup, monkeypatch):
    client, service, binding = setup
    req = load_workflow_request(service.state_dir, binding["request_id"])
    req = revise_workflow_request(service.state_dir, Path(req["workflow_input_manifest_ref"]),
                                 actor="test", confirm=True, writer=service.writer)
    blocker = {"severity": "STOP", "kind": "missing_closure", "message": "Review required skills"}
    monkeypatch.setattr("zf.runtime.workflow_proposal._synthesis_compatibility_blockers", lambda **kwargs: [blocker])
    manifest = json.loads(Path(req["workflow_input_manifest_ref"]).read_text())
    ctx = Path(manifest["intake_json_ref"])
    preview = build_flow_submit_preview(config_path=service.config_path, intake_path=ctx,
                                        flow_kind="issue", allow_missing_env=True)
    assert load_workflow_request(service.state_dir, req["request_id"])["status"] == "ready"
    return client, service, {"request_id": req["request_id"], "revision": req["revision"],
                             "proposal_digest": preview["proposal"]["proposal_digest"]}


def test_blocked_candidate_edit_is_visible_after_reload_and_remains_unstarted(blocked):
    client, service, binding = blocked
    baseline = service.config_path.read_bytes()
    headers = {"x-zf-web-token": "test-token"}
    context = client.post("/api/projects/default/workflow-preview-review", headers=headers,
                          json={**binding, "operation": "context"})
    assert context.status_code == 200, context.text
    assert context.json()["fields"]["config.0.lanes"]["editable"]
    edit = service.discuss({**binding, "field": "config.0.lanes", "mode": "revise", "message": "Two lanes"},
                           agent=Agent({"answer": "并行数改为 2", "replacement": 2}))
    result = service.apply(edit["draft_ref"])
    assert result["blockers"] and not result["started"]
    detail = client.get(f"/api/projects/default/workflow-requests/{binding['request_id']}").json()
    assert detail["result"]["status"] == "ready"
    assert not detail["result"].get("proposal_ref")
    assert detail["proposal"]["proposal_digest"] == result["proposal_digest"]
    assert "lanes: 2" in detail["artifacts"]["effective_config"]["yaml"]
    assert not detail["lifecycle"]["submitted"]
    assert service.config_path.read_bytes() == baseline
    assert service.apply(edit["draft_ref"])["replayed"]
    service.context({"request_id": binding["request_id"], "revision": result["revision"],
                     "proposal_digest": result["proposal_digest"]})
    with pytest.raises(PreviewReviewError, match="Preview changed"):
        service.context(binding)


def test_blocked_candidate_rejects_wrong_digest_and_new_requirement(blocked):
    _, service, binding = blocked
    with pytest.raises(PreviewReviewError, match="Preview changed"):
        service.context({**binding, "proposal_digest": "wrong"})
    req = load_workflow_request(service.state_dir, binding["request_id"])
    req = revise_workflow_request(service.state_dir, Path(req["workflow_input_manifest_ref"]),
                                 actor="test", confirm=True, writer=service.writer)
    with pytest.raises(PreviewReviewError, match="Preview changed"):
        service.context({**binding, "revision": req["revision"]})


def test_blocked_edit_resumes_after_compilation_failure(blocked, monkeypatch):
    _, service, binding = blocked
    edit = service.discuss({**binding, "field": "config.0.lanes", "mode": "revise", "message": "Two lanes"},
                           agent=Agent({"answer": "修改", "replacement": 2}))
    import zf.runtime.workflow_preview_review as module
    build = module.build_workflow_proposal
    def fail(*args, **kwargs):
        raise OSError("interrupted compilation")
    monkeypatch.setattr(module, "build_workflow_proposal", fail)
    with pytest.raises(OSError, match="interrupted"):
        service.apply(edit["draft_ref"])
    monkeypatch.setattr(module, "build_workflow_proposal", build)
    result = service.apply(edit["draft_ref"])
    assert result["revision"] == binding["revision"] + 1
    assert result["blockers"] and not result["started"]


def test_reprepared_candidate_supersedes_the_previous_review_result(blocked, monkeypatch):
    _, service, binding = blocked
    edit = service.discuss({**binding, "field": "config.0.lanes", "mode": "revise", "message": "Two lanes"},
                           agent=Agent({"answer": "修改", "replacement": 2}))
    result = service.apply(edit["draft_ref"])
    req = load_workflow_request(service.state_dir, binding["request_id"])
    manifest = json.loads(Path(req["workflow_input_manifest_ref"]).read_text())
    monkeypatch.setattr("zf.runtime.workflow_proposal._synthesis_compatibility_blockers",
                        lambda **kwargs: [{"severity": "STOP", "kind": "new_closure", "message": "Reprepared"}])
    newer = build_flow_submit_preview(config_path=service.config_path,
                intake_path=Path(manifest["intake_json_ref"]), flow_kind="issue", allow_missing_env=True)
    with pytest.raises(PreviewReviewError, match="Preview changed"):
        service.context({**binding, "revision": result["revision"], "proposal_digest": result["proposal_digest"]})
    ctx = service.context({**binding, "revision": result["revision"], "proposal_digest": newer["proposal"]["proposal_digest"]})
    assert ctx["fields"]["config.0.lanes"]["value"] == 1


def test_blocked_candidate_can_iterate_while_preflight_still_fails(blocked, monkeypatch):
    _, service, binding = blocked
    import zf.runtime.workflow_preflight as module
    original = module.build_flow_preflight_report
    def still_blocked(*args, **kwargs):
        report = original(*args, **kwargs)
        return {**report, "status": "STOP", "blockers": [*report.get("blockers", []),
                {"severity": "STOP", "kind": "remaining_preflight", "message": "Another field still needs review"}]}
    monkeypatch.setattr(module, "build_flow_preflight_report", still_blocked)
    baseline = service.config_path.read_bytes()
    edit = service.discuss({**binding, "field": "config.0.lanes", "mode": "revise", "message": "Two lanes"},
                           agent=Agent({"answer": "局部修改", "replacement": 2}))
    result = service.apply(edit["draft_ref"])
    assert any(b["kind"] == "remaining_preflight" for b in result["blockers"])
    assert not result["started"]
    assert load_workflow_request(service.state_dir, binding["request_id"])["status"] == "ready"
    assert service.config_path.read_bytes() == baseline
