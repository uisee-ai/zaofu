"""Actual installation CLI and multi-Project Connector against a deterministic wire peer."""

import base64
from dataclasses import replace
import json
from pathlib import Path

from authlib.integrations.httpx_client import OAuth2Client
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import httpx2
import pytest
import yaml

from zf.cli.main import main
from zf.core.config.controller_workspace import load_controller_workspace
from zf.core.config.project_context import ProjectContext
from zf.core.config.loader import load_config
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.workspace.registry import WorkspaceRegistry
from zf.integrations.controller.installation import Installation
from zf.integrations.controller.outbox import Outbox
from zf.integrations.controller.workspace import observation, synchronize_workspace


def sealed(pem, payload):
    public = serialization.load_pem_public_key(pem.encode())
    key = Fernet.generate_key()
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                             algorithm=hashes.SHA256(), label=None))
    return {'algorithm': 'RSA-OAEP-SHA256+Fernet', 'key': base64.b64encode(wrapped).decode(),
            'body': Fernet(key).encrypt(json.dumps(payload).encode()).decode()}


class WirePeer:
    def __init__(self, config):
        self.config = config
        self.code = 'test-only-authorization-code-0000000000'
        self.secret = 'test-only-machine-secret-must-never-appear-in-output'
        self.client_id, self.deployment_id, self.generation = 'client-a', 'deployment-a', 1
        self.delivery = None
        self.policy = {'workspace_id': config.workspace_id, 'version': 2,
                       'projects': list(config.projects), 'capabilities': ['observe'],
                       'limits': {'management': 'observed'}}
        self.receipts, self.records, self.requests = [], {}, []
        self.lose_enrollment_response = False
        self.fail_batches = False
        self.lose_ack = False
        self.revoked = False
        self.override = {}
        self.token_requests = 0

    def credential_delivery(self, public):
        body = {'issuer': self.config.issuer, 'controller': self.config.endpoint,
                'workspace_id': self.config.workspace_id, 'deployment_id': self.deployment_id,
                'generation': self.generation, 'client_id': self.client_id, 'client_secret': self.secret,
                **self.override}
        return {'protocol': 'zfc.control.v1', 'deployment_id': self.deployment_id,
                'delivery': sealed(public, body), 'status': 'awaiting_heartbeat', 'capabilities': ['observe']}

    def token(self, oauth, *args, **kwargs):
        self.token_requests += 1
        if oauth.client_id != self.client_id or oauth.client_secret != self.secret:
            raise ValueError('test-only-invalid-credential')
        return {'access_token': 'test-only-machine-token', 'expires_in': 25}

    def handle(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path))
        if path == '/enrollment/v1/consume':
            assert body['protocol'] == 'zfc.control.v1' and body['code'] == self.code
            self.delivery = self.delivery or self.credential_delivery(body['public_key'])
            if self.lose_enrollment_response:
                self.lose_enrollment_response = False
                raise httpx2.ReadTimeout('test-only-lost-enrollment-response')
            return httpx2.Response(200, json=self.delivery)
        assert request.headers['authorization'] == 'Bearer test-only-machine-token'
        if self.revoked:
            return httpx2.Response(403, json={'error': 'deployment_not_admitted'})
        if path == '/internal/v1/me':
            return httpx2.Response(200, json={'protocol': 'zfc.telemetry.v1',
                'workspace_id': self.config.workspace_id, 'deployment_id': self.deployment_id,
                'generation': self.generation, 'status': 'active', 'max_batch': 32,
                'capabilities': ['cumulative_baseline']})
        if path == '/internal/v1/schema':
            schema = json.loads((Path(__file__).parent / 'fixtures/controller/telemetry-v1.schema.json').read_text())
            return httpx2.Response(200, json=schema)
        if path == '/internal/v1/me/control':
            return httpx2.Response(200, json={'protocol': 'zfc.control.v1',
                'deployment_id': self.deployment_id, 'generation': self.generation,
                'policy': self.policy, 'commands': []})
        if path == '/internal/v1/me/control/policy':
            self.receipts.append(body)
            return httpx2.Response(200, json={'applied': body['applied']})
        if path == '/internal/v1/me/batches':
            if self.fail_batches:
                return httpx2.Response(503, json={'error': 'capacity'})
            results = []
            for message in body['records']:
                old = self.records.setdefault(message['message_id'], message)
                assert old == message
                results.append({'message_id': message['message_id'], 'seq': message['seq'], 'status': 'accepted'})
            if self.lose_ack:
                self.lose_ack = False
                raise httpx2.ReadTimeout('test-only-lost-ACK')
            return httpx2.Response(200, json={'protocol': 'zfc.telemetry.v1', 'results': results})
        raise AssertionError(path)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('ZF_WORKSPACE_HOME', str(tmp_path / 'registry'))
    monkeypatch.delenv('ZF_PROJECT_ROOT', raising=False)
    monkeypatch.delenv('ZF_STATE_DIR', raising=False)
    path = tmp_path / 'installation' / 'zf.yaml'
    path.parent.mkdir()
    raw = {'version': '1.0', 'kind': 'WorkspaceConfig', 'workspace': {
        'name': 'local-fixture', 'state_dir': 'custom-installation-state', 'controller': {
            'management': 'observed', 'endpoint': 'https://controller.example.test',
            'issuer': 'https://identity.example.test/realms/zaofu',
            'workspace_id': 'workspace-a', 'mode': 'self_managed_cli', 'projects': []}}}
    path.write_text(yaml.safe_dump(raw))
    config = load_controller_workspace(path)
    peer = WirePeer(config)
    original = httpx2.Client.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs, transport=httpx2.MockTransport(peer.handle))

    monkeypatch.setattr(httpx2.Client, '__init__', initialize)
    monkeypatch.setattr(OAuth2Client, 'fetch_token', lambda self, *a, **kw: peer.token(self, *a, **kw))
    code_file = tmp_path / 'code'
    code_file.write_text(peer.code)
    code_file.chmod(0o600)
    return config, peer, code_file


def enrolled(workspace):
    config, peer, code_file = workspace
    installation = Installation(config)
    installation.public()
    installation.enroll(code_file)
    return installation


def add_project(config, root, *, name='fixture'):
    root.mkdir()
    path = root / 'zf.yaml'
    path.write_text(yaml.safe_dump({'version': '1.0', 'project': {'name': name, 'state_dir': 'custom-runtime'}}))
    loaded = load_config(path)
    context = ProjectContext(root, path, loaded, root / 'custom-runtime')
    entry = WorkspaceRegistry(workspace=config.workspace).upsert_context(context)
    return entry.project_id, context


def configure_projects(config, peer, ids):
    raw = yaml.safe_load(config.path.read_text())
    raw['workspace']['controller']['projects'] = ids
    config.path.write_text(yaml.safe_dump(raw))
    peer.policy['projects'] = ids
    return load_controller_workspace(config.path)


def cli(config, action, *args):
    return main(['controller', action, '--workspace-config', str(config.path), *args])


def test_cli_empty_workspace_enrollment_and_heartbeat_without_project(workspace, capsys):
    config, peer, code = workspace
    assert cli(config, 'status') == 0
    assert not config.state_dir.exists()
    assert cli(config, 'installation-key') == 0
    assert cli(config, 'enroll', '--code-file', str(code)) == 0
    assert cli(config, 'sync') == 0
    assert cli(config, 'status') == 0
    output = capsys.readouterr().out
    assert peer.secret not in output and peer.code not in output
    assert peer.receipts[-1]['applied'] is True
    records = list(peer.records.values())
    assert len(records) == 1 and records[0]['payload']['kind'] == 'heartbeat'
    assert records[0]['payload']['project_id'] is None
    assert records[0]['payload']['applied_generation'] == 1
    assert not (config.path.parent / '.zf').exists()
    installation = Installation(config)
    assert peer.secret not in (installation.path / 'binding.json').read_text()
    for name in ('binding.json', 'private.pem', 'target.json', 'policy.json'):
        assert (installation.path / name).stat().st_mode & 0o777 == 0o600
    assert installation.path.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('key,value', [
    ('management', 'managed'), ('management', None), ('mode', 'litellm'),
    ('endpoint', 'http://remote.test'), ('endpoint', 'https://user:pass@remote.test'),
    ('issuer', 'https://example.test/realms/a?redirect=x'), ('development', 'true'),
    ('projects', ['duplicate', 'duplicate']), ('projects', ['bad id']), ('batch_size', True),
    ('client_secret', 'must-not-be-configured'),
])
def test_config_rejects_unsafe_or_unimplemented_settings(workspace, key, value):
    config, _, _ = workspace
    raw = yaml.safe_load(config.path.read_text())
    raw['workspace']['controller'][key] = value
    config.path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_controller_workspace(config.path)
    assert not config.state_dir.exists()


def test_enrollment_loss_retries_same_installation_without_replacing_key(workspace):
    config, peer, code = workspace
    installation = Installation(config)
    public = installation.public()
    peer.lose_enrollment_response = True
    with pytest.raises(httpx2.ReadTimeout):
        installation.enroll(code)
    assert not (installation.path / 'binding.json').exists()
    assert installation.public() == public
    first = installation.enroll(code)
    second = installation.enroll(code)
    assert first == second and first['deployment_id'] == peer.deployment_id
    assert installation.public() == public


@pytest.mark.parametrize('field,value', [
    ('controller', 'https://untrusted.test'), ('issuer', 'https://untrusted.test/realms/x'),
    ('workspace_id', 'other-workspace'), ('deployment_id', 'wrong-deployment'), ('generation', True),
])
def test_sealed_credentials_cannot_retarget_installation(workspace, field, value):
    config, peer, code = workspace
    installation = Installation(config)
    installation.public()
    peer.override[field] = value
    with pytest.raises(ValueError):
        installation.enroll(code)
    assert not (installation.path / 'binding.json').exists()


def test_private_input_permissions_symlinks_and_missing_key_fail_closed(workspace, tmp_path):
    config, _, code = workspace
    installation = Installation(config)
    installation.public()
    code.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'):
        installation.enroll(code)
    code.chmod(0o600)
    linked = tmp_path / 'linked-code'
    linked.symlink_to(code)
    with pytest.raises(OSError):
        installation.enroll(linked)
    installation.enroll(code)
    (installation.path / 'private.pem').unlink()
    with pytest.raises(ValueError, match='cannot regenerate'):
        installation.public()


def test_reusing_state_with_different_target_is_rejected(workspace):
    config, _, _ = workspace
    enrolled(workspace)
    with pytest.raises(ValueError, match='target changed'):
        Installation(replace(config, workspace_id='workspace-b')).credentials()


def test_credential_rotation_preserves_deployment_stream_and_rejects_rollback(workspace, tmp_path):
    config, peer, _ = workspace
    installation = enrolled(workspace)
    synchronize_workspace(config)
    before = Outbox(config.state_dir, observation(config, installation.credentials())).status()
    public = installation.public()['public_key']
    old = (installation.path / 'binding.json').read_text()
    peer.client_id = 'client-rotated'
    peer.secret = 'test-only-rotated-secret'
    delivery_file = tmp_path / 'rotation'
    delivery_file.write_text(json.dumps(peer.credential_delivery(public)))
    delivery_file.chmod(0o600)
    result = installation.accept_credentials(delivery_file)
    assert result['online_verified'] and result['deployment_id'] == peer.deployment_id
    synchronize_workspace(config)
    after = Outbox(config.state_dir, observation(config, installation.credentials())).status()
    assert before['stream_id'] == after['stream_id'] and before['coverage_since'] == after['coverage_since']
    delivery_file.write_text(old)
    with pytest.raises(ValueError, match='invalid-credential'):
        installation.accept_credentials(delivery_file)
    assert installation.credentials()['client_id'] == 'client-rotated'


@pytest.mark.mock_e2e
def test_two_projects_replay_native_usage_without_changing_kernel_or_configs(workspace, tmp_path):
    config, peer, _ = workspace
    first, one = add_project(config, tmp_path / 'one')
    second, two = add_project(config, tmp_path / 'two')
    config = configure_projects(config, peer, [first, second])
    installation = enrolled((config, peer, workspace[2]))
    synchronize_workspace(config)  # Establish per-source opening boundaries before new work.
    originals = {}
    for context in (one, two):
        originals[context.config_path] = context.config_path.read_bytes()
        log = event_log_from_project(context.state_dir, config=context.config)
        EventWriter(log).append(ZfEvent(type='agent.usage', payload={'backend': 'codex', 'provider': 'openai',
            'model': 'test-model', 'accounting_mode': 'subscription', 'session_id': 'same-session',
            'usage_sample_id': 'same-sample', 'usage': {'input_tokens': 100, 'output_tokens': 20,
                'cached_input_tokens': 30}, 'private_body': 'private-prompt-must-stay-local'}))
        log.close()
        originals[context.state_dir / 'events.jsonl'] = (context.state_dir / 'events.jsonl').read_bytes()
    result = synchronize_workspace(config)
    assert result['status'] == 'synchronized' and len(result['projects']) == 2
    synchronize_workspace(config)
    usages = [m['payload'] for m in peer.records.values() if m['payload']['kind'] == 'usage']
    assert len(usages) == 2 and len({row['record_id'] for row in usages}) == 2
    assert {row['project_id'] for row in usages} == {first, second}
    assert all(row['accounting_mode'] == 'subscription' for row in usages)
    assert all(row['tokens']['input_tokens'] == 70 for row in usages)
    assert all(row.get('provider_reported_cost_usd') is None for row in usages)
    assert 'private-prompt' not in json.dumps(list(peer.records.values()))
    assert all(path.read_bytes() == content for path, content in originals.items())
    for project_id, context in ((first, one), (second, two)):
        outbox = Outbox(context.state_dir, observation(config, installation.credentials(), project_id))
        assert outbox.status()['pending_records'] == 0
        assert not (context.state_dir / 'controller' / 'private.pem').exists()


@pytest.mark.parametrize('mutation,error', [
    ({'capabilities': ['observe', 'commands'], 'limits': {'management': 'managed'}}, 'unsupported'),
    ({'projects': ['not-consented']}, 'local_consent_required'),
])
def test_policy_cannot_silently_upgrade_or_expand_scope(workspace, mutation, error):
    config, peer, _ = workspace
    enrolled(workspace)
    peer.policy.update(mutation)
    with pytest.raises(ValueError, match=error):
        synchronize_workspace(config)
    assert peer.receipts[-1] == {'protocol': 'zfc.control.v1', 'version': 2, 'applied': False, 'error': error}
    assert {row['payload']['kind'] for row in peer.records.values()} == {'heartbeat'}
    assert not (Installation(config).path / 'policy.json').exists()


def test_registry_state_hint_is_not_authoritative_and_missing_project_is_not_acked(workspace, tmp_path):
    config, peer, _ = workspace
    project_id, context = add_project(config, tmp_path / 'project')
    config = configure_projects(config, peer, [project_id])
    enrolled((config, peer, workspace[2]))
    registry = WorkspaceRegistry(workspace=config.workspace)
    registry.upsert(replace(registry.get(project_id), state_dir_hint=str(tmp_path / 'stale')))
    synchronize_workspace(config)
    assert (context.state_dir / 'controller/outbox.sqlite3').exists()
    assert not (tmp_path / 'stale').exists()
    context.config_path.unlink()
    with pytest.raises(ValueError, match='local_configuration_invalid'):
        synchronize_workspace(config)
    assert peer.receipts[-1]['applied'] is False


def test_rejected_settings_report_liveness_without_ack_or_commands_and_can_recover(workspace, monkeypatch):
    import hashlib

    config, peer, _ = workspace
    installation = enrolled(workspace)
    synchronize_workspace(config)
    saved = (installation.path / 'policy.json').read_bytes()
    before = set(peer.records)
    invalid = dict(protocol='zfc.runtime-settings.v1', interval_seconds=5, batch_size=100,
        diagnostic_sample_rate=1.0, mode='self_managed_cli', request_tokens=32768,
        request_money='0', price_version='unknown', request_seconds=600)
    peer.policy.update(version=3, limits={'management': 'observed', 'runtime_settings': invalid,
        'runtime_settings_digest': hashlib.sha256(json.dumps(
            invalid, sort_keys=True, separators=(',', ':')).encode()).hexdigest()})
    def forbidden(*args, **kwargs):
        pytest.fail('Rejected policy must not execute commands or maintenance')
    monkeypatch.setattr('zf.integrations.controller.commands.consume_commands', forbidden)
    monkeypatch.setattr('zf.integrations.controller.maintenance.maintain', forbidden)
    with pytest.raises(ValueError, match='local_configuration_invalid'):
        synchronize_workspace(config)
    assert (installation.path / 'policy.json').read_bytes() == saved
    assert peer.receipts[-1]['version'] == 3 and peer.receipts[-1]['applied'] is False
    fresh = [row for key, row in peer.records.items() if key not in before]
    assert len(fresh) == 1 and fresh[0]['payload']['kind'] == 'heartbeat'
    assert fresh[0]['payload']['kernel_status'] == 'unknown'
    assert fresh[0]['payload']['applied_generation'] == installation.credentials()['generation']
    peer.policy.update(version=4, limits={'management': 'observed'})
    assert synchronize_workspace(config)['status'] == 'synchronized'
    assert peer.receipts[-1]['version'] == 4 and peer.receipts[-1]['applied'] is True


def test_ack_loss_capacity_and_revocation_preserve_receipts(workspace):
    config, peer, _ = workspace
    installation = enrolled(workspace)
    peer.lose_ack = True
    with pytest.raises(httpx2.ReadTimeout):
        synchronize_workspace(config)
    outbox = Outbox(config.state_dir, observation(config, installation.credentials()))
    original = outbox.pending(32)[0]
    assert original['message_id'] in peer.records
    peer.fail_batches = True
    with pytest.raises(httpx2.HTTPStatusError):
        synchronize_workspace(config)
    assert outbox.pending(32)[0] == original
    peer.fail_batches = False
    synchronize_workspace(config)
    assert outbox.status()['pending_records'] == 0
    assert peer.records[original['message_id']] == original
    peer.revoked = True
    with pytest.raises(httpx2.HTTPStatusError):
        synchronize_workspace(config)
    assert installation.credentials()['deployment_id'] == 'deployment-a'


def test_scope_removal_stops_uploading_removed_project_without_deleting_local_state(workspace, tmp_path):
    config, peer, _ = workspace
    project_id, context = add_project(config, tmp_path / 'project')
    config = configure_projects(config, peer, [project_id])
    enrolled((config, peer, workspace[2]))
    synchronize_workspace(config)
    before = len(peer.records)
    peer.policy['projects'] = []
    peer.policy['version'] += 1
    result = synchronize_workspace(config)
    assert result['projects'] == [] and result['unapproved_projects'] == [project_id]
    assert (context.state_dir / 'controller/outbox.sqlite3').exists()
    assert len(peer.records) == before + 1  # Only the deployment heartbeat.


def test_policy_rollback_cannot_overwrite_last_applied_scope(workspace):
    config, peer, _ = workspace
    installation = enrolled(workspace)
    synchronize_workspace(config)
    previous = (installation.path / 'policy.json').read_bytes()
    peer.policy['version'] -= 1
    with pytest.raises(ValueError, match='rollback'):
        synchronize_workspace(config)
    assert (installation.path / 'policy.json').read_bytes() == previous


def test_one_broken_project_does_not_starve_other_project_delivery(workspace, tmp_path):
    from zf.web.terminal_registry import REGISTRY_FILENAME

    config, peer, _ = workspace
    bad, bad_context = add_project(config, tmp_path / 'bad')
    good, good_context = add_project(config, tmp_path / 'good')
    config = configure_projects(config, peer, [bad, good])
    enrolled((config, peer, workspace[2]))
    synchronize_workspace(config)
    (bad_context.state_dir / REGISTRY_FILENAME).write_text('not-json\n')
    log = event_log_from_project(good_context.state_dir, config=good_context.config)
    EventWriter(log).append(ZfEvent(type='agent.usage', payload={'backend': 'codex',
        'usage': {'input_tokens': 30, 'output_tokens': 3}}))
    log.close()
    result = synchronize_workspace(config)
    assert result['status'] == 'partial'
    assert result['projects'][0]['status'] == 'delivery_failed'
    assert any(m['payload'].get('project_id') == good and m['payload']['kind'] == 'usage'
               for m in peer.records.values())


def test_cli_errors_do_not_leak_credentials_or_source_payload(workspace, capsys):
    config, peer, _ = workspace
    enrolled(workspace)
    peer.fail_batches = True
    assert cli(config, 'sync') == 1
    output = capsys.readouterr().out
    assert peer.secret not in output and peer.code not in output
    assert 'HTTPStatusError' in output and 'Sources and unconfirmed receipts retained' in output
    assert cli(config, 'enroll') == 2
    assert cli(config, 'status', '--watch') == 2


@pytest.mark.parametrize('keyword', ['$ref', '$dynamicRef', '$recursiveRef'])
def test_schema_cannot_trigger_external_reference_fetches(workspace, monkeypatch, keyword):
    config, peer, _ = workspace
    enrolled(workspace)
    original = peer.handle

    def handle(request):
        if request.url.path == '/internal/v1/schema':
            return httpx2.Response(200, json={keyword: 'https://untrusted.test/schema'})
        return original(request)

    monkeypatch.setattr(peer, 'handle', handle)
    with pytest.raises(ValueError, match='remote schema'):
        synchronize_workspace(config)
    assert not peer.records and not peer.receipts


def test_boolean_generation_cannot_impersonate_integer_generation(workspace):
    config, peer, _ = workspace
    enrolled(workspace)
    peer.generation = True
    with pytest.raises(ValueError, match='generation'):
        synchronize_workspace(config)
    assert not peer.records and not peer.receipts
