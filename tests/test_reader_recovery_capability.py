"""Provider replacement renews only the immutable active reader capability."""
import json
from types import SimpleNamespace

import pytest

from tests.test_reader_respawn_continuation import _running_recovered_synth
from zf.cli.restart import _restart_role
from zf.runtime.artifact_read_capability import (
    ArtifactReadCapabilityError, authorize_artifact_read_from_environment,
    bind_attempt_artifact_read_capability, provision_role_artifact_read_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def expired_reader(tmp_path, monkeypatch):
    state, log, runtime, role, dispatch, operation = _running_recovered_synth(tmp_path)
    manifest = hydrate_sidecar_ref(state, dispatch.payload['attempt_source_manifest']).payload
    token = provision_role_artifact_read_credential(state, role.instance_id,
        role_name=role.name, provider=role.backend)
    bind_attempt_artifact_read_capability(state, operation_id=operation['operation_id'],
        attempt_id=operation['active_attempt_id'], role_instance=role.instance_id, manifest=manifest)
    binding = state / 'private/artifact-read/attempts' / (operation['active_attempt_id'] + '.json')
    body = json.loads(binding.read_text());body['expires_at'] = '2000-01-01T00:00:00+00:00'
    binding.write_text(json.dumps(body))
    monkeypatch.setenv('ZF_ROLE_INSTANCE', role.instance_id)
    monkeypatch.setenv('ZF_ARTIFACT_READ_TOKEN_FILE', str(token))
    for key in ('ZF_ARTIFACT_READ_TOKEN', 'ZF_ROLE_NAME', 'ZF_ROLE_BACKEND', 'ZF_ARTIFACT_PURPOSE'):
        monkeypatch.delenv(key, raising=False)
    return state, log, runtime, role, dispatch, operation, manifest, binding


def test_cli_restart_renews_expired_capability_with_same_input_scope(tmp_path, monkeypatch):
    state, log, runtime, role, _dispatch, operation, manifest, binding = expired_reader(tmp_path, monkeypatch)
    before = json.loads(binding.read_text())
    with pytest.raises(ArtifactReadCapabilityError, match='expired'):
        authorize_artifact_read_from_environment(state, manifest=manifest, event_log=log)
    alive = [True]
    monkeypatch.setattr(runtime.transport, 'terminate', lambda _: alive.__setitem__(0, False), raising=False)
    monkeypatch.setattr(runtime.transport, 'is_alive', lambda _: alive[0])
    monkeypatch.setattr(runtime, '_wait_role_ready', lambda _: True)
    monkeypatch.setattr('zf.cli.restart.WorkflowRuntimeCoordinator', lambda *a, **kw: runtime)
    monkeypatch.setattr(runtime, '_get_spawn_coordinator', lambda: SimpleNamespace(
        spawn=lambda *a, **kw: alive.__setitem__(0, True)))
    assert _restart_role(role.name, runtime.config, runtime.transport, log, state, tmp_path, False) == 0
    identity = authorize_artifact_read_from_environment(state, manifest=manifest, event_log=log)
    assert identity['operation_id'] == operation['operation_id']
    assert identity['attempt_id'] == operation['active_attempt_id']
    after = json.loads(binding.read_text())
    assert {k: v for k, v in after.items() if k != 'expires_at'} == {
        k: v for k, v in before.items() if k != 'expires_at'}


@pytest.mark.parametrize('fault', ['terminal', 'request_hash', 'attempt', 'manifest', 'request_body'])
def test_recovery_does_not_renew_or_deliver_a_divergent_read_binding(tmp_path, monkeypatch, fault):
    state, log, runtime, role, dispatch, operation, _manifest, binding = expired_reader(tmp_path, monkeypatch)
    child = runtime._active_fanout_child_for_instance(role.instance_id)
    if fault == 'terminal':
        runtime.event_writer.emit('workflow.operation.blocked', payload={
            'operation_id': operation['operation_id'], 'reason': 'provider_policy_refusal'})
    elif fault == 'request_hash': child['request_hash'] = 'different'
    elif fault == 'attempt': child['attempt_id'] = 'different'
    elif fault == 'manifest':
        (state / dispatch.payload['attempt_source_manifest']['ref']).write_text('{}')
    elif fault == 'request_body':
        (state / operation['request_ref']['ref']).write_text('{}')
    before = binding.read_bytes()
    sent = len(runtime.transport.sent)
    assert not runtime._inject_fanout_recovery_briefing(role, child)
    assert binding.read_bytes() == before
    assert len(runtime.transport.sent) == sent
