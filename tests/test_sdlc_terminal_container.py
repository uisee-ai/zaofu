"""A Goal terminal also carries acceptance for its top-level requirement."""
import pytest
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.sdlc_graph import build_sdlc_graph
from tests.test_sdlc_graph import sources


@pytest.mark.parametrize('mismatch', ['', 'worker', 'generation', 'run', 'container'])
def test_terminal_container_acceptance_requires_current_kernel_binding(mismatch):
    event = ZfEvent(id='done', type='run.goal.completed', origin='kernel', payload={
        'workflow_run_id': 'run-a', 'task_map_generation': 'generation-a',
        'pdd_id': 'ROOT', 'feature_id': 'ROOT', 'completed_task_ids': ['T1'],
    })
    if mismatch == 'worker':
        event.origin = 'worker'
    elif mismatch == 'generation':
        event.payload['task_map_generation'] = 'old'
    elif mismatch == 'run':
        event.payload['workflow_run_id'] = 'foreign'
    elif mismatch == 'container':
        event.payload.update(pdd_id='foreign', feature_id='foreign')
    graph = build_sdlc_graph(sources(
        tasks={'ROOT': Task(id='ROOT', title='Requirement', status='done')},
        versions=[{'task_map_generation': 'generation-a', 'body': {'tasks': []}}],
        events=[(1, event)],
    ))
    node = next(n for n in graph['nodes'] if n['id'] == 'task:ROOT')
    assert node['status'] == ('evidence_missing' if mismatch else 'done')
    assert node['data']['accepted_event_ids'] == ([] if mismatch else ['done'])
