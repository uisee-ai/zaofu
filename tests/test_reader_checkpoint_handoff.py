"""A controlled fresh Reader retry inherits only its pinned predecessor facts."""
import json
import pytest

from tests.test_reader_work_record import candidate_reader
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_handoff import predecessor_work_record
from zf.runtime.work_record import publish_work_record, authorize_work_record
from zf.runtime.result_submit import SemanticResultSubmitService
from zf.runtime.workflow_operation import reduce_workflow_operations


def checkpoint_and_fail(tmp_path):
    state, log, runtime, op, manifest, token = candidate_reader(tmp_path)
    record = publish_work_record(state_dir=state, event_log=log, operation_id=op['operation_id'],
        role='verify', credential=token, request_id='before-stop', record_body={
            'completed_actions': ['REQ-1 examined against frozen candidate'],
            'pending': ['REQ-2'], 'next_step': 'Check REQ-2', 'evidence_files': [],
            'report_draft': 'Unfinished independent report',
        })
    dispatch = [e for e in log.read_all() if e.type == 'fanout.child.dispatched'][-1]
    failed = runtime.event_writer.emit('fanout.child.failed', payload={
        **dispatch.payload, 'reason': 'mock external failure',
    })
    workflow_operation_service(runtime).fail(operation_id=op['operation_id'],
        request_hash=op['request_hash'], workflow_run_id=op['workflow_run_id'],
        reason='mock external failure', causation_id=failed.id)
    return state, log, runtime, op, token, record, dispatch


def retry(values):
    state, log, runtime, old, token, record, dispatch = values
    result = runtime._rerun_fanout_child(dispatch.payload['fanout_id'], dispatch.payload['child_id'])
    assert result.action == 'rerun_fanout_child', result.reason
    current = next(o for o in reduce_workflow_operations(log.read_all()).values() if o['status'] == 'running')
    service = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=runtime.event_writer)
    binding, manifest, _ = authorize_work_record(service, current['operation_id'], 'verify', token)
    return current, binding, manifest


def fail_current(values, current):
    state, log, runtime, _, token, record, _ = values
    dispatch = [e for e in log.read_all() if e.type == 'fanout.child.dispatched'][-1]
    failed = runtime.event_writer.emit('fanout.child.failed', payload={
        **dispatch.payload, 'reason': 'mock repeated interruption',
    })
    workflow_operation_service(runtime).fail(operation_id=current['operation_id'],
        request_hash=current['request_hash'], workflow_run_id=current['workflow_run_id'],
        reason='mock repeated interruption', causation_id=failed.id)
    return state, log, runtime, current, token, record, dispatch


def test_reader_repeated_interruption_preserves_last_checkpoint_without_growing_body(tmp_path):
    values = checkpoint_and_fail(tmp_path)
    sizes = []
    for _ in range(3):
        current, binding, manifest = retry(values)
        ref, body = predecessor_work_record(values[0], manifest, binding)
        assert ref == values[5]['work_record_ref']
        assert body['binding']['operation_id'] != current['operation_id']
        handoff = next(s for s in manifest['sources'] if s['source_id'] == 'reader-handoff')
        sizes.append((values[0] / handoff['ref']).stat().st_size)
        values = fail_current(values, current)
    assert max(sizes) - min(sizes) < 100


@pytest.mark.parametrize('corrupt', ['handoff', 'record'])
def test_reader_inherited_checkpoint_corruption_blocks_retry(tmp_path, corrupt):
    values = checkpoint_and_fail(tmp_path)
    current, _, manifest = retry(values)
    values = fail_current(values, current)
    ref = (next(s for s in manifest['sources'] if s['source_id'] == 'reader-handoff')
           if corrupt == 'handoff' else values[5]['work_record_ref'])
    (values[0] / ref['ref']).write_text('{}')
    result = values[2]._rerun_fanout_child(values[6].payload['fanout_id'], values[6].payload['child_id'])
    assert result.action == 'rerun_fanout_child_failed'
    assert len([e for e in values[1].read_all() if e.type == 'fanout.child.dispatched']) == 2


@pytest.mark.parametrize('corrupt_new', [False, True])
def test_new_reader_checkpoint_supersedes_inherited_history_without_fallback(tmp_path, corrupt_new):
    values = checkpoint_and_fail(tmp_path)
    current, _, _ = retry(values)
    published = publish_work_record(state_dir=values[0], event_log=values[1],
        operation_id=current['operation_id'], role='verify', credential=values[4],
        request_id='new-checkpoint', record_body={'completed_actions': ['REQ-2 checked'],
            'pending': [], 'next_step': 'Finish report', 'evidence_files': [], 'report_draft': 'New report'})
    values = fail_current(values, current)
    if corrupt_new:
        (values[0] / published['work_record_ref']['ref']).write_text('{}')
        result = values[2]._rerun_fanout_child(values[6].payload['fanout_id'], values[6].payload['child_id'])
        assert result.action == 'rerun_fanout_child_failed'
    else:
        _, binding, manifest = retry(values)
        ref, body = predecessor_work_record(values[0], manifest, binding)
        assert ref == published['work_record_ref']
        assert body['report_draft'] == 'New report'


def test_fresh_reader_retry_reads_pinned_checkpoint_without_accepting_it(tmp_path):
    values = checkpoint_and_fail(tmp_path)
    state, log, runtime, old, token, record, _ = values
    current, binding, manifest = retry(values)
    ref, body = predecessor_work_record(state, manifest, binding)
    assert current['operation_id'] != old['operation_id']
    assert ref == record['work_record_ref']
    assert body['report_draft'] == 'Unfinished independent report'
    assert body['claims_status'] == 'worker_report_unverified'
    assert body['authorizes_execution'] is False
    from zf.runtime.artifact_input_recovery import build_input_recovery
    pack = build_input_recovery(state_dir=state, manifest=manifest, events=log.read_all(),
        identity={'actor': 'verify'}, required=[], reads=[])
    assert '--predecessor' in pack['commands']['predecessor_work_record_read']
    assert len(json.dumps(pack).encode()) <= 8192
    assert not any(e.type == 'task.done' for e in log.read_all())


def test_retry_handoff_is_immutable_and_no_older_checkpoint_fallback(tmp_path):
    values = checkpoint_and_fail(tmp_path)
    state, log, _, _, _, record, _ = values
    (state / record['work_record_ref']['ref']).write_text('{}')
    result = values[2]._rerun_fanout_child(values[6].payload['fanout_id'], values[6].payload['child_id'])
    assert result.action == 'rerun_fanout_child_failed'
    assert len([e for e in log.read_all() if e.type == 'fanout.child.dispatched']) == 1


def test_successor_cli_paginates_history_but_cannot_use_predecessor_authority(tmp_path, monkeypatch, capsys):
    from zf.cli.main import main
    values = checkpoint_and_fail(tmp_path)
    state, log, runtime, old, token, record, _ = values
    current, binding, manifest = retry(values)
    monkeypatch.setenv('ZF_ROLE_INSTANCE', 'verify')
    monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN', token)
    command = ['artifact', 'work-record', 'show', '--predecessor', '--operation',
               current['operation_id'], '--state-dir', str(state)]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['sha256'] == record['work_record_ref']['sha256']
    offset = first['next_page_start']
    if offset is not None:
        assert main(command + ['--page-start', str(offset), '--digest', first['sha256']]) == 0
        capsys.readouterr()
        assert main(command + ['--page-start', str(offset), '--digest', 'invalid']) == 1
        capsys.readouterr()
    service = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=runtime.event_writer)
    with pytest.raises(ValueError):
        authorize_work_record(service, old['operation_id'], 'verify', token)
    monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN', 'foreign-credential')
    assert main(command) == 1


@pytest.mark.parametrize('field', ['fanout_id', 'child_id', 'role_instance', 'target_commit'])
@pytest.mark.parametrize('inherited', [False, True])
def test_retry_cannot_attach_other_predecessor_scope(tmp_path, field, inherited):
    from zf.runtime.reader_handoff import attach_reader_handoff
    values = checkpoint_and_fail(tmp_path)
    if inherited:
        current, _, _ = retry(values)
        values = fail_current(values, current)
    state, log, runtime, old, _, _, dispatch = values
    payload = {**dispatch.payload['payload'], 'operation_id': 'new-op',
               'parent_operation_id': old['operation_id'], 'retry_of_operation_id': old['operation_id'],
               field: 'other'}
    with pytest.raises(ValueError, match='scope'):
        attach_reader_handoff(runtime, payload)


def test_handoff_replay_keeps_frozen_digest(tmp_path):
    from zf.runtime.reader_handoff import attach_reader_handoff
    values = checkpoint_and_fail(tmp_path)
    state, log, runtime, old, _, record, _ = values
    current, binding, manifest = retry(values)
    dispatch = [e for e in log.read_all() if e.type == 'fanout.child.dispatched'][-1]
    payload = dict(dispatch.payload['payload'])
    original = next(s for s in manifest['sources'] if s['source_id'] == 'reader-handoff')
    attach_reader_handoff(runtime, payload)
    replay = next(s for s in payload['input_refs'] if s['source_id'] == 'reader-handoff')
    assert replay['sha256'] == original['sha256']
    assert hydrate_sidecar_ref(state, replay).payload['target']['operation_id'] == current['operation_id']
