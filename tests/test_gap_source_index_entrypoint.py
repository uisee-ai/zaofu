"""The production gap bridge publishes the replacement's source-index partition."""
import json
import subprocess

from zf.core.events.model import ZfEvent
from zf.runtime.task_scoped_plan_inputs import task_input_paths
from tests.test_module_gap_plan_runtime import _state, _write_base_task_map


def test_gap_bridge_rebinds_source_index_and_keeps_original(tmp_path):
    state, log, transport, orch = _state(tmp_path)
    ref = _write_base_task_map(state)
    old = 'CANGJIE-WEB-001'
    new = 'CANGJIE-WEB-R2'
    source = {'schema_version': 'source-index.v1', 'tasks': [{'task_id': old, 'requirement': 'original text'}],
              'task_input_policy': {'schema_version': 'task-input-policy.v1',
                  'shared_paths': ['$.schema_version'], 'tasks': [{'task_id': old, 'paths': ['$.tasks[0]']}]}}
    source_path = state / 'source-index.json'
    source_path.write_text(json.dumps(source))
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp_path, text=True).strip()
    event = ZfEvent(id='replacement-gap', type='flow.gap_plan.ready', actor='zf-cli', payload={
        'schema_version': 'goal-gap-plan.v1', 'pdd_id': 'CANGJIE', 'feature_id': 'CANGJIE',
        'goal_id': 'CANGJIE', 'goal_kind': 'refactor', 'gap_category': 'parity_gap',
        'task_map_ref': ref, 'source_index_ref': '.zf/source-index.json',
        'candidate_base_commit': commit, 'gap_tasks': [{
            'task_id': new, 'parent_task_id': old, 'supersedes_task_ids': [old],
            'base_commit': commit, 'owner_role': 'dev', 'claim_paths': ['web/**'],
            'acceptance': ['original behavior'], 'verify_commands': ['pytest'],
            'source_refs': ['git:' + commit]}]})
    orch.run_once(events=[event])
    ready = next(e for e in log.read_all() if e.type == 'task_map.ready')
    assert ready.payload['task_ids'] == [new]
    assert ready.payload['source_index_ref'] != '.zf/source-index.json'
    updated = json.loads((state / ready.payload['source_index_ref']).read_text())
    assert updated['tasks'] == [{'task_id': new, 'requirement': 'original text'}]
    assert task_input_paths(updated, task_id=new) == ['$.schema_version', '$.tasks[0]']
    assert json.loads(source_path.read_text()) == source
    assert len(transport.sent) == 1
