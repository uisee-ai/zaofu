"""Controlled gap recovery carries new inputs and rejects missing partitions."""
import json

import pytest

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import PlanArtifactPackageError, prepare_plan_artifact_package
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from tests.test_control_actions_recovery import _service


def _port(state, tasks):
    body = {'schema_version': 'task-scoped-plan-input.v1', 'shared': {'rule': 'preserve'},
            'tasks': [{'task_id': task, 'body': {'requirement': 'complete original input'}} for task in tasks]}
    descriptor = write_immutable_json_sidecar(state, body, root='fixtures', kind='task_execution_input',
                                             schema_version=body['schema_version'], created_by='test')
    return {'logical_name': 'task_execution_input', 'schema_version': body['schema_version'],
            'ref': descriptor['ref'], 'sha256': descriptor['sha256']}


@pytest.mark.parametrize('fault', ['', 'digest', 'protected', 'duplicate', 'malformed'])
def test_controlled_retrigger_delivers_pinned_ports_or_rejects_before_ready(tmp_path, fault):
    svc = _service(tmp_path)
    port = _port(svc.state_dir, ['NEW'])
    ports = [port]
    if fault == 'digest': port['sha256'] = '0' * 64
    elif fault == 'protected': port['logical_name'] = 'task_map'
    elif fault == 'duplicate': ports.append(dict(port))
    elif fault == 'malformed': ports.append('bad')
    source = svc.writer.emit('worker.reply.requested', actor='operator', payload={})
    request = svc.writer.emit('control.action.requested', actor='operator', payload={})
    result = svc.execute(action='candidate-rework-apply', requested_action='candidate-rework-apply',
        requested=request, payload={'pdd_id': 'P1', 'source_event_id': source.id,
          'candidate_rework_action': 'retrigger', 'task_map_ref': 'artifacts/new-map.json',
          'source_commit': 'a' * 40, 'candidate_base_commit': 'a' * 40, 'plan_ports': ports})
    ready = [e for e in svc.writer.event_log.read_all() if e.type == 'task_map.ready']
    if fault:
        assert not result['ok']
        assert not ready
    else:
        assert result['ok']
        assert ready[0].payload['plan_ports'][0]['ref'] == port['ref']
        assert ready[0].payload['plan_ports'][0]['sha256'] == port['sha256']


@pytest.mark.parametrize('present', [False, True])
def test_package_admission_rejects_new_task_without_input_partition(tmp_path, present):
    state = tmp_path / '.zf'
    state.mkdir()
    contract = {'schema_version': 'run-contract.v1', 'workflow': {'kind': 'prd'}}
    write_run_contract(state, {**contract, 'contract_digest': stable_json_sha256(contract)})
    (state / 'requirement.md').write_text('Deliver AC-1.\n')
    task_map = {'schema_version': 'task-map.v1', 'objective': 'Deliver AC-1',
      'required_plan_ports': ['task_execution_input'],
      'tasks': [{'task_id': 'NEW', 'parent_task_id': 'OLD', 'acceptance_criteria': ['AC-1']}],
      'amend': {'kind': 'goal_gap', 'gap_task_ids': ['NEW'], 'superseded_task_ids': []}}
    (state / 'task-map.json').write_text(json.dumps(task_map))
    args = dict(state_dir=state, project_root=tmp_path, events=[], workflow_run_id='run',
        flow_kind='prd', producer_stage_id='plan', goal_id='P1',
        payload={'task_map_ref': '.zf/task-map.json', 'prd_ref': '.zf/requirement.md',
          'task_ids': ['NEW'], 'plan_ports': [_port(state, ['OLD', 'NEW'] if present else ['OLD'])]})
    if present:
        package, _, _ = prepare_plan_artifact_package(**args)
        assert 'task_execution_input' in package['required_ports']
    else:
        with pytest.raises(PlanArtifactPackageError, match='NEW.*no resolvable partition'):
            prepare_plan_artifact_package(**args)
        assert not list((state / 'artifacts/plan-packages').glob('*.json'))

from tests.test_task_plan_consumption import case  # noqa: E402,F401
from tests.test_plan_input_lineage_preflight import _candidate  # noqa: E402


@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('changed_input', [False, True])
def test_partition_admission_preserves_exact_replacement_lineage(case, retained, changed_input):
    from zf.runtime.plan_candidate_input_lineage import validate_dispatch_input_partitions
    state, trigger, report = _candidate(case, retained=retained,
                                       fault='input_changed' if changed_input else '')
    body = report['plan_ports'][0]['body']
    descriptor = write_immutable_json_sidecar(state, body, root='fixtures', kind='task_execution_input',
                                             schema_version=body['schema_version'], created_by='test')
    args = dict(state_dir=state, task_map=report['task_map'], trigger=trigger,
                required_ports=['task_execution_input'],
                ports=[{'logical_name': 'task_execution_input', **descriptor}])
    if changed_input:
        with pytest.raises(ValueError, match='differs from pinned baseline'):
            validate_dispatch_input_partitions(**args)
    else:
        validate_dispatch_input_partitions(**args)


def test_partition_admission_checks_only_dispatched_consumers_but_validates_whole_structure(tmp_path):
    from zf.runtime.plan_candidate_input_lineage import validate_dispatch_input_partitions
    tasks = [{'task_id': 'NEW', 'evidence_contract': {'required_plan_ports': ['local']}},
             {'task_id': 'UNCHANGED'}]
    port = _port(tmp_path, ['NEW'])
    port['logical_name'] = 'local'
    args = dict(state_dir=tmp_path, task_map={'tasks': tasks}, ports=[port],
                required_ports=[], trigger={}, task_ids=['NEW'])
    validate_dispatch_input_partitions(**args)
    args['task_ids'] = None
    validate_dispatch_input_partitions(**args)  # UNCHANGED does not consume local.
    args['required_ports'] = ['local']
    with pytest.raises(ValueError, match='UNCHANGED'):
        validate_dispatch_input_partitions(**args)
    args['task_ids'] = ['NEW']
    validate_dispatch_input_partitions(**args)
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    body = hydrate_sidecar_ref(tmp_path, port).payload
    body['tasks'].append(dict(body['tasks'][0]))
    bad = write_immutable_json_sidecar(tmp_path, body, root='fixtures', kind='local',
                                     schema_version=body['schema_version'], created_by='test')
    args['ports'] = [{'logical_name': 'local', **bad}]
    with pytest.raises(ValueError, match='unique'):
        validate_dispatch_input_partitions(**args)


def test_explicit_selection_checks_both_stages_and_requires_scoped_execution_input(tmp_path):
    from zf.runtime.plan_candidate_input_lineage import validate_dispatch_input_partitions
    policy = {'schema_version': 'task-plan-input-selection.v1',
              'implementation': ['task_execution_input'],
              'task-verify': ['task_execution_input', 'verify_details']}
    task = {'task_id': 'NEW', 'evidence_contract': {'plan_input_selection': policy}}
    execution = _port(tmp_path, ['NEW'])
    details = {**_port(tmp_path, ['OTHER']), 'logical_name': 'verify_details'}
    args = dict(state_dir=tmp_path, task_map={'tasks': [task]}, ports=[execution, details],
                required_ports=['task_execution_input', 'verify_details'], trigger={})
    with pytest.raises(ValueError, match='verify_details'):
        validate_dispatch_input_partitions(**args)
    args['ports'] = [execution]
    validate_dispatch_input_partitions(**args)
    legacy = write_immutable_json_sidecar(tmp_path, {'legacy': 'whole input'}, root='fixtures',
                                         kind='task_execution_input', schema_version='v1', created_by='test')
    args['ports'] = [{'logical_name': 'task_execution_input', **legacy}]
    with pytest.raises(ValueError, match='must be task scoped'):
        validate_dispatch_input_partitions(**args)
