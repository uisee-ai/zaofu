from copy import deepcopy

import pytest

from tests.test_control_actions_surgery import _service, _exec
from zf.core.events.model import ZfEvent
from zf.runtime.run_manager_router import build_no_progress_projection


def dismissed(tmp_path):
    service, log = _service(tmp_path)
    service.writer.default_origin = "kernel"
    for i in range(3):
        source = service.writer.append(ZfEvent(type='human.escalate', correlation_id='RUN',
            payload={'decision_token': f'gate-{i}', 'reason': 'stage replan cap exhausted'}))
    before = log.read_all()
    assert build_no_progress_projection(before)['status'] == 'tripped'
    assert _exec(service, 'human-decision-dismiss', {'decision_token': 'gate-2'})['ok']
    return service, log, source, before


def test_controlled_dismiss_removes_only_exact_occurrence(tmp_path):
    service, log, source, before = dismissed(tmp_path)
    assert build_no_progress_projection(log.read_all())['status'] == 'clear'
    assert build_no_progress_projection(before)['status'] == 'tripped'
    service.writer.append(ZfEvent(type=source.type, correlation_id='RUN', payload=source.payload))
    current = build_no_progress_projection(log.read_all())
    assert current['status'] == 'tripped' and current['items'][0]['count'] == 3
    assert build_no_progress_projection(log.read_all()) == current


@pytest.mark.parametrize('defect', ['external', 'other_run', 'wrong_source', 'not_dismissed'])
def test_unproven_dismissal_cannot_clear_breaker(tmp_path, defect):
    _, log, _, _ = dismissed(tmp_path)
    events = deepcopy(log.read_all())
    ack = next(e for e in events if e.type == 'human.escalation.acknowledged')
    if defect == 'external':
        ack.origin = 'external'
    elif defect == 'other_run':
        ack.correlation_id = 'OTHER'
    elif defect == 'wrong_source':
        ack.payload['escalation_event_id'] = 'missing'
    else:
        ack.payload['status'] = 'pending'
    assert build_no_progress_projection(events)['status'] == 'tripped'
