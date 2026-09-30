from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from issue_core import AgentResult

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.runtime.self_issue_session_host import SelfIssueSessionHost
from zf.web.server import create_app


async def test_poll_and_evidence_reads_do_not_write_receipts_or_events(tmp_path, monkeypatch):
    state = tmp_path / ".state"
    state.mkdir()
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    class ReadyAgent:
        def run(self, *, session, working_set, tools):
            return AgentResult(status="ready", working_set=replace(working_set, report={"title": "Refresh fails"}))

    host = SelfIssueSessionHost(state, tmp_path, agent=ReadyAgent())
    created = host.create_and_run("Refresh fails. Reproduction: click Refresh. Expected: page updates")
    sid = created["session_id"]
    draft = host.materialize_publication_draft(sid)
    host._progress(sid, "investigation", "inspect", detail={"result_detail": "full tool output"})
    activity_id = host.store.load_working_set(sid).workflow["activity_log"][-1]["id"]
    app = create_app(state, config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")), project_root=tmp_path)
    event_path = state / "events.jsonl"
    before = event_path.read_bytes() if event_path.exists() else b""
    receipts = state / "web-actions/idempotency.jsonl"
    receipt_before = receipts.read_bytes() if receipts.exists() else b""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-ZF-Web-Token": "test-token", "X-Idempotency-Key": "same-poll-key"}
        for action, payload in [
            ("self-issue-get", {}),
            ("self-issue-session-get", {"session_id": sid}),
            ("self-issue-session-draft-version", {"session_id": sid, "version": draft["editor"]["revision"]}),
            ("self-issue-session-activity-result", {"session_id": sid, "activity_id": activity_id}),
        ]:
            for _ in range(3):
                response = await client.post(f"/api/actions/{action}", headers=headers, json=payload)
                assert response.status_code == 200, response.text
                assert response.json()["ok"]
        working = host.store.load_working_set(sid)
        host.store.save_working_set(replace(working, fields={**working.fields, "actual_behavior": "new observation"}), expected_revision=working.revision)
        fresh = await client.post("/api/actions/self-issue-session-get", headers=headers, json={"session_id": sid})
        assert fresh.json()["report"]["actual_behavior"] == "new observation"
        denied = await client.post("/api/actions/self-issue-session-get", json={"session_id": sid})
        assert denied.status_code == 403
    assert (event_path.read_bytes() if event_path.exists() else b"") == before
    assert (receipts.read_bytes() if receipts.exists() else b"") == receipt_before


@pytest.mark.parametrize("payload,status", [({}, 422), ({"session_id": "issue-session-missing"}, 404)])
async def test_read_validation_still_fails_closed(tmp_path: Path, monkeypatch, payload, status):
    state = tmp_path / ".state"
    state.mkdir()
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    app = create_app(state, project_root=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/actions/self-issue-session-get",
                                     headers={"X-ZF-Web-Token": "test-token"}, json=payload)
    assert response.status_code == status
