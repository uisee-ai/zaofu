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


@pytest.mark.parametrize("source_state", ["current", "superseded", "external", "external_wrapper"])
def test_candidate_retrigger_preserves_only_current_native_freeze(tmp_path, source_state):
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

    service = _service(tmp_path)
    body = {"workflow_run_id": "run", "schema_version": "candidate-freeze-receipt.v1",
            "freeze_id": "freeze", "target_commit": "a" * 40}
    source = service.writer.append(ZfEvent(type="candidate.ready", payload=body,
        origin="external" if source_state == "external" else "kernel"))
    if source_state == "external_wrapper":
        source = service.writer.append(ZfEvent(type="candidate.ready", origin="external",
            payload={"workflow_run_id": "run", "candidate_snapshot_event_id": source.id}))
    if source_state == "superseded":
        service.writer.append(ZfEvent(type="candidate.ready", origin="kernel",
            payload={**body, "freeze_id": "next"}))
    result = service.execute(action="stage-retrigger", requested_action="stage-retrigger",
        requested=_req({}), payload={"source_event_id": source.id})
    events = service.writer.event_log.read_all()
    if source_state != "current":
        assert not result["ok"]
        assert not any(e.payload.get("redrive_of") for e in events)
        return
    retry = next(e for e in events if e.id == result["retriggered_event_id"])
    assert "schema_version" not in retry.payload
    assert retry.payload["candidate_snapshot_event_id"] == source.id
    assert retry.payload["target_commit"] == source.payload["target_commit"]
    assert frozen_candidate_for_payload(events, "run", payload={"trigger_payload": retry.payload}).id == source.id
    assert frozen_candidate_for_payload(events, "run", payload={}).id == source.id
