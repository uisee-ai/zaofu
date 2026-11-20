"""Quota exhaustion must not become an ordinary completion/requeue signal."""
import pytest

from tests.test_reader_work_record import candidate_reader
from zf.core.events import ZfEvent
from zf.runtime.provider_stop import classify_provider_stop, provider_stop_action
from zf.runtime.provider_health import project_provider_health
from zf.runtime.workflow_operation import load_workflow_operation


@pytest.mark.parametrize('error', [
    {'code': 'insufficient_quota'}, {'codex_error_info': 'usage_limit_reached'},
    'You have hit your usage limit', 'quota exhausted',
])
def test_quota_error_preserves_external_wait(error):
    reason = classify_provider_stop({'error': error})
    assert reason == 'quota_exhausted'
    assert provider_stop_action(reason) == 'cooldown'


def test_quota_words_in_successful_work_are_not_provider_failure():
    assert classify_provider_stop({'terminal_event': True, 'message': 'quota exhausted case tested'}) == 'completed_with_terminal_event'


def test_inflight_reader_wait_is_durable_and_not_a_product_failure(tmp_path):
    state, log, runtime, op, _, _ = candidate_reader(tmp_path)
    event = runtime.event_writer.append(ZfEvent(type='codex.hook.stop', actor='verify',
        task_id=op['task_id'], correlation_id=op['workflow_run_id'],
        payload={'operation_id': op['operation_id'], 'provider_stop_reason': 'quota_exhausted'}))
    before = len(runtime.transport.sent)
    runtime._on_codex_hook_stop(event)
    assert load_workflow_operation(log, op['operation_id'])['status'] == 'blocked'
    waits = [e for e in log.read_all() if e.type == 'provider.stop.recovery'
             and e.payload.get('operation_id') == op['operation_id']]
    assert waits
    waiting = waits[-1].payload['external_wait']
    assert waiting['workflow_run_id'] == op['workflow_run_id']
    assert waiting['active_attempt_id'] == op['active_attempt_id']
    assert waiting['source_event_id'] == event.id
    assert waiting['probe_status'] == 'not_configured'
    assert waiting['resume_condition'] == 'authorized_availability_evidence_and_controlled_recovery'
    assert not waiting['authorizes_execution']
    # Replaying the stop does not mint a new provider turn or business verdict.
    runtime._on_codex_hook_stop(event)
    assert len(runtime.transport.sent) == before
    health = project_provider_health(state, events=log.read_all())
    row = next(r for r in health['providers'] if r['instance_id'] == 'verify')
    assert row['status'] == 'exhausted' and row['requires_operator']
    assert row['external_wait']['active_attempt_id'] == op['active_attempt_id']
