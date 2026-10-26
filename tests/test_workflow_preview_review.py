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


@pytest.mark.parametrize("field,replacement", [
    ("config.0.roleDefaults.model", "test-model"),
    ("config.1.workflow.plan_approval", True),
    ("config.0.roleSkillBundles.impl", ["zf-yoke-dev-worker-role-context", "zf-plan-task-map-contract"]),
])
def test_nested_model_and_skills_recompile_exact_selected_input(setup, field, replacement):
    import yaml
    from zf.runtime.workflow_config_apply import WorkflowConfigApplyService
    from zf.runtime.workflow_proposal import load_workflow_proposal

    _, service, binding = setup
    baseline = service.config_path.read_bytes()
    context = service.context(binding)
    assert context["fields"][field]["editable"]
    draft = service.discuss(
        {**binding, "field": field, "message": "仅修改所选配置", "mode": "revise"},
        agent=Agent({"answer": "修改所选字段", "replacement": replacement}),
    )
    applied = service.apply(draft["draft_ref"])
    proposal = load_workflow_proposal(service.state_dir, applied["proposal_ref"])
    candidate = WorkflowConfigApplyService(
        state_dir=service.state_dir, project_root=service.project_root,
    )._private_candidate(proposal)
    before = list(yaml.safe_load_all(context["config_text"]))
    after = list(yaml.safe_load_all(candidate.read_text()))
    _, index, parent, key = field.split(".")
    expected = deepcopy(before)
    expected[int(index)]["spec"].setdefault(parent, {})[key] = replacement
    assert after == expected
    assert service.config_path.read_bytes() == baseline
    assert not applied["started"]


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


@pytest.mark.parametrize("kind", ["issue", "auto"])
def test_synthesis_preview_lane_reduction_rebinds_compiled_roles(tmp_path, kind):
    from tests.test_web_workflow_proposals import _write_config
    from tests.test_workflow_synthesis import _result
    from zf.core.config.loader import load_config
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_synthesis import run_workflow_synthesis
    from zf.runtime.workflow_synthesis_support import admission_catalog

    path = tmp_path / 'zf.yaml'
    _write_config(path, lanes=2)
    path.write_text(path.read_text().replace("  lanes: 2", "  topology: fanout\n  lanes: 2") + "  workflow:\n    candidate_quality_source: task_contract_required\n")
    state = tmp_path / '.zf'
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    intake = build_flow_intake(kind=kind, objective='Fix expiry', request_id='lane-review',
        backend='mock', project_id='default', acceptance=('Expiry regression passes',),
        output=tmp_path / 'docs/intake/bug.md')
    req = revise_workflow_request(state, Path(intake['workflow_input_manifest_ref']),
        actor='test', confirm=True, writer=writer)
    config = load_config(path)
    catalog = admission_catalog(config, tmp_path)
    candidate = _result(req, 'IssueFlow', requested_roles=sorted(catalog['roles']),
        requested_skills=[], requested_profiles=[],
        completion_profile=catalog['configured_completion_by_family']['issue'])
    candidate['short_flow_spec']['parameters']['lanes'] = 2
    synth = run_workflow_synthesis(state_dir=state, project_root=tmp_path, config=config,
        writer=writer, request_id=req['request_id'], actor='test', candidate_result=candidate)
    report = build_flow_submit_preview(config_path=path, intake_path=Path(intake['intake_ref']),
        flow_kind='issue', allow_missing_env=True, synthesis_result_ref=synth.result_ref)
    assert report.get('status') != 'STOP', report.get('blockers')
    req = load_workflow_request(state, req['request_id'])
    service = WorkflowPreviewReview(state, tmp_path, writer)
    binding = {key: req[key] for key in ('request_id', 'revision', 'proposal_digest')}
    draft = service.discuss({**binding, 'field': 'config.0.lanes', 'mode': 'revise', 'message': 'One lane'},
        agent=Agent({'answer': '仅修改并行数', 'replacement': 1}))
    applied = service.apply(draft['draft_ref'])
    assert not applied['blockers'], applied['blockers']
    latest = load_workflow_request(state, req['request_id'])
    admitted = hydrate_sidecar_ref(state, latest['synthesis_ref']).payload
    assert not any('lane-1' in role for role in admitted['requested_roles'])
    assert any('lane-0' in role for role in admitted['requested_roles'])
    assert latest['status'] == 'proposed'
    assert any('lane-1' in role.name for role in load_config(path).roles)


def test_preview_apply_holds_preparation_consumer_lease(setup, monkeypatch):
    from zf.core.config.loader import load_config
    from zf.runtime import workflow_preview_review as module
    from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations
    _, service, binding = setup
    change = service.discuss({**binding, 'field': 'config.0.lanes', 'mode': 'revise', 'message': 'Two lanes'},
        agent=Agent({'answer': '两条 lane', 'replacement': 2}))
    original = module.build_workflow_proposal
    observed = []

    def compile_during_consumer_tick(*args, **kwargs):
        with pytest.raises(TimeoutError):
            consume_workflow_synthesis_operations(state_dir=service.state_dir,
                project_root=service.project_root, config=load_config(service.config_path), writer=service.writer)
        observed.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'build_workflow_proposal', compile_during_consumer_tick)
    assert service.apply(change['draft_ref'])['ok']
    assert observed == [True]


def test_multi_flow_skill_fields_use_compiler_input_roles(tmp_path):
    import yaml
    from tests.test_web_workflow_proposals import _write_config
    from zf.core.config.loader import load_config
    from zf.runtime.workflow_review_fields import flow_input_roles, config_review_fields, apply_config_field
    path = tmp_path / 'zf.yaml'
    _write_config(path)
    docs = list(yaml.safe_load_all(path.read_text()))
    docs.insert(1, {'apiVersion': 'zaofu.dev/v1', 'kind': 'PrdFlow',
        'metadata': {'name': 'product'}, 'spec': {'lanes': 1, 'backend': 'mock'}})
    inputs = flow_input_roles(docs, 1, 'prd')
    fields = config_review_fields(1, docs[1]['spec'], role_names=list(inputs), family='prd', inherited_skills=inputs)
    assert 'config.1.roleSkillBundles.planner' in fields
    assert 'config.1.roleSkillBundles.prd-planner' not in fields
    patched = apply_config_field(docs, 'config.1.roleSkillBundles.planner', ['zf-plan-task-map-contract'])
    path.write_text(yaml.safe_dump_all(patched))
    planner = next(role for role in load_config(path).roles if role.name == 'prd-planner')
    assert planner.skills == ['zf-plan-task-map-contract']


@pytest.mark.parametrize('topology,bundle', [('light', 'impl'), ('fanout', 'fix')])
def test_issue_skill_input_matches_selected_topology(tmp_path, topology, bundle):
    import yaml
    from tests.test_web_workflow_proposals import _write_config
    from zf.core.config.loader import load_config
    from zf.runtime.workflow_review_fields import flow_input_roles, config_review_fields, apply_config_field
    path = tmp_path / 'zf.yaml'
    _write_config(path)
    docs = list(yaml.safe_load_all(path.read_text()))
    docs[0]['spec']['topology'] = topology
    inputs = flow_input_roles(docs, 0, 'issue')
    fields = config_review_fields(0, docs[0]['spec'], role_names=list(inputs), family='issue', inherited_skills=inputs)
    field = f'config.0.roleSkillBundles.{bundle}'
    assert field in fields
    path.write_text(yaml.safe_dump_all(apply_config_field(docs, field, ['zf-plan-task-map-contract'])))
    assert any(role.skills == ['zf-plan-task-map-contract'] and 'fix' in role.name for role in load_config(path).roles)
