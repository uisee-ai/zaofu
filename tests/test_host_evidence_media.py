"""Independent readers must be able to open authenticated container media."""
import base64
import hashlib
import json
from dataclasses import asdict

import pytest

from tests.test_reader_stage_recovery import recovery
from tests.test_host_verification import policy
from zf.runtime.host_verification_handoff import resume_reader_after_probes
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_host_manifest import digest


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')


def media_receipt(state, *, file_changes=None, evidence_changes=None, data=PNG, max_artifact_bytes=None):
    p = policy()
    if max_artifact_bytes is not None:
        p.max_artifact_bytes = max_artifact_bytes
    entry = {'path': 'desktop.png', 'sha256': hashlib.sha256(data).hexdigest(),
             'base64': base64.b64encode(data).decode()}
    entry.update(file_changes or {})
    evidence = write_sidecar_json(state, 'artifacts/container-evidence.json', {
        'image': p.image, 'target_commit': 'a' * 40, 'files': [entry],
        **(evidence_changes or {})}, kind='container_evidence',
        schema_version='container-evidence.v1', created_by='run-manager')
    receipt = write_sidecar_json(state, 'artifacts/receipt.json', {
        'command_id': 'probe', 'status': 'completed', 'source_stable': True,
        'exit_code': 0, 'execution_origin': 'preauthorized_container',
        'policy_digest': digest(asdict(p)), 'image': p.image,
        'target_commit': 'a' * 40, 'evidence_ref': evidence},
        kind='task_host_receipt', schema_version='task-host-receipt.v1', created_by='run-manager')
    return p, receipt


@pytest.mark.parametrize('large_media', [False, True])
def test_reader_handoff_materializes_existing_media_without_another_probe(recovery, large_media):
    service, failure, original = recovery
    data = b'x' * (21 * 1024 * 1024) if large_media else PNG
    p, receipt = media_receipt(service.state_dir, data=data,
                              max_artifact_bytes=64 * 1024 * 1024 if large_media else None)
    operation = {'operation_id': 'op-reader', 'workflow_run_id': 'run-1'}
    service.writer.emit('fanout.child.dispatched', payload={**operation, 'fanout_id': 'f1'})
    service.writer.emit(failure.type, correlation_id='run-1', payload=failure.payload)
    service.writer.emit('runtime.action.attempt.completed', payload={**operation,
        'approval_ref': p.approval_ref, 'kind': 'verify', 'receipt_ref': receipt})
    contract = {'verification_commands': [{'command_id': 'probe', 'command': 'python3 probe.py'}]}
    assert resume_reader_after_probes(service, operation, contract, p, service.writer.event_log.read_all())
    indices = list((service.state_dir / 'artifacts/container-media').glob('*/index.json'))
    assert len(indices) == 1, 'Reader needs a bound media index, not Base64-only evidence'
    index = json.loads(indices[0].read_text())
    image = service.state_dir / index['files'][0]['ref']
    assert image.read_bytes() == data
    assert not image.stat().st_mode & 0o222
    trigger = [e for e in service.writer.event_log.read_all() if e.type == original.type][-1]
    assert str(indices[0]) in trigger.payload['operator_feedback'][0]
    assert not any(e.type == 'test.passed' or (e.type == 'runtime.action.attempt.started'
                   and e.payload.get('kind') == 'verify')
                   for e in service.writer.event_log.read_all())


@pytest.mark.parametrize('changes', [
    {'path': '../outside.png'}, {'path': '/tmp/outside.png'}, {'path': 'a\\b.png'},
    {'path': 'a//b.png'}, {'path': 'a/./b.png'}, {'path': 'a\n.png'},
    {'sha256': '0' * 64}, {'base64': '!invalid!'},
])
def test_invalid_media_is_not_materialized(tmp_path, changes):
    from zf.runtime.host_evidence_media import materialize_container_media
    p, ref = media_receipt(tmp_path, file_changes=changes)
    with pytest.raises(ValueError):
        materialize_container_media(tmp_path, [ref], image=p.image)
    assert not (tmp_path / 'artifacts/container-media').exists()


@pytest.mark.parametrize('changes', [
    {'image': 'unreviewed'}, {'target_commit': 'b' * 40},
    {'files': [None]}, {'files': 'invalid'},
])
def test_evidence_binding_and_shape_are_checked(tmp_path, changes):
    from zf.runtime.host_evidence_media import materialize_container_media
    p, ref = media_receipt(tmp_path, evidence_changes=changes)
    with pytest.raises(ValueError):
        materialize_container_media(tmp_path, [ref], image=p.image)
    assert not (tmp_path / 'artifacts/container-media').exists()


def test_projection_is_idempotent_and_original_is_unchanged(tmp_path):
    from zf.runtime.host_evidence_media import materialize_container_media
    p, ref = media_receipt(tmp_path)
    original = (tmp_path / 'artifacts/container-evidence.json').read_bytes()
    first = materialize_container_media(tmp_path, [ref, ref], image=p.image)
    second = materialize_container_media(tmp_path, [ref], image=p.image)
    assert first == second and len(first) == 1
    assert (tmp_path / 'artifacts/container-evidence.json').read_bytes() == original


def test_projection_symlink_cannot_write_outside_state(tmp_path):
    from zf.runtime.host_evidence_media import materialize_container_media
    state = tmp_path / 'state'
    p, ref = media_receipt(state)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (state / 'artifacts/container-media').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        materialize_container_media(state, [ref], image=p.image)
    assert not list(outside.iterdir())


def test_corrupt_projection_is_not_silently_reused(tmp_path):
    from zf.runtime.host_evidence_media import materialize_container_media
    p, ref = media_receipt(tmp_path)
    index_ref = materialize_container_media(tmp_path, [ref], image=p.image)[0]
    index = json.loads((tmp_path / index_ref['ref']).read_text())
    target = tmp_path / index['files'][0]['ref']
    target.chmod(0o600)
    target.write_bytes(b'changed')
    with pytest.raises(ValueError, match='differs'):
        materialize_container_media(tmp_path, [ref], image=p.image)


def test_tampered_parent_evidence_is_rejected(tmp_path):
    from zf.runtime.host_evidence_media import materialize_container_media
    p, ref = media_receipt(tmp_path)
    (tmp_path / 'artifacts/container-evidence.json').write_text('{}')
    with pytest.raises(ValueError):
        materialize_container_media(tmp_path, [ref], image=p.image)


@pytest.mark.parametrize('limit', ['file', 'total'])
def test_configured_media_limits_reject_before_projection(tmp_path, limit):
    from zf.runtime.host_evidence_media import materialize_container_media
    entry = {'path': 'one.png', 'base64': base64.b64encode(b'abcdef').decode(),
             'sha256': hashlib.sha256(b'abcdef').hexdigest()}
    p, ref = media_receipt(tmp_path, evidence_changes={
        'files': [entry, {**entry, 'path': 'two.png'}]})
    p.max_artifact_bytes = 5 if limit == 'file' else 6
    p.max_total_evidence_bytes = 10
    with pytest.raises(ValueError, match='limit'):
        materialize_container_media(tmp_path, [ref], image=p.image, policy=p)
    assert not (tmp_path / 'artifacts/container-media').exists()
