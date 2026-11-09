"""Pre-submit path checks use the same actual selection as writer admission."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tests.test_plan_candidate_preflight import _task_map, _source_index
from zf.core.events import ZfEvent
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.writer_fanout_admission import load_writer_task_map


@pytest.mark.parametrize('selector,conflict', [({}, True), ({'task_ids': ['TASK-1']}, False),
    ({'task_ids': ['TASK-1', 'TASK-2']}, True), ({'wave': 1}, False)])
def test_preflight_and_real_loader_agree_on_selected_path_conflicts(tmp_path, selector, conflict):
    task_map = _task_map(); first = task_map['tasks'][0]; first['wave'] = 1
    second = deepcopy(first)
    second.update(task_id='TASK-2', title='Second task', goal_claim_ids=[], wave=2, blocked_by=['TASK-1'])
    second['validation']['commands'][0].update(id='test-second', acceptance_ids=['AC-2'])
    second['acceptance_criteria'][0].update(id='AC-2', verification_command_ids=['test-second'])
    task_map['tasks'].append(second)
    index = _source_index(); index['tasks'].append({**index['tasks'][0], 'task_id': 'TASK-2'})
    state = tmp_path / '.state'; state.mkdir(); (state / 'map.json').write_text(json.dumps(task_map))
    payload = {'flow_kind': 'issue', 'issue_ref': 'docs/issue.md', 'task_map_ref': 'map.json', **selector}
    args = dict(stage=SimpleNamespace(task_map='map.json'), event=ZfEvent(type='task_map.ready', payload=payload),
                pdd_id='GOAL-1', state_dir=state, project_root=tmp_path)
    if conflict:
        with pytest.raises(RuntimeError, match='overlapping allowed paths'):
            load_writer_task_map(**args)
    else:
        loaded = load_writer_task_map(**args)
        assert [t['task_id'] for t in loaded.task_items] == ['TASK-1']
    result = evaluate_plan_candidate_preflight(state_dir=state, project_root=tmp_path,
        reports=[{'report': {'task_map': task_map, 'source_index': index, 'plan_md': '# Plan'}}],
        manifest={'trigger_payload': payload}, metadata={'flow_kind': 'issue'})
    errors = [e for e in result['errors'] if e['code'] == 'writer_fanout_task_map_invalid']
    assert bool(errors) is conflict, result['errors']
