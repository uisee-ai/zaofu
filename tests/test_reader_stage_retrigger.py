"""Controlled recovery follows registered reader triggers across flow families."""
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from tests.test_control_actions_recovery import _req, _service


@pytest.mark.parametrize('topology,expected', [('fanout_reader', True), ('fanout_writer', False)])
def test_registered_reader_trigger_recovery_is_bounded(tmp_path, topology, expected):
    service = _service(tmp_path)
    service.config = SimpleNamespace(workflow=SimpleNamespace(stages=[
        SimpleNamespace(id='custom-plan', topology=topology, trigger='custom.scan.completed'),
    ]))
    source = service.writer.append(ZfEvent(
        type='custom.scan.completed', actor='zf-cli', task_id='T1',
        correlation_id='run-custom', payload={'artifact_refs': ['scans/scan.json']},
    ))
    request = _req({})
    result = service.execute(action='stage-retrigger', requested_action='stage-retrigger',
                             payload={'source_event_id': source.id}, requested=request)
    assert result['ok'] is expected
    if expected:
        retrigger = next(e for e in service.writer.event_log.read_all()
                         if e.payload.get('redrive_of') == source.id)
        assert retrigger.payload == {**source.payload, 'redrive_of': source.id, 'rework_of': source.id}
        assert retrigger.task_id == source.task_id
        assert retrigger.correlation_id == source.correlation_id
        assert retrigger.causation_id == request.id
        repeat = service.execute(action='stage-retrigger', requested_action='stage-retrigger',
                                 payload={'source_event_id': source.id}, requested=_req({}))
        assert not repeat['ok']
        assert 'already retriggered' in repeat['reason']
