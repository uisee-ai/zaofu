"""Read the entire effective control plane once, not duplicate YAML renderings."""
import pytest

from tests.test_orchestrator_agent_operations import _runtime, _request
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.orchestrator_agent_briefing import build_orchestrator_agent_operation_briefing


@pytest.mark.parametrize('checkpoint', ['plan_candidate', 'stage_barrier', 'pre_impl', 'semantic_failure'])
def test_actual_oa_request_requires_complete_config_subtree_with_full_snapshot_identity(tmp_path, checkpoint):
    runtime = _runtime(tmp_path)
    config = {'runtime': {'source_repair': {'allow_paths': ['src/a.py']}},
              'roles': [{'name': 'dev', 'model': 'pinned'}],
              'workflow': {'run_limits': {'token_budget': 0}}}
    ref = write_sidecar_json(runtime.state_dir, 'artifacts/config.json', {
        'schema_version': 'effective-config-snapshot.v1', 'config': config,
        'yaml': 'duplicate-text\n' * 20000, 'compiled_graph': {'derived_from': 'config'}},
        kind='effective_config_snapshot', schema_version='effective-config-snapshot.v1', created_by='test')
    prepared = _request(runtime, checkpoint=checkpoint, payload_overrides={'effective_config_ref': ref})
    context = prepared.context
    row = next(r for r in context.read_policy['required_reads'] if r['source_id'] == 'effective-config')
    assert row['json_path'] == '$.config'
    assert row['artifact_sha256'] == ref['sha256']
    assert context.effective_config_ref == ref
    assert not row['allow_truncated'] and row['max_chars'] == 0
    briefing = build_orchestrator_agent_operation_briefing(state_dir=runtime.state_dir, prepared=prepared)
    line = next(l for l in briefing.splitlines() if '--source effective-config ' in l)
    assert "--json-path '$.config'" in line


def test_legacy_unwrapped_snapshot_remains_fully_required(tmp_path):
    runtime = _runtime(tmp_path)
    ref = write_sidecar_json(runtime.state_dir, 'artifacts/config.json', {'runtime': {'mode': 'legacy'}},
        kind='effective_config_snapshot', schema_version='legacy.v1', created_by='test')
    prepared = _request(runtime, payload_overrides={'effective_config_ref': ref})
    row = next(r for r in prepared.context.read_policy['required_reads'] if r['source_id'] == 'effective-config')
    assert row['json_path'] == '$'


def test_config_subtree_delivery_satisfies_real_admission_without_yaml(tmp_path):
    from tests.test_orchestrator_agent_operations import _decision
    from zf.runtime.orchestrator_agent_operations import activate_orchestrator_agent_operation
    from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
    from zf.runtime.artifact_read_ledger import read_attempt_artifact
    from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential, ResultSubmitError
    runtime = _runtime(tmp_path)
    ref = write_sidecar_json(runtime.state_dir, 'artifacts/config.json', {
        'schema_version': 'effective-config-snapshot.v1',
        'config': {'runtime': {'source_repair': {'enabled': False}}, 'workflow': {'run_limits': {'token_budget': 0}}},
        'yaml': 'not-required-duplicate\n' * 10000},
        kind='effective_config_snapshot', schema_version='effective-config-snapshot.v1', created_by='test')
    token = provision_role_submit_credential(runtime.state_dir, 'orchestrator').read_text().strip()
    provision_role_artifact_read_credential(runtime.state_dir, 'orchestrator', role_name='orchestrator', provider='mock')
    prepared = _request(runtime, payload_overrides={'effective_config_ref': ref})
    activate_orchestrator_agent_operation(runtime, prepared, dispatch_id='d', causation_id='evt-run-start')
    submitter = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=runtime.event_writer)
    def submit():
        return submitter.submit(operation_id=prepared.operation_id, semantic_result=_decision(prepared),
            role_instance='orchestrator', credential=token)
    rows = prepared.context.read_policy['required_reads']
    for row in sorted(rows, key=lambda row: row['source_id'] == 'effective-config'):
        if row['source_id'] == 'effective-config':
            with pytest.raises(ResultSubmitError, match='required_read_missing'):
                submit()
        read_attempt_artifact(runtime.state_dir, manifest=prepared.context.source_manifest,
            source_id=row['source_id'], artifact_id=row['artifact_id'], json_path=row['json_path'],
            actor='orchestrator', role='orchestrator', provider='mock')
    assert submit().canonical_event_type == 'orchestrator.semantic.decision.submitted'
