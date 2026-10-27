"""Three intake surfaces share preparation, answers, and exact YAML review."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_control_actions_workflow_request import _service, _execute
from tests.test_web_workflow_proposals import _client, _write_config
from tests.e2e.test_intake_semantic_handoff_mock_e2e import candidate
from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.task.store import TaskStore
from zf.runtime.workflow_requests import (
    load_workflow_request,
    hydrate_workflow_requirement,
)
from zf.runtime.workflow_synthesis import run_workflow_synthesis
from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations


QUESTION = "Which session behavior should be preserved?"


def _provider(monkeypatch, *, ask=True):
    calls = []

    def synthesize(**kwargs):
        current = load_workflow_request(kwargs["state_dir"], kwargs["request_id"])
        requirement = hydrate_workflow_requirement(kwargs["state_dir"], current)
        calls.append(requirement)
        result = candidate(current)
        if ask and not requirement.get("clarification_answers"):
            result["open_questions"] = [QUESTION]
        return run_workflow_synthesis(**{**kwargs, "candidate_result": result})

    monkeypatch.setattr(
        "zf.runtime.workflow_synthesis.run_workflow_synthesis", synthesize
    )
    return calls


def _consume(service):
    return consume_workflow_synthesis_operations(
        state_dir=service.state_dir,
        project_root=service.project_root,
        config=service.config,
        writer=service.writer,
        limit=10,
    )


def _create_payload():
    return {
        "title": "Keep active sessions valid",
        "execution_mode": "workflow",
        "prepare_workflow": True,
        "backend": "mock",
        "contract": {
            "behavior": "Keep active sessions valid",
            "verification": "python -m pytest -q",
            "acceptance_criteria": ["Active sessions remain valid"],
        },
    }


def test_kanban_create_prepares_without_start_or_config_write(tmp_path, monkeypatch):
    service, log = _service(tmp_path)
    calls = _provider(monkeypatch)
    before = (tmp_path / "zf.yaml").read_bytes()
    result = _execute(service, "create-task", _create_payload())
    assert result["ok"], result
    preparation = result["workflow_preparation"]
    assert preparation["status"] == "synthesis_queued", preparation
    assert _consume(service) == 1
    request = load_workflow_request(service.state_dir, preparation["request_id"])
    assert request["status"] == "clarifying"
    assert request["open_questions"] == [QUESTION]
    assert calls[0]["acceptance"] == ["Active sessions remain valid"]
    assert (tmp_path / "zf.yaml").read_bytes() == before
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())


def test_tracking_task_does_not_queue_synthesis(tmp_path):
    service, log = _service(tmp_path)
    payload = _create_payload()
    payload.pop("prepare_workflow")
    assert _execute(service, "create-task", payload)["ok"]
    assert not any(e.type == "workflow.synthesis.queued" for e in log.read_all())


@pytest.mark.parametrize("ask", [False, True])
def test_settled_request_does_not_block_next_preparation(tmp_path, monkeypatch, ask):
    service, _log = _service(tmp_path)
    calls = _provider(monkeypatch, ask=ask)
    for objective in ("Repair first session issue", "Repair second session issue"):
        prepared = _execute(service, "idea-to-product", {
            "objective": objective, "backend": "mock",
        })
        assert consume_workflow_synthesis_operations(
            state_dir=service.state_dir,
            project_root=service.project_root,
            config=service.config,
            writer=service.writer,
            limit=1,
        ) == 1
        current = load_workflow_request(service.state_dir, prepared["request_id"])
        assert current["status"] == ("clarifying" if ask else "proposed")
    assert len(calls) == 2


def test_channel_task_prepares_with_canonical_prd(tmp_path, monkeypatch):
    from tests.test_channel_prd_task_contract import (
        _ready_prd_fixture,
        _task_payload,
        _execute as channel_execute,
    )

    state, writer, service, authority = _ready_prd_fixture(
        tmp_path, implementation_start=False
    )
    _write_config(tmp_path / "zf.yaml")
    service.config = load_config(tmp_path / "zf.yaml")
    calls = _provider(monkeypatch)
    payload = {**_task_payload(authority), "prepare_workflow": True, "backend": "mock"}
    result = channel_execute(service, writer, "create-task", payload)
    assert result["ok"], result
    prepared = result["workflow_preparation"]
    assert prepared["status"] == "synthesis_queued", prepared
    _consume(service)
    request = load_workflow_request(state, prepared["request_id"])
    assert request["channel_id"] == authority["channel_id"]
    assert request["status"] == "clarifying"
    assert calls[0]["acceptance"]
    task = TaskStore(state / "kanban.json").get(result["task_id"])
    assert task.contract.evidence_contract["implementation_start"] is False
    assert not any(
        e.type == "workflow.invoke.requested" for e in writer.event_log.read_all()
    )


def test_cli_questions_answers_and_full_yaml_preview(tmp_path, monkeypatch, capsys):
    service, log = _service(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = _provider(monkeypatch)
    payload = _create_payload()
    payload.pop("prepare_workflow")
    created = _execute(service, "create-task", payload)
    assert (
        main(
            [
                "workflow",
                "prepare",
                "--task",
                created["task_id"],
                "--backend",
                "mock",
                "--allow-missing-env",
                "--format",
                "json",
            ]
        )
        == 0
    )
    initial = json.loads(capsys.readouterr().out)
    current = initial["request"]
    assert current["open_questions"] == [QUESTION]
    assert (
        main(
            [
                "workflow",
                "prepare",
                "--request",
                initial["request_id"],
                "--revision",
                str(current["revision"]),
                "--answer",
                QUESTION + "=Keep active sessions",
                "--allow-missing-env",
                "--format",
                "json",
            ]
        )
        == 0
    )
    answered = json.loads(capsys.readouterr().out)
    assert answered["status"] == "proposed", answered
    assert len(calls) == 2
    assert calls[1]["clarification_answers"] == [
        {"question": QUESTION, "answer": "Keep active sessions"}
    ]
    assert (
        main(
            [
                "workflow",
                "preview",
                "--request",
                initial["request_id"],
                "--format",
                "yaml",
            ]
        )
        == 0
    )
    assert "kind: IssueFlow" in capsys.readouterr().out
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())


def test_web_answers_requeue_and_preview_current_revision(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    calls = _provider(monkeypatch)
    headers = {"X-ZF-Web-Token": "test-token"}
    queued = client.post(
        "/api/projects/default/actions/workflow-request",
        headers=headers,
        json={
            "project_id": "default",
            "idempotency_key": "prepare-test",
            "actor": "web",
            "payload": {
                "kind": "auto",
                "objective": "Repair login",
                "backend": "mock",
                "allow_missing_env": True,
            },
        },
    )
    assert queued.status_code == 202, queued.text
    result = queued.json()
    # Consumer is the same one used by Web's startup worker, exercised explicitly here.
    from zf.core.events.factory import event_log_from_project
    from zf.core.events.writer import EventWriter

    cfg = load_config(tmp_path / "zf.yaml")
    writer = EventWriter(event_log_from_project(state, config=cfg))
    service = SimpleNamespace(
        state_dir=state, project_root=tmp_path, config=cfg, writer=writer
    )
    _consume(service)
    rid = result["request_id"]
    current = load_workflow_request(state, rid)
    answer = client.post(
        "/api/projects/default/workflow-clarify",
        headers=headers,
        json={
            "request_id": rid,
            "intake_ref": result["intake_ref"],
            "expected_revision": current["revision"],
            "expected_requirement_digest": current["requirement_spec_digest"],
            "clarification_answers": [
                {"question": QUESTION, "answer": "Keep active sessions"}
            ],
            "open_questions": [],
            "confirm": True,
        },
    )
    assert answer.status_code == 200, answer.text
    prepared = client.post(
        "/api/projects/default/workflow-submit",
        headers=headers,
        json={
            "request_id": rid,
            "intake_ref": result["intake_ref"],
            "allow_missing_env": True,
        },
    )
    assert prepared.status_code == 202, prepared.text
    _consume(service)
    detail = client.get(f"/api/projects/default/workflow-requests/{rid}").json()
    assert detail["result"]["status"] == "proposed", detail
    assert detail["proposal"]["request_revision"] == current["revision"] + 1
    assert "kind: IssueFlow" in detail["artifacts"]["effective_config"]["yaml"]
    assert len(calls) == 2
    assert not any(
        e.type == "workflow.invoke.requested" for e in writer.event_log.read_all()
    )


def test_prepared_task_submit_keeps_identity_and_rejects_stale_task(
    tmp_path, monkeypatch
):
    service, log = _service(tmp_path)
    _provider(monkeypatch, ask=False)
    created = _execute(service, "create-task", _create_payload())
    prepared = created["workflow_preparation"]
    _consume(service)
    request = load_workflow_request(service.state_dir, prepared["request_id"])
    assert request["status"] == "proposed", request
    submit = {
        "request_id": request["request_id"],
        "intake_ref": prepared["intake_ref"],
        "proposal_ref": request["proposal_ref"],
        "proposal_digest": request["proposal_digest"],
        "kind": "auto",
        "allow_missing_env": True,
    }
    from zf.runtime.workflow_delivery import build_flow_submit_preview

    wrong = build_flow_submit_preview(
        config_path=tmp_path / "zf.yaml",
        intake_path=Path(prepared["intake_ref"]),
        task_id="TASK-OTHER",
        allow_missing_env=True,
    )
    assert wrong["status"] == "STOP"
    stale = _execute(service, "workflow-submit", {**submit, "task_id": "TASK-OTHER"})
    assert not stale["ok"]
    # This test exercises actual intake/proposal/invoke, with only runtime startup stubbed.
    monkeypatch.setattr(
        "zf.runtime.workflow_delivery.ensure_submitted_workflow_runtime",
        lambda value, **kw: value,
    )
    accepted = _execute(service, "workflow-submit", submit)
    assert accepted["ok"], accepted
    invokes = [e for e in log.read_all() if e.type == "workflow.invoke.requested"]
    assert len(invokes) == 1
    assert invokes[0].task_id == created["task_id"]


def test_yaml_snapshot_redacts_nested_keys(tmp_path, monkeypatch):
    service, _ = _service(tmp_path)
    path = tmp_path / "zf.yaml"
    path.write_text(
        path.read_text().replace(
            "metadata: {name: issue-demo}",
            "metadata: {name: issue-demo, api_key: fake-private-value}",
        )
    )
    service.config = load_config(path)
    _provider(monkeypatch, ask=False)
    queued = _execute(
        service,
        "workflow-request",
        {
            "kind": "auto",
            "objective": "Fix login",
            "backend": "mock",
            "allow_missing_env": True,
        },
    )
    _consume(service)
    from zf.cli.workflow_prepare import request_preview

    preview = request_preview(service.state_dir, queued["request_id"])
    text = preview["artifacts"]["effective_config"]["yaml"]
    assert "fake-private-value" not in text
    assert "REDACTED" in text


def test_channel_prepared_submit_requires_owner_before_binding_task(
    tmp_path, monkeypatch
):
    from tests.test_channel_prd_task_contract import (
        _ready_prd_fixture,
        _task_payload,
        _execute as channel_execute,
    )
    from zf.runtime.workflow_anchor import workflow_task_request_binding

    state, writer, service, authority = _ready_prd_fixture(
        tmp_path, implementation_start=False
    )
    _write_config(tmp_path / "zf.yaml")
    service.config = load_config(tmp_path / "zf.yaml")
    _provider(monkeypatch, ask=False)
    created = channel_execute(
        service,
        writer,
        "create-task",
        {
            **_task_payload(authority),
            "prepare_workflow": True,
            "backend": "mock",
        },
    )
    preparation = created["workflow_preparation"]
    _consume(service)
    request = load_workflow_request(state, preparation["request_id"])
    assert request["status"] == "proposed", request
    submit = {
        "request_id": request["request_id"],
        "intake_ref": preparation["intake_ref"],
        "proposal_ref": request["proposal_ref"],
        "proposal_digest": request["proposal_digest"],
        "kind": "auto",
        "allow_missing_env": True,
    }
    store = TaskStore(state / "kanban.json")
    before = workflow_task_request_binding(store.get(created["task_id"]))
    service.actor = "unrelated-member"
    rejected = channel_execute(service, writer, "workflow-submit", submit)
    assert not rejected["ok"], rejected
    assert workflow_task_request_binding(store.get(created["task_id"])) == before
    service.actor = "web"
    monkeypatch.setattr(
        "zf.runtime.workflow_delivery.ensure_submitted_workflow_runtime",
        lambda value, **kw: value,
    )
    accepted = channel_execute(service, writer, "workflow-submit", submit)
    assert accepted["ok"], accepted
    assert (
        store.get(created["task_id"]).contract.evidence_contract["implementation_start"]
        is False
    )
    assert (
        len(
            [
                e
                for e in writer.event_log.read_all()
                if e.type == "workflow.invoke.requested"
            ]
        )
        == 1
    )


def test_kanban_preparation_keeps_explicit_prd_source_and_target(tmp_path):
    service, _log = _service(tmp_path)
    source = tmp_path / 'docs' / 'prd' / 'handoff.md'
    source.parent.mkdir(parents=True)
    source.write_text('Product requirements', encoding='utf-8')
    payload = _create_payload()
    payload['target_root'] = 'app'
    payload['contract']['source_ref'] = 'docs/prd/handoff.md'
    payload['contract']['behavior'] = 'Deliver PRD v4 with 8 independent impl and verify lanes'
    result = _execute(service, 'create-task', payload)
    assert result['ok'], result
    request = load_workflow_request(service.state_dir, result['workflow_preparation']['request_id'])
    requirement = hydrate_workflow_requirement(service.state_dir, request)
    assert requirement['target_root'] == 'app'
    assert requirement['source_ref'] == str(source)
    assert '8 independent impl and verify lanes' in requirement['objective']


def test_kanban_prd_v4_request_produces_bound_yaml_candidate(tmp_path, monkeypatch):
    from tests.test_workflow_proposal import _synthesis_result
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    service, log = _service(tmp_path)
    config_file = tmp_path / 'zf.yaml'
    config_file.write_text(config_file.read_text().replace('IssueFlow', 'PrdFlow')
                           .replace('issueRef', 'prdRef').replace('backend: mock', 'backend: codex')
                           .replace('  lanes: 1', '  deliveryPolicy: report_only\n  lanes: 1', 1)
                           .replace('  project: {name: demo, state_dir: .zf}',
                                    '  project: {name: demo, state_dir: .zf}\n  workflow: {candidate_quality_source: task_contract_required}'))
    service.config = load_config(config_file)
    original = config_file.read_bytes()
    source = tmp_path / 'docs' / 'prd' / 'handoff.md'
    source.parent.mkdir(parents=True)
    source.write_text('Product requirements', encoding='utf-8')
    payload = _create_payload()
    payload['target_root'] = 'app'
    payload['contract']['source_ref'] = 'docs/prd/handoff.md'
    payload['contract']['behavior'] = 'Deliver PRD v4 with 8 independent impl and verify lanes'
    payload['backend'] = 'mock'

    def synthesize(**kwargs):
        if kwargs.get('candidate_result') is not None:
            return run_workflow_synthesis(**kwargs)
        request = load_workflow_request(kwargs['state_dir'], kwargs['request_id'])
        result = _synthesis_result(request, lanes=8)
        result['selected_flow_family'] = 'PrdFlow'
        result['short_flow_spec']['flow_family'] = 'PrdFlow'
        result['short_flow_spec']['parameters']['task_pipeline'] = {
            'profile': 'prd-flow-v4-task-pipeline', 'mode': 'blocking',
            'impl_capacity': 8, 'verify_capacity': 8, 'max_active_task_pipelines': 8,
        }
        return run_workflow_synthesis(**{**kwargs, 'candidate_result': result})

    monkeypatch.setattr('zf.runtime.workflow_synthesis.run_workflow_synthesis', synthesize)
    created = _execute(service, 'create-task', payload)
    assert created['ok'], created
    prepared = created['workflow_preparation']
    assert prepared['status'] == 'synthesis_queued', prepared
    assert _consume(service) == 1
    request = load_workflow_request(service.state_dir, prepared['request_id'])
    assert request['status'] == 'proposed', request
    proposal = hydrate_sidecar_ref(service.state_dir, request['proposal_ref']).payload
    snapshot = hydrate_sidecar_ref(service.state_dir, proposal['effective_config_ref']).payload
    assert 'prd-flow-v4-task-pipeline' in snapshot['yaml']
    assert 'capacity: 8' in snapshot['yaml']
    assert proposal['change_mode'] == 'config_change'
    assert config_file.read_bytes() == original
    assert not any(e.type in {'workflow.config.change.applied', 'workflow.invoke.requested'} for e in log.read_all())

    # A reviewed pool revision must update the synthesis binding, not silently
    # retain the old capacities or apply the candidate to canonical config.
    import yaml
    from tests.test_workflow_preview_review import Agent
    from zf.runtime.workflow_preview_review import WorkflowPreviewReview

    service_review = WorkflowPreviewReview(service.state_dir, tmp_path, service.writer)
    binding = {key: request[key] for key in ('request_id', 'revision', 'proposal_digest')}
    docs = list(yaml.safe_load_all(snapshot['yaml']))
    pipeline = docs[0]['spec']['taskPipeline']
    pipeline['pools']['verify']['capacity'] = 4
    draft = service_review.discuss({**binding, 'mode': 'chat', 'message': 'Set verify capacity to 4'}, agent=Agent({
        'answer': 'Review the updated verify capacity',
        'changes': [{'field': 'config.0.taskPipeline', 'replacement': pipeline}],
    }))
    revised = service_review.apply(draft['draft_ref'])
    revised_proposal = hydrate_sidecar_ref(service.state_dir, revised['proposal_ref']).payload
    assert revised_proposal['approval_status'] == 'approvable', revised_proposal['blockers']
    assert config_file.read_bytes() == original
