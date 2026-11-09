"""A new plan package must not revoke another task's current input binding."""
import json

import pytest

from tests.test_artifact_input_lifecycle import admission, binding
from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import source_manifest_from_payload
from zf.runtime.task_contract_snapshot import current_task_contract_identity


def setup(state, *, operation_task="T7", operation_run="r"):
    task = Task(id='T7', title='current task', status='in_progress', assigned_to='dev-1',
        active_dispatch_id='dispatch', execution_binding=TaskExecutionBinding(workflow_run_id='r'),
        contract=TaskContract(behavior='original behavior', evidence_contract={'task_map_generation': '1'}))
    TaskStore(state / 'kanban.json').add(task)
    source = state / 'artifacts/input.json'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"body":"original pinned requirements"}')
    manifest, _ = source_manifest_from_payload(state_dir=state, project_root=state.parent,
        payload={'input_refs': [{'source_id': 'plan', 'artifact_id': 'input', 'ref': 'artifacts/input.json'}]},
        workflow_run_id='r', task_id='T7', attempt_id='a', dispatch_id='dispatch',
        manifest_metadata={**binding(), **current_task_contract_identity(task), 'read_purpose': 'implementation',
            'handoff_authority_contract': {'attempt_domain': 'task'}})
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    writer.append(admission())
    payload = {'workflow_run_id': operation_run, 'operation_id': 'op', 'active_attempt_id': 'a',
        'dispatch_id': 'dispatch', 'task_map_generation': '1', 'role_instance': 'dev-1',
        'required_reads': [{'source_id': 'plan', 'artifact_id': 'input',
            'artifact_sha256': manifest['sources'][0]['sha256'], 'json_path': '$'}]}
    for kind in ['workflow.operation.requested', 'workflow.operation.started']:
        writer.append(ZfEvent(type=kind, task_id=operation_task, payload=payload))
    writer.append(admission('2'))
    return task, manifest, writer


def test_actual_cli_recovery_and_index_keep_current_task_generation(tmp_path, monkeypatch, capsys):
    state = tmp_path / 'state'
    task, manifest, _ = setup(state)
    _grant(state, monkeypatch, manifest=manifest, operation_id='op')
    for command in ['recover', 'index']:
        assert main(['artifact', command, '--attempt', 'a', '--state-dir', str(state)]) == 0
        result = json.loads(capsys.readouterr().out)
        lifecycle = result['inputs']['lifecycle'] if command == 'recover' else result['lifecycle']
        assert lifecycle['requires_rebind'] is False
        assert lifecycle['reason'] == 'current_task_generation_binding'
        assert lifecycle['authorizes_rebind'] is False
        if command == 'recover':
            assert result['status'] == 'bound'
            assert result['authorizes_execution'] is False
            assert 'required_read' in result['commands']
    assert TaskStore(state / 'kanban.json').get(task.id).active_dispatch_id == 'dispatch'


@pytest.mark.parametrize('fault', ['contract', 'dispatch', 'run', 'generation', 'task_done',
    'cancelled', 'new_attempt', 'wrong_operation_task', 'wrong_operation_run', 'rejected_package', 'missing_task',
    'wrong_role', 'missing_generation', 'reader_domain', 'conflicting_package'])
def test_old_inputs_do_not_resurrect_changed_or_retired_tasks(tmp_path, fault):
    from zf.runtime.artifact_input_lifecycle import input_lifecycle
    task, manifest, writer = setup(tmp_path, operation_task="other" if fault == "wrong_operation_task" else "T7",
        operation_run="other" if fault == "wrong_operation_run" else "r")
    store = TaskStore(tmp_path / 'kanban.json')
    if fault == 'contract':
        task.contract.behavior = 'changed behavior'; store.update(task.id, contract=task.contract)
    elif fault == 'dispatch': store.update(task.id, active_dispatch_id='new')
    elif fault == 'run': store.update(task.id, execution_binding=TaskExecutionBinding(workflow_run_id='other'))
    elif fault == 'generation':
        task.contract.evidence_contract['task_map_generation'] = '2'; store.update(task.id, contract=task.contract)
    elif fault == 'task_done': store.update(task.id, status='done')
    elif fault == 'missing_task': manifest['task_id'] = 'absent'
    elif fault == 'wrong_role': store.update(task.id, assigned_to='dev-2')
    elif fault == 'missing_generation': manifest.pop('task_map_generation')
    elif fault == 'reader_domain': manifest['handoff_authority_contract']['attempt_domain'] = 'plan'
    elif fault == 'conflicting_package':
        writer.append(admission('3', task_map_generation='1'))
        manifest.update(binding('3'))
    elif fault == 'rejected_package':
        writer.append(ZfEvent(type='fanout.cancelled', payload={'workflow_run_id': 'r',
            'failure_scope': 'plan_admission', 'trigger_event_id': 'critic-1'}))
    elif fault in {'cancelled', 'new_attempt'}:
        p = {'workflow_run_id': 'other' if fault == 'wrong_operation_run' else 'r',
             'operation_id': 'op', 'active_attempt_id': 'new' if fault == 'new_attempt' else 'a',
             'dispatch_id': 'dispatch', 'task_map_generation': '1', 'role_instance': 'dev-1'}
        writer.append(ZfEvent(type='workflow.operation.cancelled' if fault == 'cancelled' else 'workflow.operation.started',
            task_id='other' if fault == 'wrong_operation_task' else 'T7', payload=p))
    assert input_lifecycle(manifest, writer.event_log.read_all(), state_dir=tmp_path)['requires_rebind']
