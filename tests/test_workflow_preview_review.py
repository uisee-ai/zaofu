"""Preview API contract: discussion is not approval; patches are narrow and fenced."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_delivery import build_flow_submit_preview
from zf.runtime.workflow_requests import (
    hydrate_workflow_requirement,
    load_workflow_request,
    mark_workflow_request,
    revise_workflow_request,
)
from zf.runtime.workflow_preview_review import WorkflowPreviewReview, PreviewReviewError
from tests.test_web_workflow_proposals import _client


@pytest.fixture
def setup(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        config_path.read_text().replace("  lanes:", "  topology: light\n  lanes:")
    )
    writer = EventWriter(EventLog(state / "events.jsonl"))
    intake = build_flow_intake(
        kind="issue",
        objective="Fix checkout expiry",
        request_id="review-test",
        backend="mock",
        project_id="default",
        acceptance=("Active sessions remain valid.", "Expired sessions fail."),
        constraints=("Preserve API.",),
        output=tmp_path / "docs/intake/review.md",
    )
    revise_workflow_request(
        state,
        Path(intake["workflow_input_manifest_ref"]),
        actor="test",
        confirm=True,
        writer=writer,
    )
    build_flow_submit_preview(
        config_path=tmp_path / "zf.yaml",
        intake_path=Path(intake["intake_ref"]),
        flow_kind="issue",
        allow_missing_env=True,
    )
    service = WorkflowPreviewReview(state, tmp_path, writer)
    req = load_workflow_request(state, "review-test")
    assert req["status"] == "proposed"
    binding = {
        "request_id": req["request_id"],
        "revision": req["revision"],
        "proposal_digest": req["proposal_digest"],
    }
    return client, service, binding


class Agent:
    def __init__(self, reply, callback=None):
        self.reply, self.callback = reply, callback
        self.calls = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        if self.callback:
            self.callback()
        return SimpleNamespace(ok=True, reply=json.dumps(self.reply), incomplete=False)


def revision(service, binding, field="requirement.acceptance.0", **kwargs):
    return service.discuss(
        {
            **binding,
            "field": field,
            "mode": "revise",
            "message": "Only refine this fragment",
            **kwargs,
        },
        agent=Agent(
            {
                "answer": "只修改选中部分",
                "replacement": "Active sessions including empty input remain valid.",
            }
        ),
    )


def test_discussion_is_not_a_patch_or_execution(setup):
    _, service, binding = setup
    before = load_workflow_request(service.state_dir, binding["request_id"])
    agent = Agent({"answer": "解释", "replacement": "Must not apply"})
    result = service.discuss(
        {**binding, "field": "requirement.objective", "message": "Why?"}, agent=agent
    )
    assert not result["draft_ref"] and not result["changes"]
    assert load_workflow_request(service.state_dir, binding["request_id"]) == before
    assert agent.calls[0]["permission_profile"] == "read_only"
    message = [
        e for e in service.writer.event_log.read_all() if e.type == "user.message"
    ][-1]
    assert message.payload["target"] == "kanban-agent"
    assert message.payload["runtime_delivery"] == "headless"
    assert not any(
        e.type == "workflow.invoke.requested"
        for e in service.writer.event_log.read_all()
    )


def test_local_patch_preserves_other_fields_and_replays_once(setup):
    _, service, binding = setup
    before = service.context(binding)["requirement"]
    draft = revision(service, binding)
    result = service.apply(draft["draft_ref"])
    assert not result["blockers"], result["blockers"]
    assert result["revision"] == binding["revision"] + 1 and not result["started"]
    after = hydrate_workflow_requirement(
        service.state_dir,
        load_workflow_request(service.state_dir, binding["request_id"]),
    )
    assert after["acceptance"] == [
        draft["changes"][0]["after"],
        before["acceptance"][1],
    ]
    for key in ("objective", "constraints", "source_refs", "task_input_binding"):
        assert after[key] == before[key]
    assert service.apply(draft["draft_ref"])["replayed"]
    assert (
        len(
            [
                e
                for e in service.writer.event_log.read_all()
                if e.type == "web.action.completed"
                and e.payload.get("preview_review_digest")
            ]
        )
        == 1
    )
    with pytest.raises(PreviewReviewError, match="Preview changed"):
        service.context(binding)


def test_quote_patch_changes_only_the_selected_substring(setup):
    _, service, binding = setup
    result = service.discuss(
        {
            **binding,
            "field": "requirement.acceptance.0",
            "mode": "revise",
            "message": "Include empty sessions",
            "quote": {"text": "Active sessions"},
        },
        agent=Agent(
            {"answer": "仅替换引用", "replacement": "Active and empty sessions"}
        ),
    )
    assert result["changes"][0]["after"] == "Active and empty sessions remain valid."


@pytest.mark.parametrize(
    "payload",
    [{"quote": {"text": "invented"}}, {"revision": 999}, {"field": "config.0.secret"}],
)
def test_invalid_or_stale_reference_never_calls_provider(setup, payload):
    _, service, binding = setup
    agent = Agent({"answer": "should not run"})
    with pytest.raises(PreviewReviewError):
        service.discuss(
            {
                **binding,
                "field": "requirement.objective",
                "message": "Change",
                **payload,
            },
            agent=agent,
        )
    assert not agent.calls


def test_started_and_late_provider_result_are_rejected(setup):
    _, service, binding = setup

    def start():
        mark_workflow_request(
            service.state_dir,
            binding["request_id"],
            status="approved",
            actor="test",
            writer=service.writer,
        )

    with pytest.raises(PreviewReviewError, match="unstarted"):
        service.discuss(
            {**binding, "field": "requirement.objective", "message": "Why?"},
            agent=Agent({"answer": "late"}, start),
        )


def test_config_patch_builds_candidate_without_applying_yaml(setup):
    _, service, binding = setup
    baseline = service.config_path.read_bytes()
    result = service.discuss(
        {
            **binding,
            "field": "config.0.lanes",
            "mode": "revise",
            "message": "Two lanes",
        },
        agent=Agent({"answer": "提高并发", "replacement": 2}),
    )
    applied = service.apply(result["draft_ref"])
    assert applied["requires_config_apply"] and not applied["started"]
    assert service.config_path.read_bytes() == baseline
    assert not list(service.project_root.glob(".zf-preview-*.yaml"))


def test_http_context_auth_and_apply_are_wired(setup, monkeypatch):
    client, service, binding = setup
    url = "/api/projects/default/workflow-preview-review"
    headers = {"x-zf-web-token": "test-token"}
    assert client.post(url, json={**binding, "operation": "context"}).status_code == 403
    context = client.post(
        url, json={**binding, "operation": "context"}, headers=headers
    )
    assert context.status_code == 200, context.text
    assert "requirement.acceptance.0" in context.json()["fields"]
    agent = Agent({"answer": "局部修改", "replacement": "Fixed expiry"})
    monkeypatch.setattr("zf.web.headless_agent.KanbanHeadlessAgent", lambda **_: agent)
    draft = client.post(
        url,
        json={
            **binding,
            "field": "requirement.objective",
            "message": "Adjust",
            "mode": "revise",
        },
        headers=headers,
    )
    assert draft.status_code == 200, draft.text
    applied = client.post(
        url,
        json={"operation": "apply", "draft_ref": draft.json()["draft_ref"]},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["revision"] == binding["revision"] + 1


def test_apply_can_resume_after_compiler_interruption(setup, monkeypatch):
    _, service, binding = setup
    draft = revision(service, binding)
    import zf.runtime.workflow_preview_review as review

    original = review.build_workflow_proposal

    def interrupted(*args, **kwargs):
        raise OSError("injected compiler interruption")

    monkeypatch.setattr(review, "build_workflow_proposal", interrupted)
    with pytest.raises(OSError, match="interruption"):
        service.apply(draft["draft_ref"])
    assert (
        load_workflow_request(service.state_dir, binding["request_id"])["revision"]
        == binding["revision"] + 1
    )
    monkeypatch.setattr(review, "build_workflow_proposal", original)
    result = service.apply(draft["draft_ref"])
    assert result["revision"] == binding["revision"] + 1
    assert not result["blockers"]


def test_config_drift_and_invalid_config_do_not_advance_revision(setup):
    _, service, binding = setup
    with pytest.raises(ValueError):
        service.discuss(
            {
                **binding,
                "field": "config.0.lanes",
                "mode": "revise",
                "message": "Change lanes",
            },
            agent=Agent({"answer": "bad config", "replacement": "not-an-integer"}),
        )
    draft = revision(service, binding)
    service.config_path.write_text(service.config_path.read_text() + "\n# changed\n")
    with pytest.raises(PreviewReviewError, match="Config changed"):
        service.apply(draft["draft_ref"])
    assert (
        load_workflow_request(service.state_dir, binding["request_id"])["revision"]
        == binding["revision"]
    )


def test_channel_bound_requirement_is_discussable_but_not_editable(setup):
    _, service, _ = setup
    intake = build_flow_intake(
        kind="issue",
        objective="Channel-owned requirement",
        request_id="channel-review",
        backend="mock",
        project_id="default",
        channel_id="channel-1",
        thread_id="main",
        acceptance=("Deliver all requirements",),
        output=service.project_root / "docs/intake/channel.md",
    )
    revise_workflow_request(
        service.state_dir,
        Path(intake["workflow_input_manifest_ref"]),
        actor="test",
        confirm=True,
        writer=service.writer,
    )
    build_flow_submit_preview(
        config_path=service.config_path,
        intake_path=Path(intake["intake_ref"]),
        flow_kind="issue",
        allow_missing_env=True,
    )
    req = load_workflow_request(service.state_dir, "channel-review")
    binding = {
        "request_id": req["request_id"],
        "revision": req["revision"],
        "proposal_digest": req["proposal_digest"],
    }
    assert (
        service.context(binding)["fields"]["requirement.objective"]["editable"] is False
    )
    with pytest.raises(PreviewReviewError, match="规范需求"):
        revision(service, binding, field="requirement.objective")
    assert service.discuss(
        {**binding, "field": "requirement.objective", "message": "Why?"},
        agent=Agent({"answer": "原始完整需求"}),
    )["ok"]


def test_old_approval_is_stale_after_review_revision(setup):
    client, service, binding = setup
    original = service.context(binding)
    draft = revision(service, binding)
    service.apply(draft["draft_ref"])
    response = client.post(
        "/api/projects/default/workflow-submit",
        headers={"x-zf-web-token": "test-token"},
        json={
            "apply": True,
            "request_id": binding["request_id"],
            "proposal_ref": original["request"]["proposal_ref"],
            "proposal_digest": binding["proposal_digest"],
            "intake_ref": original["manifest"]["intake_ref"],
            "request_revision": binding["revision"],
            "approval_ref": "old-preview",
            "allow_missing_env": True,
        },
    )
    assert response.status_code == 409, response.text
    assert not any(
        e.type == "workflow.invoke.requested"
        for e in service.writer.event_log.read_all()
    )


def test_revised_preview_can_be_approved_through_existing_submit(setup):
    client, service, binding = setup
    result = service.apply(revision(service, binding)["draft_ref"])
    detail = client.get(
        f"/api/projects/default/workflow-requests/{binding['request_id']}"
    ).json()
    response = client.post(
        "/api/projects/default/workflow-submit",
        headers={"x-zf-web-token": "test-token"},
        json={
            "apply": True,
            "request_id": binding["request_id"],
            "proposal_ref": result["proposal_ref"],
            "proposal_digest": result["proposal_digest"],
            "intake_ref": detail["links"]["intake_ref"],
            "kind": "issue",
            "allow_missing_env": True,
        },
    )
    assert response.status_code < 300, response.text
    assert response.json()["ok"], response.text
    assert any(
        e.type == "workflow.invoke.requested"
        for e in service.writer.event_log.read_all()
    )


def test_registered_research_revision_preserves_synthesis_contract(tmp_path):
    from tests.test_web_workflow_proposals import (
        _write_generic_base_config,
        _generic_synthesis_candidate,
    )
    from zf.core.config.loader import load_config
    from zf.runtime.workflow_synthesis import run_workflow_synthesis
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    config_path = tmp_path / "zf.yaml"
    _write_generic_base_config(config_path)
    state = tmp_path / ".zf"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    intake = build_flow_intake(
        kind="workflow",
        objective="Research a report",
        request_id="research-review",
        backend="mock",
        project_id="default",
        acceptance=("The report answers the question.",),
        output=tmp_path / "docs/intake/research.md",
    )
    req = revise_workflow_request(
        state,
        Path(intake["workflow_input_manifest_ref"]),
        actor="test",
        confirm=True,
        writer=writer,
    )
    synthesis = run_workflow_synthesis(
        state_dir=state,
        project_root=tmp_path,
        config=load_config(config_path),
        writer=writer,
        request_id=req["request_id"],
        actor="test",
        candidate_result=_generic_synthesis_candidate(req),
    )
    build_flow_submit_preview(
        config_path=config_path,
        intake_path=Path(intake["intake_ref"]),
        flow_kind="workflow",
        allow_missing_env=True,
        synthesis_result_ref=synthesis.result_ref,
    )
    req = load_workflow_request(state, req["request_id"])
    binding = {
        "request_id": req["request_id"],
        "revision": req["revision"],
        "proposal_digest": req["proposal_digest"],
    }
    service = WorkflowPreviewReview(state, tmp_path, writer)
    draft = service.discuss(
        {
            **binding,
            "field": "requirement.acceptance.0",
            "message": "Clarify output",
            "mode": "revise",
        },
        agent=Agent(
            {
                "answer": "细化报告",
                "replacement": "The report answers the question with cited evidence.",
            }
        ),
    )
    result = service.apply(draft["draft_ref"])
    assert not result["blockers"], result["blockers"]
    after = load_workflow_request(state, req["request_id"])
    admitted = hydrate_sidecar_ref(state, after["synthesis_ref"]).payload
    assert admitted["requested_roles"] == synthesis.result["requested_roles"]
    assert admitted["completion_profile"] == synthesis.result["completion_profile"]


def test_quote_offsets_disambiguate_repeated_unicode_text():
    from zf.runtime.workflow_preview_review import _quote_range

    assert _quote_range("🙂 test test", {"text": "test", "start": 7, "end": 11}) == (
        7,
        11,
    )
    with pytest.raises(PreviewReviewError):
        _quote_range("🙂 test test", {"text": "test"})
    with pytest.raises(PreviewReviewError):
        _quote_range("🙂 test test", {"text": "test", "start": 8, "end": 12})


def test_invalid_candidate_returns_422_without_mutation(setup, monkeypatch):
    client, service, binding = setup
    agent = Agent({"answer": "invalid topology", "replacement": "unknown"})
    monkeypatch.setattr("zf.web.headless_agent.KanbanHeadlessAgent", lambda **_: agent)
    result = client.post(
        "/api/projects/default/workflow-preview-review",
        headers={"x-zf-web-token": "test-token"},
        json={
            **binding,
            "field": "config.0.topology",
            "mode": "revise",
            "message": "Change the topology",
        },
    )
    assert result.status_code == 422, result.text
    assert (
        load_workflow_request(service.state_dir, binding["request_id"])["revision"]
        == binding["revision"]
    )
    assert not list(service.project_root.glob(".zf-preview-*.yaml"))
