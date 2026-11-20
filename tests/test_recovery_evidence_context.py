"""The resident can reconcile facts without relying on disabled recovery routes."""
from unittest.mock import patch
from tests.test_run_manager import _config, _state
from zf.runtime.run_manager import _execute_resident_agent_reprompt
from tests.test_reader_stage_recovery import recovery, execute


def test_actual_resident_diagnosis_exposes_disabled_routes(tmp_path):
    state, log, writer = _state(tmp_path)
    config = _config()
    config.runtime.run_manager.reflect.enabled = False
    config.runtime.run_manager.source_repair.enabled = False
    action = {'request_kind': 'recovery_diagnosis', 'request_id': 'diag-evidence',
              'task_id': 'T1', 'workflow_run_id': 'R1', 'checkpoint_id': 'failed-reader'}
    # No tmux provider is started. Context creation precedes the delivery gate.
    _execute_resident_agent_reprompt(state_dir=state, writer=writer, config=config,
        project_root=tmp_path, action=action, causation_id='failure')
    import json
    body = json.loads((state / 'diagnostics/run-manager/diag-evidence/context.json').read_text())
    caps = body['recovery_capabilities']
    assert caps['reflect'] is False
    assert caps['source_repair'] is False
    assert caps['resident_read_only_reconciliation'] is True
    assert caps['human_gate_bypass'] is False


def test_resident_method_does_not_require_another_agent_for_read_only_reconciliation(tmp_path):
    from zf.core.config.schema import RoleConfig
    from zf.runtime.run_manager_resident import build_resident_run_manager_briefing
    briefing = build_resident_run_manager_briefing(project_root=tmp_path, state_dir=tmp_path / 'state',
        role=RoleConfig(name='run-manager'))
    assert 'recovery_capabilities' in briefing
    assert '证据不可用不等于执行失败' in briefing


def test_owner_scope_review_is_bound_to_completed_controlled_retry(recovery):
    from zf.runtime.continuation_evidence import _owner_recovery_reviews
    service, failure, _ = recovery
    service.writer.default_origin = 'kernel'
    feedback = 'Continue product acceptance; do not claim a fresh unattended qualification.'
    assert execute(service, failure.id, operator_feedback=[feedback])['ok']
    events = service.writer.event_log.read_all()
    reviews = _owner_recovery_reviews(events, 'run-1')
    assert len(reviews) == 1
    assert reviews[0]['operator_feedback'] == [feedback]
    assert _owner_recovery_reviews(events, 'other-run') == []
    assert _owner_recovery_reviews([e for e in events if e.type != 'runtime.action.completed'], 'run-1') == []


def test_broken_evidence_still_reaches_the_diagnostician(tmp_path):
    import json
    state, log, writer = _state(tmp_path)
    with patch('zf.runtime.continuation_evidence.continuation_input_refs',
               side_effect=ValueError('historical digest mismatch')):
        _execute_resident_agent_reprompt(state_dir=state, writer=writer, config=_config(),
            project_root=tmp_path, action={'request_kind': 'recovery_diagnosis',
                'request_id': 'broken-evidence', 'workflow_run_id': 'R1'}, causation_id='failed')
    body = json.loads((state / 'diagnostics/run-manager/broken-evidence/context.json').read_text())
    assert body['continuation_evidence_error'] == 'historical digest mismatch'
    assert body['continuation_evidence_refs'] == []
