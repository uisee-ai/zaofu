from dataclasses import replace, asdict
from datetime import datetime, timezone
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_external_issue_ingress import _config, _mirror, _Reconciler
from tests.test_settings_integration import setup, USER
from zf.core.config.loader import load_config, ConfigError
from zf.core.config.project_context import ProjectContext
from zf.core.config.self_issue_policy import inherit_workspace_self_issue_config, inject_self_issue_policy
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.self_issue.models import PublicationIntent, stable_digest
from zf.core.self_issue.store import PublicationIntentStore
from zf.core.workspace.registry import WorkspaceRegistry
from zf.runtime.external_issue_ingress import ExternalIssueIngressService
from zf.runtime.issue_execution import IssueExecutionRoutes
from zf.runtime.issue_execution_settings import execution_settings, save_execution_settings
from zf.runtime.issue_candidate_delivery import IssueCandidateDeliveryService
from zf.web.issue_triage_routes import build_issue_triage_router


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv('ZF_WORKSPACE_HOME', str(tmp_path / 'workspace'))
    monkeypatch.setattr('zf.web.issue_triage_routes.build_github_lifecycle_label_writer', lambda: None)


def project(tmp_path, name, *, execution='', source='/report-only'):
    root = tmp_path / name
    root.mkdir()
    config = _config(auto_triage_new_only=False)
    config.project = replace(config.project, name=name, state_dir='runtime/state')
    config.self_issue = replace(config.self_issue, source_mode='explicit_target', source_root=source, execution_project_root=execution)
    # Use the canonical generator for a real executable Triage workflow.
    docs = inject_self_issue_policy([{'apiVersion': 'zaofu.dev/v1', 'kind': 'ZfConfig', 'spec': {'project': {'name': name, 'state_dir': 'runtime/state'},
        'workflow': {'kind_routes': {'issue': {'pattern_id': 'issue-lanes-impl'}}},
        'self_issue': {'source_mode': 'explicit_target', 'source_root': source, 'execution_project_root': execution}}}], config.self_issue)
    docs.insert(1, {'apiVersion': 'zaofu.dev/v1', 'kind': 'IssueFlow', 'metadata': {'name': name+'-issue'},
        'spec': {'topology': 'light', 'lanes': 1, 'backend': 'codex', 'issueRef': 'docs/issue.md', 'targetRoot': '.',
                 'qualityFloor': 'issue-regression', 'evidencePolicy': 'strict_refs', 'deliveryPolicy': 'ship_candidate'}})
    (root / 'zf.yaml').write_text(yaml.safe_dump_all(docs, allow_unicode=True))
    for args in [('init', '-q'), ('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '--allow-empty', '-qm', 'initial')]:
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    config = load_config(root / 'zf.yaml')
    ctx = ProjectContext(root, root / 'zf.yaml', config, root / 'runtime/state')
    WorkspaceRegistry().upsert_context(ctx)
    return ctx


def service(ctx):
    return ExternalIssueIngressService(ctx.state_dir, ctx.config, project_root=ctx.project_root,
        reconciler=_Reconciler(), now=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc))


def mirror(ctx, *, body='report'):
    item, _ = IssueMirrorStore(ctx.state_dir).upsert(_mirror(created_at='2026-09-01T00:00:00+00:00'), body)
    return item


def publication(ctx, item, *, pending=False):
    payload = {'title': 'report', 'body': 'body'}
    intent = PublicationIntent(intent_id='pub-test', draft_id='draft-test', draft_revision=1,
        payload=payload, payload_digest=stable_digest(payload), redaction_digest='', disclosure_digest='',
        target_binding={'provider': 'github', 'project': item.repository}, credential_subject='test',
        permission_snapshot={}, marker='local-marker', status='published',
        published_issue_ref={'provider': 'github', 'project': item.repository, 'number': str(item.number), 'url': item.html_url})
    if pending:
        intent.status = 'publishing'
        intent.published_issue_ref = {}
    PublicationIntentStore(ctx.state_dir / 'self-issues/publication-intents.json').save(intent)


def test_settings_default_custom_reset_and_source_independence(tmp_path):
    owner = project(tmp_path, 'owner')
    other = project(tmp_path, 'other')
    before = execution_settings(owner.project_root, owner.config)
    assert before['configured_root'] == '' and before['execution_project_root'] == str(owner.project_root)
    saved = save_execution_settings(owner.project_root, owner.config, {'execution_project_root': '../other', 'config_digest': before['config_digest']})
    assert saved['execution_project_root'] == str(other.project_root)
    current = load_config(owner.config_path)
    assert current.self_issue.source_root == '/report-only'
    assert current.self_issue.source_mode == 'explicit_target'
    assert inherit_workspace_self_issue_config(current, other.config).self_issue.execution_project_root == '../other'
    assert save_execution_settings(owner.project_root, current, {'execution_project_root': '', 'config_digest': before['config_digest']})['status'] == 'config_conflict'
    with pytest.raises(ValueError, match='zf.yaml'):
        save_execution_settings(owner.project_root, current, {'execution_project_root': '/not-a-project', 'config_digest': saved['config_digest']})
    reset = save_execution_settings(owner.project_root, current, {'execution_project_root': '', 'config_digest': saved['config_digest']})
    assert reset['execution_project_root'] == str(owner.project_root)
    docs = list(yaml.safe_load_all(owner.config_path.read_text()))
    next(d for d in docs if d['kind'] == 'ZfConfig')['spec']['self_issue']['execution_project_root'] = []
    owner.config_path.write_text(yaml.safe_dump_all(docs))
    with pytest.raises(ConfigError, match='execution_project_root'):
        load_config(owner.config_path)


def test_developer_api_scope_auth_cas_and_source_preservation(tmp_path, monkeypatch):
    root, config, client = setup(tmp_path, monkeypatch)
    target = project(tmp_path, 'developer')
    url = '/api/projects/test/settings/issue-execution'
    assert client.get(url, headers=USER).status_code == 403
    assert client.patch(url, headers=USER, json={}).status_code == 403
    client.post('/api/settings/access/unlock', json={'token': 'actual-develop-token'})
    before = client.get(url).json()
    payload = {'execution_project_root': str(target.project_root), 'config_digest': before['config_digest']}
    assert client.patch(url, headers={'Origin': 'https://evil.invalid'}, json=payload).status_code == 403
    saved = client.patch(url, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['workflow_project_name'] == 'developer'
    assert load_config(root / 'zf.yaml').self_issue.source_mode == config.self_issue.source_mode
    assert client.patch(url, json=payload).status_code == 409
    bad = {**payload, 'config_digest': saved.json()['config_digest'], 'source_root': '/other'}
    assert client.patch(url, json=bad).status_code == 422


def test_manual_triage_routes_to_project_and_pins_even_after_setting_changes(tmp_path):
    developer = project(tmp_path, 'developer')
    reporter = project(tmp_path, 'reporter', execution=str(developer.project_root))
    item = mirror(reporter)
    host = service(reporter)
    result = host.start_manual_triage(item.number, refresh=False, defer_remote_sync=True, defer_attachments=True)
    assert result.ok, result
    assert result.execution_project_root == str(developer.project_root)
    assert not host.tasks.list_all()
    execution = service(developer)
    task = execution.tasks.get(result.task_id)
    assert task.contract.evidence_contract['source_code_root'] == str(developer.project_root)
    invoke = next(e for e in execution.writer.event_log.read_all() if e.type == 'workflow.invoke.requested')
    snapshot = json.loads((developer.state_dir / invoke.payload['run_contract_ref']).read_text())
    assert snapshot['contract']['project']['root'] == str(developer.project_root)
    assert snapshot['contract']['project']['state_dir'] == str(developer.state_dir)
    settings = execution_settings(reporter.project_root, reporter.config)
    save_execution_settings(reporter.project_root, reporter.config, {'execution_project_root': '', 'config_digest': settings['config_digest']})
    assert IssueExecutionRoutes(host).target(item, automatic=False).project_root == developer.project_root
    delivery = IssueCandidateDeliveryService(state_dir=developer.state_dir, project_root=developer.project_root, config=developer.config)
    assert delivery.project_root == developer.project_root


def test_parent_poller_routes_published_issue_to_reporting_project_default(tmp_path):
    parent = project(tmp_path, 'parent')
    reporter = project(tmp_path, 'reporter')
    item = mirror(parent)
    publication(reporter, item)
    parent_service = service(parent)
    result = parent_service.process_mirrors()
    assert result.ok and result.triage_queued == 1
    assert parent_service.tasks.list_all() == []
    target_service = service(reporter)
    task = target_service.tasks.list_all()[0]
    assert task.contract.evidence_contract['source_code_root'] == str(reporter.project_root)
    assert parent_service.process_mirrors().triage_queued == 0
    assert len(target_service.tasks.list_all()) == 1


def test_parent_poller_respects_reporters_explicit_developer_and_pending_publication(tmp_path):
    parent = project(tmp_path, 'parent')
    developer = project(tmp_path, 'developer')
    reporter = project(tmp_path, 'reporter', execution=str(developer.project_root))
    item = mirror(parent, body='report\n<!-- zf-self-issue:local-marker -->')
    host = service(parent)
    publication(reporter, item, pending=True)
    assert host.process_mirrors().triage_queued == 0
    publication(reporter, item)
    assert host.process_mirrors().triage_queued == 1
    assert service(reporter).tasks.list_all() == []
    assert service(developer).tasks.list_all()[0].contract.evidence_contract['source_code_root'] == str(developer.project_root)


def test_web_start_returns_actual_execution_owner_and_cancel_routes_back(tmp_path, monkeypatch):
    developer = project(tmp_path, 'developer')
    reporter = project(tmp_path, 'reporter', execution=str(developer.project_root))
    mirror(reporter)
    contexts = {'reporter': reporter, 'developer': developer}
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=contexts.__getitem__, project_ids=lambda: list(contexts),
        reconciler_factory=lambda *_: _Reconciler(), mutation_auth_error=lambda *a, **k: None))
    client = TestClient(app)
    result = client.post('/api/projects/reporter/issue-triage/start-triage', json={'issue': 8})
    assert result.status_code == 200, result.text
    assert result.json()['execution_project_root'] == str(developer.project_root)
    assert result.json()['issue']['workflow']['workflow_project_name'] == 'developer'
    projected = client.get('/api/projects/reporter/issue-triage/8').json()
    assert projected['issue']['workflow']['workflow_project_id'] == 'developer'
    cancelled = client.post('/api/projects/reporter/issue-triage/8/cancel-queued', json={})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['workflow_project_id'] == 'developer'


def test_core_orchestrator_ignores_user_report_source(tmp_path):
    from unittest.mock import MagicMock
    from zf.runtime.orchestrator import Orchestrator
    ctx = project(tmp_path, 'developer')
    ctx.state_dir.mkdir(parents=True)
    runtime = Orchestrator(ctx.state_dir, ctx.config, MagicMock(), project_root=ctx.project_root)
    assert runtime.project_root == ctx.project_root



def test_forwarded_triage_fix_proposal_uses_developer_checkout(tmp_path):
    from zf.runtime.task_contract_authority import TaskContractAuthorityService
    parent = project(tmp_path, 'parent')
    developer = project(tmp_path, 'developer')
    reporter = project(tmp_path, 'reporter', execution=str(developer.project_root))
    item = mirror(parent)
    publication(reporter, item)
    host = service(parent)
    assert host.process_mirrors().triage_queued == 1
    execution = service(developer)
    task = execution.tasks.list_all()[0]
    contract = asdict(task.contract)
    contract.update(schema_version='task-contract.v1', behavior='修复确定性回归', verification='python -m pytest tests/test_cli.py',
        verification_tiers=['static', 'runtime'], scope=['src/app.py'], acceptance_criteria=['复现先失败，修复后通过'],
        owner_role='fix-lane-0', spec_skip_reason='GitHub Issue 与分诊报告作为来源合同')
    contract['evidence_contract'].update(external_issue_triage_status='ready_to_fix', external_issue_revision=contract['source_revision'])
    TaskContractAuthorityService(task_store=execution.tasks, event_writer=execution.writer, state_dir=developer.state_dir).replace(
        task, contract=contract, source='external_issue_triage', actor='external-issue-triage')
    assert host.process_mirrors().ok
    proposals = [e for e in execution.writer.event_log.read_all() if e.type == 'operator.action.proposed']
    assert len(proposals) == 1
    parameters = proposals[0].payload['proposal']['payload']['parameters']
    assert parameters['source_root'] == str(developer.project_root)
    assert parameters['target_root'] == str(developer.project_root)
    assert host.tasks.list_all() == []


def test_concurrent_manual_admission_cannot_create_two_owners(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    a, b = project(tmp_path, 'a'), project(tmp_path, 'b')
    mirror(a); mirror(b)
    barrier = Barrier(2)
    def start(ctx):
        barrier.wait(timeout=5)
        return service(ctx).start_manual_triage(8, refresh=False, defer_remote_sync=True, defer_attachments=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, [a, b]))
    assert all(r.ok for r in results)
    assert sum(r.queued for r in results) == 1
    assert len({r.execution_project_root for r in results}) == 1
    assert sum(len(service(ctx).tasks.list_all()) for ctx in [a, b]) == 1


def test_invalid_execution_config_never_falls_back_to_report_source(tmp_path):
    ctx = project(tmp_path, 'reporter', execution='/missing-developer-project')
    mirror(ctx)
    host = service(ctx)
    result = host.start_manual_triage(8, refresh=False)
    assert not result.ok and result.status == 'invalid_execution_project'
    assert host.tasks.list_all() == []


def test_cancel_then_explicit_start_can_use_new_execution_project(tmp_path):
    owner = project(tmp_path, 'owner')
    next_owner = project(tmp_path, 'next-owner')
    reporter = project(tmp_path, 'reporter', execution=str(owner.project_root))
    item = mirror(reporter)
    host = service(reporter)
    first = host.start_manual_triage(8, refresh=False, defer_remote_sync=True, defer_attachments=True)
    service(owner).writer.emit('external_issue.triage.queued_cancelled', actor='web', task_id=first.task_id,
        correlation_id=first.workflow_run_id, payload={'source_key': item.issue_key, 'source_revision': first.source_revision,
            'workflow_run_id': first.workflow_run_id})
    current = execution_settings(reporter.project_root, reporter.config)
    save_execution_settings(reporter.project_root, reporter.config, {'execution_project_root': str(next_owner.project_root),
        'config_digest': current['config_digest']})
    assert host.process_mirrors().triage_queued == 0
    second = host.start_manual_triage(8, refresh=False, defer_remote_sync=True, defer_attachments=True)
    assert second.ok and second.queued
    assert second.execution_project_root == str(next_owner.project_root)
    assert len(service(next_owner).tasks.list_all()) == 1


def test_execution_routing_verifies_signed_owner_ledger(tmp_path, monkeypatch):
    from zf.core.config.schema import EventSigningConfig
    owner = project(tmp_path, 'owner')
    reporter = project(tmp_path, 'reporter')
    item = mirror(reporter)
    docs = list(yaml.safe_load_all(owner.config_path.read_text()))
    next(d for d in docs if d['kind'] == 'ZfConfig')['spec']['security'] = {
        'event_signing': {'enabled': True, 'secret_env': 'ISSUE_EXECUTION_TEST_KEY'}}
    owner.config_path.write_text(yaml.safe_dump_all(docs))
    monkeypatch.setenv('ISSUE_EXECUTION_TEST_KEY', 'test-only-key')
    config = load_config(owner.config_path)
    assert isinstance(config.security.event_signing, EventSigningConfig)
    EventWriter(event_log_from_project(owner.state_dir, config=config)).emit('external_issue.triage.queued', actor='kernel',
        payload={'source_key': item.issue_key, 'workflow_run_id': 'SIGNED-TRIAGE'})
    assert IssueExecutionRoutes(service(reporter)).target(item, automatic=True).project_root == owner.project_root


def test_poll_routes_multiple_issues_in_one_owner_pass(tmp_path, monkeypatch):
    owner = project(tmp_path, 'owner')
    reporter = project(tmp_path, 'reporter', execution=str(owner.project_root))
    first = mirror(reporter)
    second = replace(first, issue_key='github:123:9', number=9, node_id='I_node_9', html_url=first.html_url[:-1]+'9')
    IssueMirrorStore(reporter.state_dir).upsert(second, 'second report')
    counts = []
    original = ExternalIssueIngressService._process_mirrors_locked
    def counted(service, **kwargs):
        counts.append(service.project_root)
        return original(service, **kwargs)
    monkeypatch.setattr(ExternalIssueIngressService, '_process_mirrors_locked', counted)
    result = service(reporter).process_mirrors()
    assert result.ok and result.triage_queued == 2
    assert counts.count(owner.project_root) == 1
    assert len(service(owner).tasks.list_all()) == 2
