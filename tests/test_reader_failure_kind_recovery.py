"""Reader execution abstention uses repair recovery, not semantic retry."""
from zf.core.events.model import ZfEvent
from zf.runtime.failure_kind import failure_kind_from_payload
from zf.runtime.stage_failure_replan import plan_reader_stage_replan
from tests.test_stage_failure_replan import _config, _failure
from tests.test_reader_stage_recovery import recovery, execute


def test_reader_abstention_does_not_automatically_replan():
    origin = ZfEvent(type='issue.requested', payload={'issue_ref': 'docs/issue.md'})
    failure = _failure(reason='Required input does not fit the execution boundary')
    failure.payload['failure_class'] = 'reader_execution_failure'
    trigger, reason = plan_reader_stage_replan(_config(), [origin, failure], failure)
    assert trigger is None
    assert reason == 'infrastructure_failure'


def test_historical_reader_execution_failures_do_not_exhaust_semantic_replans():
    origin = ZfEvent(type='issue.requested', payload={'issue_ref': 'docs/issue.md'})
    infra = [_failure(reason='Reader could not consume inputs') for _ in range(3)]
    for event in infra:
        event.payload['failure_class'] = 'reader_execution_failure'
    quality = _failure(reason='Plan leaves a required outcome unowned')
    trigger, _ = plan_reader_stage_replan(_config(), [origin, *infra, quality], quality)
    assert trigger is not None
    assert trigger.payload['rework_attempt'] == 1


def test_repaired_reader_class_can_use_controlled_recovery(recovery):
    service, failure, original = recovery
    payload = {key: value for key, value in failure.payload.items() if key != 'failure_kind'}
    failure = service.writer.emit(failure.type, correlation_id='run-1', payload={
        **payload, 'failure_class': 'reader_execution_failure',
    })
    result = execute(service, failure.id)
    assert result['ok'], result
    triggers = [e for e in service.writer.event_log.read_all() if e.type == original.type]
    assert len(triggers) == 2
    assert triggers[-1].payload['repaired_failure_event_id'] == failure.id
    assert not execute(service, failure.id)['ok']


def test_explicit_kind_and_unknown_failure_classes_stay_authoritative():
    assert failure_kind_from_payload({
        'failure_class': 'reader_execution_failure', 'failure_kind': 'quality',
    }) == 'quality'
    assert failure_kind_from_payload({'failure_class': 'semantic_rejection'}) == ''
    assert failure_kind_from_payload({'failure_class': 'unknown'}) == ''
