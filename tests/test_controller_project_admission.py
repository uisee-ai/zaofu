"""Cloud initialization wire retries preserve authorization and canonical project state."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import httpx2
import pytest
import yaml
from tests.test_controller_workspace import workspace as workspace, enrolled

from zf.core.config.controller_workspace import load_controller_workspace
from zf.core.events import EventLog
from zf.core.workspace.project_initializer import ProjectInitializer
from zf.integrations.controller.binding import ManagedDenied, bind
from zf.integrations.controller.installation import Installation
from zf.integrations.controller.workspace import apply_policy
from zf.integrations.controller.workspace_transport import WorkspaceTransport


@pytest.fixture
def cloud(workspace, tmp_path):
    config, peer, _ = workspace
    enrolled(workspace)
    raw = yaml.safe_load(config.path.read_text())
    raw['workspace']['controller'].update(management='managed', hosting='cloud',
        gateway_origin='https://workspace.example.test', project_root=str(tmp_path / 'projects'))
    config.path.write_text(yaml.safe_dump(raw))
    config = load_controller_workspace(config.path)
    peer.policy.update(capabilities=['observe', 'gateway', 'commands', 'budget'], limits={'management': 'managed'})
    bind(config)
    old = peer.handle
    peer.admissions, peer.lost_admit, peer.lost_policy_ack = {}, False, False
    peer.deny_admission = False

    def handle(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.path == '/internal/v1/me/projects/admit':
            if peer.deny_admission:
                return httpx2.Response(409, json={'error': 'project_quota_exceeded'})
            previous = peer.admissions.get(body['request_id'])
            if previous:
                assert previous['request'] == body
                result = previous['result']
            else:
                assert body['generation'] == peer.generation
                assert body['policy_version'] == peer.policy['version']
                peer.policy['projects'] = [*peer.policy['projects'], body['project_id']]
                peer.policy['version'] += 1
                result = dict(protocol='zfc.control.v1', admitted=True, workspace_id=config.workspace_id,
                    deployment_id=peer.deployment_id, generation=peer.generation, project_id=body['project_id'],
                    policy_version=peer.policy['version'])
                peer.admissions[body['request_id']] = {'request': body, 'result': result}
            if peer.lost_admit:
                peer.lost_admit = False
                raise httpx2.ReadTimeout('lost admission response')
            return httpx2.Response(200, json=result)
        if request.url.path == '/internal/v1/me/control/policy':
            assert body['applied']
            peer.policy.update(applied_version=body['version'], applied_deployment=peer.deployment_id)
            if peer.lost_policy_ack:
                peer.lost_policy_ack = False
                raise httpx2.ReadTimeout('lost policy ACK')
        return old(request)

    peer.handle = handle
    with WorkspaceTransport(config, Installation(config).credentials()) as transport:
        apply_policy(config, Installation(config).credentials(), transport, Installation(config))
    return config, peer


def initialize(root, **changes):
    return ProjectInitializer().initialize(cwd=root, preset='minimal', create_root=True,
        with_instruction_docs=False, with_git_hooks=False, workspace_register=True, **changes)


def test_cloud_init_requires_admission_and_ack_before_first_event(cloud):
    config, peer = cloud
    result = initialize(config.project_root / 'new')
    assert result.ok
    assert result.registered_project.project_id in peer.policy['projects']
    assert peer.policy['applied_version'] == peer.policy['version']
    assert [e.type for e in EventLog(result.state_dir / 'events.jsonl').read_all()] == ['session.started']
    with pytest.raises(FileExistsError):
        initialize(config.project_root / 'new')


@pytest.mark.parametrize('lost', ['lost_admit', 'lost_policy_ack'])
def test_lost_response_retries_without_force_or_duplicate_grant(cloud, lost):
    config, peer = cloud
    root = config.project_root / 'retry'
    setattr(peer, lost, True)
    with pytest.raises(ManagedDenied):
        initialize(root)
    assert not list(root.glob('**/events.jsonl'))
    from zf.core.workspace.project_admission import inspect_project_admission
    assert inspect_project_admission(root)['admission']['action'] == 'initialize_state'
    result = initialize(root)
    assert result.ok and len(peer.admissions) == 1
    assert len(EventLog(result.state_dir / 'events.jsonl').read_all()) == 1


def test_quota_failure_never_writes_canonical_project_state(cloud):
    config, peer = cloud
    peer.deny_admission = True
    root = config.project_root / 'quota'
    with pytest.raises(ManagedDenied, match='project_quota_exceeded'):
        initialize(root)
    assert (root / 'zf.yaml').is_file()
    assert not list(root.glob('**/events.jsonl'))
    assert not list(root.glob('**/kanban.json'))
    peer.deny_admission = False
    assert initialize(root).ok


def test_cloud_paths_rejected_before_config_or_git_writes(cloud, tmp_path):
    from zf.cli.project import init_flow_project
    config, _ = cloud
    outside = tmp_path / 'outside'
    with pytest.raises(ManagedDenied, match='private project'):
        initialize(outside)
    assert not outside.exists()
    with pytest.raises(ManagedDenied, match='private project'):
        init_flow_project(project_root=outside, kind='prd', name='Outside', create_root=True)
    assert not outside.exists()
    with pytest.raises(ManagedDenied, match='state must remain'):
        initialize(config.project_root / 'bad-state', explicit_state_dir=tmp_path / 'state')
    assert not (config.project_root / 'bad-state').exists()


def test_concurrent_same_root_initialization_does_not_reset_state(cloud):
    config, peer = cloud
    root = config.project_root / 'parallel'
    barrier = threading.Barrier(2)
    def run():
        barrier.wait()
        try:
            return initialize(root)
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    result = next(r for r in results if r is not None)
    assert len([r for r in results if r is not None]) == 1
    assert len(peer.admissions) == 1
    assert len(EventLog(result.state_dir / 'events.jsonl').read_all()) == 1


def test_managed_boundary_imports_in_a_fresh_worker_process():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c', 'import zf.integrations.controller.binding'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('wrong_grant', [False, True])
def test_cloud_delivery_obtains_bounded_handoff_before_historical_replay(cloud, wrong_grant):
    from zf.integrations.controller.outbox import Outbox
    from zf.integrations.controller.workspace import observation, heartbeat
    config, peer = cloud
    old = Installation(config).credentials()
    outbox = Outbox(config.state_dir, observation(config, old))
    heartbeat(outbox, old['generation'], [])
    peer.deployment_id, peer.generation = 'successor', 2
    credentials = {**old, 'deployment_id': 'successor', 'generation': 2}
    outbox = Outbox(config.state_dir, observation(config, credentials))
    previous, calls = peer.handle, []
    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        calls.append(path)
        if path == '/internal/v1/me/cloud-handoffs':
            assert body['origin_id'] == old['deployment_id'] and body['max_seq'] == 1
            import time
            return httpx2.Response(200, json={**body, 'workspace_id': config.workspace_id,
                'uploader_id': 'wrong' if wrong_grant else 'successor', 'expires_at': int(time.time())+60})
        if path == '/internal/v1/me/receipt-dispositions':
            return httpx2.Response(200, json={'protocol':'zfc.control.v1','recovery_epoch':1,
                'streams':[], 'dispositions':[], 'streams_truncated':False,'dispositions_truncated':False})
        result = previous(request)
        if path == '/internal/v1/me':
            data = result.json()
            data['capabilities'] += ['receipt_dispositions', 'cloud_receipt_handoff']
            return httpx2.Response(200, json=data)
        return result
    peer.handle = handle
    with WorkspaceTransport(config, credentials) as transport:
        transport.handshake()
        if wrong_grant:
            with pytest.raises(ValueError, match='handoff identity'):
                transport.deliver(outbox)
            assert '/internal/v1/me/batches' not in calls
            assert outbox.status()['pending_records'] == 1
        else:
            assert transport.deliver(outbox)['pending_records'] == 0
            assert calls.index('/internal/v1/me/cloud-handoffs') < calls.index('/internal/v1/me/receipt-dispositions')
            assert next(iter(peer.records.values()))['origin_deployment_id'] == old['deployment_id']


def test_pending_cloud_initialization_can_retry_after_generation_replacement(cloud, monkeypatch):
    from zf.integrations.controller.project_admission import intent_name
    config, peer = cloud
    root = config.project_root / 'pending-replacement'
    peer.deny_admission = True
    with pytest.raises(ManagedDenied, match='project_quota_exceeded'):
        initialize(root)
    installation = Installation(config)
    path = installation.path / intent_name(root)
    old = json.loads(path.read_text())
    credentials = {**installation.credentials(), 'generation': 2, 'deployment_id': 'successor'}
    peer.generation, peer.deployment_id = 2, 'successor'
    monkeypatch.setattr(Installation, 'credentials', lambda self: credentials)
    with WorkspaceTransport(config, credentials) as transport:
        apply_policy(config, credentials, transport, installation)
    with pytest.raises(ManagedDenied, match='project_quota_exceeded'):
        initialize(root)
    pending = json.loads(path.read_text())
    assert pending['project_id'] == old['project_id']
    assert pending['request']['request_id'] != old['request']['request_id']
    assert pending['request']['generation'] == 2
    assert not list(root.glob('**/events.jsonl'))
    peer.deny_admission = False
    assert initialize(root).ok and len(peer.admissions) == 1
