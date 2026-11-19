"""Only bound task completion retires an obsolete OA failure notice."""
from dataclasses import replace

import pytest

from zf.core.events import ZfEvent
from zf.runtime.run_manager import _pending_human_decisions, build_run_manager_projection


def chain():
    def event(kind, **kwargs):
        return ZfEvent(type=kind, origin='kernel', correlation_id='R1', **kwargs)
    request = event('orchestrator.semantic.failure.requested', task_id='T1', payload={
        'workflow_run_id': 'R1', 'task_id': 'T1', 'task_map_generation': 'map-1',
        'recovery_scope': 'task', 'target_stage_id': 'impl', 'target_operation_id': 'impl-1'})
    checkpoint = event('orchestrator.semantic.checkpoint.requested', payload={
        'workflow_run_id': 'R1', 'operation_id': 'oa-1', 'checkpoint': 'semantic_failure',
        'source_event_id': request.id})
    ref = {'ref': 'artifacts/decision.json', 'sha256': 'a' * 64}
    submit = event('orchestrator.semantic.decision.submitted', actor='orchestrator',
        payload={'operation_id': 'oa-1', 'control_result_ref': ref})
    # Agent submission is a proposal; only the following kernel admission
    # supplies authority. Diagnosis uses its own correlation key in production.
    submit = replace(submit, origin='')
    request = replace(request, correlation_id='diagnosis-1')
    checkpoint = replace(checkpoint, correlation_id='diagnosis-1')
    admitted = event('orchestrator.semantic.decision.admitted', actor='zf-cli', payload={
        'workflow_run_id': 'R1', 'operation_id': 'oa-1', 'checkpoint': 'semantic_failure',
        'source_event_id': request.id, 'decision_event_id': submit.id,
        'decision': 'escalate', 'status': 'admitted', 'decision_ref': ref})
    notice = event('human.escalate', actor='zf-cli', causation_id=submit.id, payload={
        'reason': 'OA semantic failure decision requires owner input: escalate',
        'orchestrator_operation_id': 'oa-1', 'orchestrator_decision_ref': ref})
    done = event('task.done', task_id='T1', payload={'workflow_run_id': 'R1',
        'task_map_generation': 'map-1', 'authority': 'task_integration_receipt',
        'integration_event_id': 'integrated-1', 'integration_receipt_digest': 'b' * 64})
    return [request, checkpoint, submit, admitted, notice, done]


def test_exact_completed_task_retires_legacy_notice_without_manual_approval():
    events = chain()
    assert len(_pending_human_decisions(events[:-1])) == 1
    assert _pending_human_decisions(events) == []
    assert events[-1].type == 'task.done'  # Pure projection; no synthetic approval.


def test_agent_submission_needs_the_exact_kernel_admission():
    events = chain()
    events[2] = replace(events[2], origin='agent')
    assert _pending_human_decisions(events) == []
    del events[3]
    assert len(_pending_human_decisions(events)) == 1


@pytest.mark.parametrize('change', ['other_task', 'other_run', 'other_map', 'worker_done',
    'wrong_authority', 'missing_receipt', 'before_notice', 'missing_admission',
    'unbound_submit', 'wrong_ref', 'wrong_checkpoint', 'run_scope', 'unknown_request',
    'agent_request', 'agent_checkpoint', 'agent_admission', 'agent_notice'])
def test_unrelated_or_unproven_evidence_preserves_owner_gate(change):
    events = chain()
    req, checkpoint, submit, admitted, notice, done = events
    if change == 'other_task': events[-1] = replace(done, task_id='T2')
    if change == 'other_run': events[-1] = replace(done, correlation_id='R2', payload={**done.payload, 'workflow_run_id': 'R2'})
    if change == 'other_map': events[-1] = replace(done, payload={**done.payload, 'task_map_generation': 'map-2'})
    if change == 'worker_done': events[-1] = replace(done, origin='worker')
    if change == 'wrong_authority': events[-1] = replace(done, payload={**done.payload, 'authority': 'worker'})
    if change == 'missing_receipt': events[-1] = replace(done, payload={**done.payload, 'integration_receipt_digest': ''})
    if change == 'before_notice': events = [*events[:4], done, notice]
    if change == 'missing_admission': events.remove(admitted)
    if change == 'unbound_submit': events[4] = replace(notice, causation_id='unknown')
    if change == 'wrong_ref': events[4] = replace(notice, payload={**notice.payload, 'orchestrator_decision_ref': {'ref': 'other'}})
    if change == 'wrong_checkpoint': events[1] = replace(checkpoint, payload={**checkpoint.payload, 'checkpoint': 'run_plan'})
    if change == 'run_scope': events[0] = replace(req, payload={**req.payload, 'recovery_scope': 'run'})
    if change == 'unknown_request': events[0] = replace(req, id='other')
    if change.startswith('agent_'):
        index = {'agent_request': 0, 'agent_checkpoint': 1, 'agent_admission': 3, 'agent_notice': 4}[change]
        events[index] = replace(events[index], origin='agent')
    assert len(_pending_human_decisions(events)) == 1


def test_later_new_notice_and_ordinary_owner_approval_remain():
    events = chain()
    fresh = replace(events[-2], id='new-notice')
    ordinary = ZfEvent(type='human.escalate', origin='kernel', payload={
        'decision_token': 'owner-release', 'blocking_scope': 'run', 'reason': 'Owner release approval'})
    pending = _pending_human_decisions([*events, fresh, ordinary])
    assert {r['event_id'] for r in pending} == {fresh.id, ordinary.id}


def test_run_manager_projection_uses_bound_retirement(tmp_path):
    from tests.test_run_manager import _config, _state
    state, log, _ = _state(tmp_path)
    for event in chain(): log.append(event)
    projection = build_run_manager_projection(state, events=log.read_all(), config=_config())
    assert projection['completion_profile']['pending_human_decisions'] == []
