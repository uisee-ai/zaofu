"""Light entry preserves the canonical Task's write and acceptance boundaries."""
import json

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.light_flow import maybe_synthesize_light_task_map
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.verification_commands import task_contract_verification_commands
from tests.test_light_flow import _light_config


def _entry(tmp_path, *, stale=False):
    state = tmp_path / '.runtime'
    state.mkdir()
    store = TaskStore(state / 'kanban.json')
    command = {'id': 'UNIT-1', 'command': 'python -m unittest -v test_counter',
               'acceptance_ids': ['AC-1'], 'owner': 'impl_self_check', 'tier': 'task_non_smoke',
               'deterministic': True, 'reusable': True, 'timeout_seconds': 30}
    criterion = {'id': 'AC-1', 'statement': 'Counter rejects booleans', 'mandatory': True,
                 'verification_owner': 'candidate_verify', 'verification_tier': 'candidate',
                 'verification_command_ids': ['UNIT-1']}
    root = Task(id='ROOT', title='Fix counter', contract=TaskContract(
        scope=['counter.py', 'test_counter.py'], exclusions=['README.md'],
        explicit_non_goals=['No network API'], acceptance_criteria=[criterion],
        validation={'commands': [command]}, goal_claim_ids=['GOAL-1']))
    store.add(root)
    log = EventLog(state / 'events.jsonl')
    event = ZfEvent(type='prd.requested', actor='operator', task_id='ROOT', payload={
        'pdd_id': 'bounded', 'workflow_run_id': 'run-1', 'objective': 'Fix counter',
        'source_refs': {'task_contract_digest': 'sha256:stale' if stale else task_workflow_binding_digest(root)}})
    result = maybe_synthesize_light_task_map(event=event, config=_light_config(), state_dir=state,
        event_writer=EventWriter(log), events=[], task_store=store)
    path = state / 'artifacts/bounded/task_map.json'
    return root, log, result, path


def test_light_entry_does_not_expand_declared_write_scope(tmp_path):
    root, _, _, path = _entry(tmp_path)
    task = json.loads(path.read_text())['tasks'][0]
    assert task['allowed_paths'] == root.contract.scope
    assert task['exclusions'] == root.contract.exclusions
    assert task['explicit_non_goals'] == root.contract.explicit_non_goals


def test_light_entry_retains_acceptance_ids_and_command_evidence_binding(tmp_path):
    root, _, _, path = _entry(tmp_path)
    task = json.loads(path.read_text())['tasks'][0]
    assert task['acceptance_criteria'] == root.contract.acceptance_criteria
    assert task['validation']['commands'] == task_contract_verification_commands(root.contract)
    assert task['goal_claim_ids'] == ['GOAL-1']


def test_light_entry_blocks_stale_root_contract_before_materializing_map(tmp_path):
    _, log, result, path = _entry(tmp_path, stale=True)
    assert not path.exists()
    assert not any(e.type == 'task_map.ready' for e in log.read_all())
    assert result.type == 'run.goal.blocked'
    assert result.payload['failure_class'] == 'light_task_contract_invalid'


def test_runtime_entry_preserves_root_scope_in_canonical_writer_contract(tmp_path):
    from types import SimpleNamespace
    from zf.core.config.schema import ProjectConfig, WorkflowConfig, ZfConfig
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.writer_fanout_admission import bind_writer_task_dispatch_owner

    root, _, _, _ = _entry(tmp_path)
    state = tmp_path / '.runtime'
    config = ZfConfig(project=ProjectConfig(name='light-contract', state_dir='.runtime'),
        workflow=WorkflowConfig(flow_metadata=_light_config().workflow.flow_metadata))
    runtime = Orchestrator(state, config, SimpleNamespace(), project_root=tmp_path)
    event = ZfEvent(type='prd.requested', actor='operator', task_id=root.id, payload={
        'pdd_id': 'runtime-entry', 'workflow_run_id': 'run-2', 'objective': root.title,
        'source_refs': {'task_contract_digest': task_workflow_binding_digest(root)}})
    runtime._on_light_flow_entry(event)
    item = json.loads((state / 'artifacts/runtime-entry/task_map.json').read_text())['tasks'][0]
    contract = bind_writer_task_dispatch_owner(task=Task(id=item['task_id'], title=item['title']),
        role=SimpleNamespace(name='dev', instance_id='dev'), config=config,
        event_writer=runtime.event_writer, task_item=item)
    assert contract.scope == root.contract.scope
    assert contract.exclusions == ['README.md']
    assert contract.acceptance_criteria == root.contract.acceptance_criteria
    assert contract.validation['commands'][0]['id'] == 'UNIT-1'


def test_inherited_commands_keep_extra_checks_and_legacy_string_commands(tmp_path):
    from zf.runtime.light_flow_contract import inherit_light_task_contract
    store = TaskStore(tmp_path / 'kanban.json')
    store.add(Task(id='ROOT', title='Counter', contract=TaskContract(
        validation={'commands': ['python -m unittest -v test_counter']})))
    task_map = {'tasks': [{'validation': {'commands': [
        {'id': 'contract-verification', 'command': 'python -m compileall counter.py'},
        {'id': 'duplicate', 'command': 'python -m unittest -v test_counter'},
    ]}}]}
    inherit_light_task_contract(task_map, task_store=store, task_id='ROOT', source_refs={})
    commands = task_map['tasks'][0]['validation']['commands']
    assert [c['command'] for c in commands] == [
        'python -m unittest -v test_counter', 'python -m compileall counter.py']
    assert len({c['id'] for c in commands}) == 2
