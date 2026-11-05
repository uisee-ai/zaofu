import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.stage_failure_replan import plan_reader_stage_replan
from tests.test_stage_failure_replan import _config


@pytest.mark.parametrize('index_ref', ['current-index.json', ''])
def test_retry_binds_map_and_index_from_admitted_package_source(index_ref):
    origin = ZfEvent(type='issue.requested', payload={
        'workflow_run_id': 'run', 'task_map_ref': 'old-map.json', 'task_map_digest': 'old-map-digest',
        'source_index_ref': 'old-index.json', 'source_index_digest': 'old-index-digest',
        'target_ref': 'candidate',
    })
    source = ZfEvent(type='task_map.ready', payload={
        'workflow_run_id': 'run', 'task_map_ref': 'current-map.json',
        'task_map_digest': 'current-map-digest', 'source_index_ref': index_ref,
    })
    admitted = ZfEvent(type='plan.artifact_package.admitted', payload={
        'workflow_run_id': 'run', 'package_id': 'current', 'package_ref': 'current-package.json',
        'package_digest': 'current-digest', 'task_map_generation': 'current-generation',
        'source_event_id': source.id,
    })
    draft = ZfEvent(type='task_map.ready', payload={
        'workflow_run_id': 'run', 'task_map_ref': 'unadmitted-draft.json',
    })
    failure = ZfEvent(type='issue.triage.failed', payload={
        'workflow_run_id': 'run', 'trigger_event_id': origin.id, 'reason': 'stale package',
    })
    retry, _ = plan_reader_stage_replan(_config(), [origin, source, admitted, draft, failure], failure)
    assert retry.payload['plan_artifact_package_id'] == 'current'
    assert retry.payload['task_map_ref'] == 'current-map.json'
    assert retry.payload['task_map_digest'] == 'current-map-digest'
    assert retry.payload['source_index_ref'] == index_ref
    assert 'source_index_digest' not in retry.payload
    assert retry.payload['target_ref'] == 'candidate'
