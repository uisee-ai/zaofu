"""Real HTTP registration, project confinement and evidence-only Channel reads."""

import pytest
from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.channel_contract_artifacts import persist_channel_prd
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.web.channel_delivery_routes import delivery_evidence
from zf.web.server import create_app


@pytest.fixture
def fixture(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    (state / "kanban.json").write_text("[]", encoding="utf-8")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-preview", "name": "Preview"})
    markdown = "# 完整 PRD\n\n## 验收\n\n| 项 | 结果 |\n|---|---|\n| A | OK |\n\n最后一行必须保留。\n"
    descriptor = persist_channel_prd(
        state, channel_id="ch-preview", thread_id="kanban:project", revision=2,
        previous_ref="", previous_digest="", body={"title": "Preview", "markdown": markdown},
        readiness_descriptor={}, created_by="leader", source_event_id="source",
    )
    prd = {"channel_id": "ch-preview", "thread_id": "kanban:project", "prd_revision": 2,
           "artifact_ref": descriptor["ref"], "artifact_digest": descriptor["sha256"]}
    writer.emit("channel.synthesis.proposed", actor="leader", payload=prd)
    client = TestClient(create_app(state, project_root=tmp_path))
    params = {"thread_id": "kanban:project", "revision": 2, "digest": descriptor["sha256"]}
    return client, state, writer, prd, params, markdown


def test_preview_is_complete_registered_and_read_only(fixture):
    client, state, _, prd, params, markdown = fixture
    before = (state / "events.jsonl").read_bytes()
    response = client.get("/api/projects/default/channels/ch-preview/prd-preview", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["markdown"] == markdown
    assert response.json()["artifact_digest"] == prd["artifact_digest"]
    assert response.json()["revision"] == 2
    assert (state / "events.jsonl").read_bytes() == before


def test_prd_preview_shows_only_version_bound_verified_owner_inputs(fixture):
    from zf.runtime.channel_attachments import capture_channel_attachment
    from zf.runtime.channel_projection import project_channel
    from zf.runtime.channel_semantic_sources import build_semantic_source_bundle
    from zf.runtime.channel_sidecar import channel_message_event_payload

    client, state, writer, _, params, _ = fixture
    thread = "kanban:project"
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-preview", "owner_actor_ref": "owner:operator"})
    attachment = capture_channel_attachment(state, channel_id="ch-preview", thread_id=thread,
        name="requirements.md", mime="text/markdown", data="必须支持撤销。".encode())
    message = channel_message_event_payload(state, {"channel_id": "ch-preview", "thread_id": thread,
        "message_id": "owner-input", "member_id": "operator", "role": "user", "source": "web", "text": "需求：支持回滚。",
        "refs": {"attachments": [attachment]}}, created_by="web")
    writer.emit("channel.message.posted", actor="web", correlation_id="ch-preview", payload=message)
    writer.emit("channel.message.posted", actor="author", correlation_id="ch-preview", payload={
        "channel_id": "ch-preview", "thread_id": thread, "message_id": "synth-trigger",
        "role": "assistant", "text": "synthesize", "refs": {"synthesis_request_id": "synth"}})
    channel = project_channel(state, "ch-preview")
    bundle = build_semantic_source_bundle(channel, thread_id=thread, trigger_message_id="synth-trigger", state_dir=state)
    assert bundle["complete"]
    descriptor = persist_channel_prd(state, channel_id="ch-preview", thread_id=thread, revision=3,
        previous_ref="", previous_digest="", body={"title": "PRD", "markdown": "# PRD",
        "synthesis": {"source_coverage": bundle["manifest"], "requirement_coverage": [{"id": "REQ-1", "source_refs": ["owner-input"]}]}},
        readiness_descriptor={}, created_by="leader", source_event_id="synth")
    writer.emit("channel.synthesis.proposed", actor="leader", correlation_id="ch-preview", payload={
        "channel_id": "ch-preview", "thread_id": thread, "prd_revision": 3,
        "artifact_ref": descriptor["ref"], "artifact_digest": descriptor["sha256"]})
    late = channel_message_event_payload(state, {"channel_id": "ch-preview", "thread_id": thread,
        "message_id": "late", "member_id": "operator", "role": "user", "source": "web", "text": "下一版才包含此修改。"}, created_by="web")
    writer.emit("channel.message.posted", actor="web", correlation_id="ch-preview", payload=late)
    before = (state / "events.jsonl").read_bytes()
    result = client.get("/api/projects/default/channels/ch-preview/prd-preview", params={**params, "revision": 3, "digest": descriptor["sha256"]})
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["sources"]["status"] == "ready"
    assert [item["content"] for item in result["sources"]["items"]] == ["需求：支持回滚。", "必须支持撤销。"]
    assert result["requirement_coverage"][0]["source_refs"] == ["owner-input"]
    assert "下一版才包含此修改" not in str(result["sources"])
    assert (state / "events.jsonl").read_bytes() == before
    (state / attachment["body_ref"]["ref"]).write_text("tampered")
    damaged = client.get("/api/projects/default/channels/ch-preview/prd-preview", params={**params, "revision": 3, "digest": descriptor["sha256"]})
    assert damaged.status_code == 200
    assert damaged.json()["sources"]["status"] == "incomplete"
    assert damaged.json()["sources"]["items"][1]["available"] is False
    assert damaged.json()["sources"]["items"][1]["content"] == ""


@pytest.mark.parametrize("ref", ["generated.md", "generated.md §1—3"])
def test_registered_file_preview_binds_project_channel_thread_and_does_not_write(fixture, ref):
    client, state, writer, _, _, markdown = fixture
    (state.parent / "generated.md").write_text(markdown)
    writer.emit("channel.message.posted", actor="leader", payload={
        "channel_id": "ch-preview", "message_id": "file-message", "thread_id": "main",
        "member_id": "leader", "text": "Generated document", "refs": {"artifacts": [{"path": ref}]},
    })
    before = (state / "events.jsonl").read_bytes()
    url = "/api/projects/default/channels/ch-preview/file-preview"
    response = client.get(url, params={"thread_id": "main", "ref": ref})
    assert response.status_code == 200, response.text
    assert response.json()["content"] == markdown
    assert client.get(url, params={"thread_id": "other", "ref": ref}).status_code == 403
    assert client.get(url, params={"thread_id": "main", "ref": "unknown.md"}).status_code == 403
    assert client.get(url.replace("default", "unknown-project"), params={"thread_id": "main", "ref": ref}).status_code == 404
    assert (state / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("change", [{"revision": 1}, {"digest": "wrong"}, {"thread_id": "main"}])
def test_preview_rejects_stale_or_wrong_selection(fixture, change):
    client, _, _, _, params, _ = fixture
    assert client.get("/api/projects/default/channels/ch-preview/prd-preview", params={**params, **change}).status_code == 409


def test_missing_channel_and_cross_project_do_not_read_default(fixture):
    client, _, _, _, params, _ = fixture
    assert client.get("/api/projects/default/channels/ch-other/prd-preview", params=params).status_code == 404
    assert client.get("/api/projects/unregistered-project/channels/ch-preview/prd-preview", params=params).status_code == 404


@pytest.mark.parametrize("failure", ["missing", "corrupt", "identity", "too_large", "no_markdown", "escape"])
def test_preview_fails_closed_on_invalid_artifacts(fixture, failure, tmp_path):
    client, state, writer, prd, params, _ = fixture
    path = state / prd["artifact_ref"]
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_text("tampered", encoding="utf-8")
    elif failure == "escape":
        outside = tmp_path / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    else:
        payload = {"schema_version": "channel-prd.v1", "channel_id": "ch-preview", "thread_id": "kanban:project", "revision": 2,
                   "body": {"markdown": "# PRD"}}
        if failure == "identity":
            payload["thread_id"] = "other"
        if failure == "no_markdown":
            payload["body"] = {"summary": "Not the document"}
        if failure == "too_large":
            payload["body"]["markdown"] = "x" * (4 * 1024 * 1024 + 1)
        descriptor = write_sidecar_json(state, prd["artifact_ref"], payload, kind="channel_prd", schema_version="channel-prd.v1", created_by="test")
        prd = {**prd, "artifact_digest": descriptor["sha256"]}
        writer.emit("channel.synthesis.proposed", actor="leader", payload=prd)
        params = {**params, "digest": descriptor["sha256"]}
    response = client.get("/api/projects/default/channels/ch-preview/prd-preview", params=params)
    assert response.status_code == (413 if failure == "too_large" else 409), response.text
    assert "markdown" not in response.json()


def test_new_revision_invalidates_open_preview(fixture):
    client, _, writer, prd, params, _ = fixture
    writer.emit("channel.synthesis.proposed", actor="leader", payload={**prd, "prd_revision": 3, "artifact_digest": "new"})
    assert client.get("/api/projects/default/channels/ch-preview/prd-preview", params=params).status_code == 409


def test_delivery_endpoint_has_no_invented_execution(fixture):
    client, _, _, _, params, _ = fixture
    response = client.get("/api/projects/default/channels/ch-preview/delivery", params=params)
    assert response.status_code == 200
    assert response.json()["plans"] == response.json()["runs"] == []
    assert response.json()["planning"] == {}


def test_prd_evaluation_action_and_read_view_bind_current_revision(fixture, monkeypatch):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client, state, writer, prd, params, _ = fixture
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-preview", "owner_actor_ref": "web"})
    payload = {**prd, "role": "full"}
    response = client.post("/api/actions/channel-prd-evaluate",
                           headers={"x-zf-web-token": "test-token"}, json=payload)
    assert response.status_code == 202, response.text
    request_id = response.json()["request_id"]
    duplicate = client.post("/api/actions/channel-prd-evaluate",
                            headers={"x-zf-web-token": "test-token"}, json=payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["request_id"] == request_id
    url = "/api/projects/default/channels/ch-preview/prd-evaluation"
    view = client.get(url, params=params)
    assert view.status_code == 200
    assert view.json()["evaluations"][0]["status"] == "requested"
    manifest = {"context": {"channel_id": "ch-preview", "thread_id": "kanban:project"},
                "target": {"ref": prd["artifact_ref"], "revision": prd["prd_revision"]}}
    manifest_descriptor = write_sidecar_json(
        state, "artifacts/output-evaluations/test/input-manifest.json", manifest,
        kind="evaluation_input_manifest", schema_version="output-evaluation.v1",
        created_by="test")
    report = {"request_id": request_id, "verdict": "FAIL",
              "manifest_ref": manifest_descriptor["ref"],
              "manifest_digest": manifest_descriptor["sha256"],
              "findings": [{"severity": "P1", "title": "缺少权限条件"}],
              "coverage": {"read": ["all"]}}
    descriptor = write_sidecar_json(state, "artifacts/output-evaluations/test/report.json", report,
                                    kind="evaluation_report", schema_version="output-evaluation.v1",
                                    created_by="test")
    writer.emit("output.evaluation.completed", actor="test", correlation_id="ch-preview", payload={
        "request_id": request_id, "verdict": "FAIL", "report_ref": descriptor["ref"],
        "report_digest": descriptor["sha256"],
        "manifest_ref": manifest_descriptor["ref"],
        "manifest_digest": manifest_descriptor["sha256"]})
    view = client.get(url, params=params)
    assert view.json()["evaluations"][0]["verdict"] == "FAIL"
    assert view.json()["evaluations"][0]["findings"][0]["severity"] == "P1"
    writer.emit("channel.prd_evaluation.feedback.requested", actor="orchestrator-reactor", payload={
        "request_id": request_id, "channel_id": "ch-preview", "thread_id": "kanban:project"})
    assert client.get(url, params=params).json()["evaluations"][0]["feedback"]["status"] == "requested"
    writer.emit("channel.prd_evaluation.feedback.reviewed", actor="critic", payload={
        "request_id": request_id, "review": {"ref": "artifacts/triage.json", "sha256": "triage"}})
    result = client.get(url, params=params).json()["evaluations"][0]
    assert result["feedback"]["status"] == "reviewed"
    assert result["verdict"] == "FAIL"
    writer.emit("channel.synthesis.proposed", actor="leader", payload={**prd, "prd_revision": 3,
                                                                        "artifact_digest": "new"})
    assert client.get(url, params=params).status_code == 409


def test_prd_evaluation_rejects_wrong_owner_and_stale_digest(fixture, monkeypatch):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client, _, writer, prd, _, _ = fixture
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-preview", "owner_actor_ref": "web"})
    payload = {**prd, "artifact_digest": "wrong", "role": "focused"}
    response = client.post("/api/actions/channel-prd-evaluate",
                           headers={"x-zf-web-token": "test-token"}, json=payload)
    assert response.status_code == 409
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-preview",
                                                         "owner_actor_ref": "owner:alice"})
    response = client.post("/api/actions/channel-prd-evaluate",
                           headers={"x-zf-web-token": "test-token"},
                           json={**prd, "role": "focused"})
    assert response.status_code == 403


def test_prd_evaluation_view_rejects_report_for_another_target(fixture, monkeypatch):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client, state, writer, prd, params, _ = fixture
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-preview", "owner_actor_ref": "web"})
    response = client.post("/api/actions/channel-prd-evaluate",
                           headers={"x-zf-web-token": "test-token"},
                           json={**prd, "role": "full"})
    request_id = response.json()["request_id"]
    manifest = write_sidecar_json(state, "artifacts/output-evaluations/foreign/manifest.json",
                                  {"context": {"channel_id": "ch-preview", "thread_id": "kanban:project"},
                                   "target": {"ref": "another-prd", "revision": 2}},
                                  kind="evaluation_input_manifest", schema_version="output-evaluation.v1",
                                  created_by="test")
    report = write_sidecar_json(state, "artifacts/output-evaluations/foreign/report.json",
                                {"request_id": request_id, "verdict": "PASS",
                                 "manifest_ref": manifest["ref"], "manifest_digest": manifest["sha256"]},
                                kind="evaluation_report", schema_version="output-evaluation.v1",
                                created_by="test")
    writer.emit("output.evaluation.completed", actor="test", correlation_id="ch-preview", payload={
        "request_id": request_id, "verdict": "PASS", "report_ref": report["ref"],
        "report_digest": report["sha256"], "manifest_ref": manifest["ref"],
        "manifest_digest": manifest["sha256"]})
    result = client.get("/api/projects/default/channels/ch-preview/prd-evaluation", params=params)
    assert result.status_code == 200
    assert result.json()["evaluations"][0]["status"] == "invalid_evidence"


def test_delivery_joins_current_prd_tasks_plans_and_run_states():
    prd = {"artifact_ref": "channels/ch-a/prd/r2.json", "artifact_digest": "digest", "prd_revision": 2}
    lineage = {"channel_id": "ch-a", "thread_id": "main", "source_ref": prd["artifact_ref"], "source_digest": "digest", "prd_revision": 2}
    receipt = {**prd, "thread_id": "main", "receipt_kind": "task_created", "revision": 2, "task_id": "T2"}
    channel = {"channel_id": "ch-a", "result_receipts": [receipt, {**receipt, "revision": 1, "task_id": "OLD"}]}
    def event(kind, payload, **kwargs):
        return ZfEvent(type=kind, actor="test", payload=payload, **kwargs)
    plan = {"valid": True, "request_id": "plan", "revision": 1, "subject_type": "task_workflow", "task_id": "T2", "workflow_parameters": lineage}
    events = [
        event("kanban.agent.plan.requested", {"plan_request": plan}),
        event("kanban.agent.plan.requested", {"plan_request": {**plan, "request_id": "old", "workflow_parameters": {**lineage, "prd_revision": 1}}}),
        event("run.admission.queued", {"run_id": "run2", "request_id": "request"}, task_id="T2"),
        event("run.started", {"run_id": "old-run"}, task_id="OLD"),
    ]
    view = delivery_evidence(events, channel, "main", prd)
    assert len(view["plans"]) == 1
    assert [row["status"] for row in view["runs"]] == ["queued"]
    for kind, status in [("run.admission.admitted", "running"), ("run.paused", "paused"), ("run.resumed", "running"), ("run.cancelled", "cancelled")]:
        events.append(event(kind, {"run_id": "run2"}, task_id="T2"))
        assert delivery_evidence(events, channel, "main", prd)["runs"][0]["status"] == status
    events.append(event("kanban.agent.plan.answered", {"request_id": "plan", "revision": 1}))
    assert delivery_evidence(events, channel, "main", prd)["plans"] == []


def test_planning_uses_causation_not_an_unrelated_agent_turn():
    prd = {"artifact_ref": "prd", "artifact_digest": "digest", "prd_revision": 2}
    request = ZfEvent(type="web.action.requested", actor="web", payload={"request": {
        "source": "web-channel-workflow-plan", "workflow_context": {"channel_id": "ch-a", "thread_id": "main", "source_ref": "prd", "source_digest": "digest", "prd_revision": 2}}})
    start = ZfEvent(type="kanban.agent.turn.started", actor="web", causation_id=request.id)
    unrelated = ZfEvent(type="kanban.agent.turn.failed", actor="web", causation_id="other")
    view = delivery_evidence([request, start, unrelated], {"channel_id": "ch-a"}, "main", prd)
    assert view["planning"]["status"] == "planning"
    failure = ZfEvent(type="kanban.agent.turn.failed", actor="web", causation_id=start.id)
    assert delivery_evidence([request, start, failure], {"channel_id": "ch-a"}, "main", prd)["planning"]["status"] == "failed"


def test_proposal_stays_awaiting_approval_after_plan_answer():
    prd = {"artifact_ref": "prd", "artifact_digest": "digest", "prd_revision": 2}
    authority = {"channel_id": "ch-a", "thread_id": "main", "source_ref": "prd", "source_digest": "digest", "prd_revision": 2}
    event = ZfEvent(type="kanban.agent.action.proposed", actor="web", payload={"proposal": {
        "proposal_id": "proposal", "valid": True, "action": "create-task", "payload": {"channel_authority": authority}}})
    view = delivery_evidence([event], {"channel_id": "ch-a"}, "main", prd)
    assert view["plans"] == [{"request_id": "proposal", "task_id": "", "subject_type": "task_create", "status": "awaiting_approval"}]


@pytest.mark.parametrize("fault", ["none", "content", "channel", "thread", "revision", "binding"])
def test_preview_exposes_complete_bound_readiness_or_rejects_invalid_risk_evidence(fixture, fault):
    from zf.runtime.channel_contract_artifacts import persist_channel_prd_readiness

    client, state, writer, _, _, markdown = fixture
    readiness = persist_channel_prd_readiness(state,
        channel_id="ch-other" if fault == "channel" else "ch-preview",
        thread_id="other" if fault == "thread" else "kanban:project",
        revision=4 if fault == "revision" else 3,
        body={"verdict": "needs_multi_lens", "implementation_start": False,
              "reason": "Runner does not exist", "gaps": ["Browser runner missing"],
              "risks": [f"Risk {i}" for i in range(25)]},
        created_by="leader", source_event_id="source")
    prd = persist_channel_prd(state, channel_id="ch-preview", thread_id="kanban:project", revision=3,
        previous_ref="", previous_digest="", body={"title":"Risk review", "markdown": markdown},
        readiness_descriptor=readiness, created_by="leader", source_event_id="source")
    writer.emit("channel.synthesis.proposed", actor="leader", payload={
        "channel_id":"ch-preview", "thread_id":"kanban:project", "prd_revision":3,
        "artifact_ref":prd["ref"], "artifact_digest":prd["sha256"],
        "readiness_ref":readiness["ref"], "readiness_digest":"wrong" if fault == "binding" else readiness["sha256"],
    })
    if fault == "content":
        (state / readiness["ref"]).write_text('{"reason":"changed"}')
    before = (state / "events.jsonl").read_bytes()
    response = client.get("/api/projects/default/channels/ch-preview/prd-preview", params={
        "thread_id":"kanban:project", "revision":3, "digest":prd["sha256"],
    })
    assert response.status_code == (200 if fault == "none" else 409), response.text
    if fault == "none":
        result = response.json()["readiness"]
        assert result["ref"] == readiness["ref"]
        assert result["digest"] == readiness["sha256"]
        assert result["gaps"] == ["Browser runner missing"]
        assert len(result["risks"]) == 25
        assert result["risks"][-1] == "Risk 24"
        assert response.json()["markdown"] == markdown
    assert (state / "events.jsonl").read_bytes() == before
