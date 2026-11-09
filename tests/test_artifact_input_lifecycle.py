import pytest

from zf.core.events import ZfEvent
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.artifact_input_index import input_index
from tests.test_artifact_read_snapshot import published, IDENTITY


def admission(version='1', **extra):
    return ZfEvent(id=f'admit-{version}', type='plan.artifact_package.admitted', payload={
        'workflow_run_id': 'r', 'plan_revision': version, 'task_map_generation': version,
        'package_ref': f'packages/{version}', 'package_digest': version * 64,
        'source_event_id': f'critic-{version}', **extra})


def binding(version='1'):
    return {'workflow_run_id': 'r', 'plan_artifact_package_ref': f'packages/{version}',
            'plan_artifact_package_digest': version * 64}


def test_lifecycle_uses_canonical_admission_replay_replacement_and_cancel():
    first, second = admission(), admission('2')
    assert input_lifecycle(binding(), [])['status'] == 'unadmitted'
    assert input_lifecycle(binding(), [first, first])['status'] == 'current'
    prior = input_lifecycle(binding(), [first, second])
    assert prior['status'] == 'superseded' and prior['requires_rebind']
    assert prior['authorizes_rebind'] is False
    assert 'packages/2' not in str(prior)
    assert input_lifecycle(binding('2'), [first, second])['status'] == 'current'
    cancel = ZfEvent(type='fanout.cancelled', payload={'workflow_run_id': 'r',
        'failure_scope': 'plan_admission', 'trigger_event_id': 'critic-2'})
    assert input_lifecycle(binding('2'), [first, second, cancel])['status'] == 'rejected'
    assert input_lifecycle(binding(), [first, second, cancel])['status'] == 'current'
    rejected = ZfEvent(type='plan.artifact_package.rejected', payload=second.payload)
    assert input_lifecycle(binding('2'), [first, rejected])['status'] == 'rejected'
    conflict = admission('2', plan_revision='1')
    assert input_lifecycle(binding(), [first, conflict])['status'] == 'unknown'
    assert input_lifecycle({'workflow_run_id': 'r'}, [])['status'] == 'pinned'


def test_index_cursor_survives_heartbeat_but_not_current_package_change(tmp_path):
    manifest, required, _ = published(tmp_path)
    manifest.update(binding())
    first = admission()
    lifecycle = input_lifecycle(manifest, [first])
    args = dict(manifest=manifest, required=required, identity=IDENTITY, reads=[], limit=1)
    page = input_index(**args, lifecycle=lifecycle)
    heartbeat = ZfEvent(type='worker.heartbeat', payload={'workflow_run_id': 'r'})
    unchanged = input_lifecycle(manifest, [first, heartbeat])
    assert unchanged == lifecycle
    assert input_index(**args, lifecycle=unchanged, cursor=page['next_cursor'])['items']
    with pytest.raises(ValueError, match='stale'):
        input_index(**args, lifecycle=input_lifecycle(manifest, [first, admission('2')]), cursor=page['next_cursor'])
