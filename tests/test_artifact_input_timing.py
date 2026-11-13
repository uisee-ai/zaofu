from zf.core.events import ZfEvent
from zf.runtime.artifact_input_timing import attempt_timing


def event(kind, second, attempt='a'):
    return ZfEvent(type='workflow.operation.' + kind, ts=f'2026-11-13T00:00:{second:02d}+00:00',
                   payload={'operation_id': 'op', 'active_attempt_id': attempt})


def test_attempt_wall_time_does_not_invent_reasoning_or_mix_retries():
    events = [event('requested', 1), event('started', 3), event('settled', 13),
              event('started', 20, 'retry'), event('settled', 30, 'retry')]
    result = attempt_timing(events, {'operation_id': 'op'}, 'a')
    assert result['queue_ms'] == 2000 and result['execution_ms'] == 10000
    assert result['model_reasoning_ms'] is None
    retry = attempt_timing(events, {'operation_id': 'op'}, 'retry')
    assert retry['queue_ms'] is None and retry['execution_ms'] == 10000
    assert attempt_timing(events + [event('started', 4)], {'operation_id': 'op'}, 'a')['execution_ms'] is None


def test_settlement_without_repeated_attempt_identity_uses_ordered_active_attempt():
    end = event('settled', 30)
    end.payload.pop('active_attempt_id')
    events = [event('requested', 1), event('started', 3), event('retry_started', 20, 'retry'),
              event('started', 21, 'retry'), end]
    assert attempt_timing(events, {'operation_id': 'op'}, 'a')['execution_ms'] is None
    assert attempt_timing(events, {'operation_id': 'op'}, 'retry')['execution_ms'] == 9000
