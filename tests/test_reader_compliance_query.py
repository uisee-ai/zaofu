"""CLI compliance must include Reader policies stored outside the ledger."""
import json

import pytest
from fastapi.testclient import TestClient

from zf.cli.main import main
from zf.core.events import EventLog, EventWriter
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from tests.test_artifact_read_snapshot import published
from zf.web.server import create_app


@pytest.mark.parametrize('policy_state', ['valid', 'missing', 'wrong_attempt', 'corrupt'])
def test_cli_missing_reads_resolves_immutable_reader_policy(tmp_path, monkeypatch, capsys, policy_state):
    manifest, required, _ = published(tmp_path)
    monkeypatch.setenv('ZF_PROJECT_ROOT', str(tmp_path.parent))
    monkeypatch.setenv('ZF_STATE_DIR', str(tmp_path))
    if policy_state != 'missing':
        policy = write_immutable_json_sidecar(tmp_path,
            {'attempt_id': 'wrong' if policy_state == 'wrong_attempt' else 'a',
             'required_reads': required}, root='policies', kind='policy',
            schema_version='input-consumption-policy.v1', created_by='kernel')
        request = write_immutable_json_sidecar(tmp_path,
            {'request': {'input_consumption_policy_ref': policy}}, root='requests',
            kind='request', schema_version='request.v1', created_by='kernel')
        EventWriter(EventLog(tmp_path / 'events.jsonl'), default_origin='kernel').emit(
            'workflow.operation.requested', payload={
                'operation_id': 'op', 'active_attempt_id': 'a', 'request_ref': request})
        if policy_state == 'corrupt':
            (tmp_path / policy['ref']).write_text('{}')

    def inspect():
        assert main(['attempt', 'missing-reads', 'a', '--state-dir', str(tmp_path)]) == 0
        return json.loads(capsys.readouterr().out)

    result = inspect()
    client = TestClient(create_app(tmp_path, project_root=tmp_path.parent))
    response = client.get('/api/attempts/a/missing-reads')
    assert response.status_code == 200
    for key in ('required_read_count', 'missing_reads', 'protocol_repair_required'):
        assert response.json()[key] == result[key]
    if policy_state != 'valid':
        assert result['required_policy_status'] == 'unknown'
        assert result['required_read_count'] is None
        assert result['missing_reads'] is None
        assert result['protocol_repair_required'] is True
        assert result['required_policy_error']
        return
    assert result['required_read_count'] == len(required) > 0
    assert result['missing_reads'] == required
    assert result['protocol_repair_required'] is True
    for row in required:
        read_attempt_artifact(tmp_path, manifest=manifest,
            source_id=row['source_id'], artifact_id=row['artifact_id'],
            json_path=row['json_path'], actor='dev', role='dev', provider='codex',
            max_chars=1_000_000, max_items=1000)
    complete = inspect()
    assert complete['required_policy_status'] == 'available'
    assert complete['required_read_count'] == len(required)
    assert complete['missing_reads'] == []
    assert complete['protocol_repair_required'] is False
