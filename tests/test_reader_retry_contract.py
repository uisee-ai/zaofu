from pathlib import Path

from zf.core.task.schema import Task
from zf.runtime.fanout_retry_briefing import write_blocking_reader_retry_briefing
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot, write_task_contract_snapshot, snapshot_payload_fields,
    build_target_snapshot, write_target_snapshot, target_payload_fields,
)
from tests.test_reader_target_pinning import _state


def test_legacy_contract_reader_retry_keeps_complete_result_template(tmp_path: Path):
    head, state, _, _, orch = _state(tmp_path, workdirs=False)
    snapshot = build_task_contract_snapshot(Task(id='T1', title='retry target'),
        workflow_run_id='RUN', task_map_generation_id='GEN', base_commit=head, task_ref='task/T1')
    descriptor = write_task_contract_snapshot(state, snapshot)
    target = write_target_snapshot(state, build_target_snapshot(descriptor, target_commit=head, contract_snapshot=snapshot))
    child = {'child_id': 'judge', 'payload': {
        'task_id': 'T1', 'workflow_run_id': 'RUN', 'target_commit': head,
        'operation_id': 'fixture-operation', 'result_protocol_mode': 'blocking',
        **snapshot_payload_fields(descriptor), **target_payload_fields(target),
    }}
    result = write_blocking_reader_retry_briefing(orch, role=orch.config.roles[0],
        manifest={'fanout_id': 'fixture-fanout', 'stage_id': 'final-judge',
                  'topology': 'fanout_reader', 'trace_id': 'RUN', 'target_ref': head},
        child=child, run_id='fixture-current-retry', contract_handoff_keys=())
    assert result is not None
    text = result.read_text()
    assert 'fixture-current-retry' in text
    assert 'verification-result.v1' in text
    assert descriptor['ref'] in text
    assert '--payload-file' in text
    assert 'immutable input templates' in text
    assert 'replace only' in text
