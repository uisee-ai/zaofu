"""Web/CLI expose the same blockers and a token-gated repair entrypoint."""
from tests.test_web_workflow_proposals import _client, _action_body
from zf.cli.workflow_prepare import request_preview
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.project_git_baseline import preview_git_baseline
from zf.runtime.workflow_operation import WorkflowOperationService


def test_failed_attempt_is_not_ready_in_list_detail_or_cli(tmp_path, monkeypatch, capsys):
    client, state = _client(tmp_path, monkeypatch)
    response = client.post("/api/projects/default/actions/workflow-request",
        headers={"X-ZF-Web-Token": "test-token"},
        json=_action_body("prepare-failure", {"kind": "auto", "objective": "Repair login", "backend": "mock"}))
    assert response.status_code == 202, response.text
    queued = response.json()
    log = EventLog(state / "events.jsonl")
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log))
    service.fail(operation_id=queued["synthesis_operation_id"], request_hash=queued["synthesis_request_hash"],
                 workflow_run_id=f"workflow-request:{queued['request_id']}:r{queued['request_revision']}", reason="test preparation failed")
    detail = client.get(f"/api/projects/default/workflow-requests/{queued['request_id']}").json()
    assert detail["status"] == "preparation_failed"
    assert detail["preparation"]["blockers"][0]["message"] == "test preparation failed"
    listing = client.get("/api/projects/default/workflow-requests").json()
    row = next(x for x in listing["items"] if x["request_id"] == queued["request_id"])
    assert row["status"] == detail["status"]
    preview = request_preview(state, queued["request_id"], project_root=tmp_path)
    assert not preview["ok"]
    assert preview["preparation"] == detail["preparation"]
    from zf.cli.workflow_prepare import _print
    _print(preview, "md")
    assert "test preparation failed" in capsys.readouterr().out


def test_baseline_web_action_requires_auth_and_exact_manifest(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    manifest = preview_git_baseline(tmp_path, state, paths=["zf.yaml"], exclude_paths=["dataset"])
    body = _action_body("baseline-test", {"manifest": manifest})
    route = "/api/projects/default/actions/project-git-baseline"
    rejected = client.post(route, json=body)
    assert rejected.status_code in {401, 403}
    assert not (tmp_path / ".git").exists()
    accepted = client.post(route, headers={"X-ZF-Web-Token": "test-token"}, json=body)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "applied"
