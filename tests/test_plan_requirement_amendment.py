"""Owner quote edits revise bound requirements without weakening other claims."""
import copy
import hashlib
import json

import pytest

from tests.test_plan_artifact_review import review
from tests.test_workflow_preview_review import Agent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_preview_review import PreviewReviewError


@pytest.fixture
def bound_review(review):
    client, service, artifact, _ = review
    body = '# Plan\n\nDefault is world.\nOther constraints stay.\n'
    artifact.write_text(body)
    requirement = {'schema_version': 'requirement-spec.v1', 'request_id': 'req-1',
                   'revision': 3, 'confirmed': True, 'objective': 'Default is world.',
                   'acceptance': ['R01: Default is world.', 'R02: Unicode is preserved.']}
    task_map = {'schema_version': 'task-map.v1', 'goal_claims': [
        {'goal_claim_id': 'R01', 'text': 'Default is world.', 'mandatory': True},
        {'goal_claim_id': 'R02', 'text': 'Unicode is preserved.', 'mandatory': True},
    ], 'tasks': []}
    refs = {}
    for name, value in [('requirement', requirement), ('task-map', task_map)]:
        path = service.state_dir / 'artifacts' / f'{name}.json'
        path.write_text(json.dumps(value))
        refs[name] = {'ref': str(path.relative_to(service.state_dir)),
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    service.writer.emit('plan.approval.requested', payload={
        'plan_id': 'P1', 'workflow_run_id': 'run-1', 'trace_id': 'run-1',
        'plan_artifact_ref': 'artifacts/plan.md',
        'plan_artifact_digest': hashlib.sha256(body.encode()).hexdigest(),
        'task_map_ref': refs['task-map']['ref'], 'task_map_digest': refs['task-map']['sha256'],
        'requirement_spec_ref': refs['requirement']['ref'],
        'requirement_spec_digest': refs['requirement']['sha256'],
    })
    changes = {'requirement': [
        {'path': '/objective', 'before': 'Default is world.', 'after': 'Default is driver.'},
        {'path': '/acceptance/0', 'before': 'R01: Default is world.', 'after': 'R01: Default is driver.'},
    ], 'claims': [{'goal_claim_id': 'R01', 'before': 'Default is world.', 'after': 'Default is driver.'}]}
    return service, body, requirement, task_map, changes


def propose(bound_review, changes=None):
    service, body, _, _, original = bound_review
    quote = 'Default is world.'
    return service.discuss({'plan_id': 'P1', 'name': 'accepted_plan', 'mode': 'chat',
        'message': 'Change the default to driver.',
        'quote': {'text': quote, 'start': body.index(quote), 'end': body.index(quote) + len(quote)}},
        agent=Agent({'answer': 'Change the default.', 'replacement': 'Default is driver.',
                     'contract_changes': original if changes is None else changes}))


def test_owner_apply_binds_exact_requirement_revision_and_replays_once(bound_review):
    service, _, old_requirement, _, changes = bound_review
    draft = propose(bound_review)
    assert draft['contract_changes'] == changes
    assert not any(e.type == 'plan.rejected' for e in service.writer.event_log.read_all())
    applied = service.apply(draft['draft_ref'])
    assert applied['ok'], applied
    assert service.apply(draft['draft_ref'])['replayed']
    events = [e for e in service.writer.event_log.read_all() if e.type == 'plan.rejected']
    assert len(events) == 1
    event = events[0]
    assert event.actor == 'operator'
    amendment = hydrate_sidecar_ref(service.state_dir, event.payload['requirement_amendment_ref']).payload
    current = hydrate_sidecar_ref(service.state_dir, amendment['requirement']).payload
    assert current['revision'] == 4
    assert current['objective'] == 'Default is driver.'
    assert current['acceptance'] == ['R01: Default is driver.', old_requirement['acceptance'][1]]
    before = hydrate_sidecar_ref(service.state_dir, amendment['previous_requirement']).payload
    assert before == old_requirement
    from zf.runtime.plan_requirement_amendment import amended_task_map_baseline
    baseline = amended_task_map_baseline(service.state_dir, 'run-1', bound_review[3])
    assert baseline['goal_claims'][0]['text'] == 'Default is driver.'
    assert baseline['goal_claims'][1] == bound_review[3]['goal_claims'][1]
    from zf.runtime.plan_candidate_preflight import _mandatory_goal_claim_contract_errors
    assert _mandatory_goal_claim_contract_errors(previous=baseline, current=baseline) == []
    weak = copy.deepcopy(baseline)
    weak['goal_claims'][1]['mandatory'] = False
    assert _mandatory_goal_claim_contract_errors(previous=baseline, current=weak)
    assert amended_task_map_baseline(service.state_dir, 'other-run', bound_review[3]) == bound_review[3]
    from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
    refs = build_plan_handoff_input_refs(state_dir=service.state_dir, project_root=service.project_root,
        payload={'workflow_run_id': 'run-1', 'prd_ref': 'artifacts/requirement.json',
                 'target_ref': 'artifacts/requirement.json'})
    assert any(r['ref'] == amendment['requirement']['ref'] for r in refs)
    assert any(r['source_id'] == 'owner-requirement-amendment' for r in refs)
    assert len([r for r in refs if r['source_id'] == 'requirement']) == 1
    from zf.runtime.goal_claim_set import build_goal_claim_set, mandatory_claim_continuity
    from zf.runtime.plan_requirement_amendment import amended_claim_set_baseline
    old_set = build_goal_claim_set(bound_review[3], workflow_run_id='run-1', goal_id='goal',
        task_map_generation='old', objective=old_requirement)
    new_set = build_goal_claim_set(baseline, workflow_run_id='run-1', goal_id='goal',
        task_map_generation='new', objective=current)
    assert mandatory_claim_continuity(old_set, new_set)['status'] == 'rejected'
    assert mandatory_claim_continuity(amended_claim_set_baseline(service.state_dir, 'run-1', old_set),
                                     new_set)['status'] == 'passed'


@pytest.mark.parametrize('fault', ['stale_requirement', 'stale_claim', 'outside_field', 'duplicate', 'claim_removal'])
def test_contract_changes_fail_closed(bound_review, fault):
    changes = copy.deepcopy(bound_review[4])
    if fault == 'stale_requirement':
        changes['requirement'][0]['before'] = 'old stale text'
    elif fault == 'stale_claim':
        changes['claims'][0]['before'] = 'old stale text'
    elif fault == 'outside_field':
        changes['requirement'][0] = {'path': '/confirmed', 'before': True, 'after': False}
    elif fault == 'duplicate':
        changes['claims'].append(copy.deepcopy(changes['claims'][0]))
    else:
        changes['claims'][0]['after'] = ''
    with pytest.raises(PreviewReviewError):
        propose(bound_review, changes)
    assert not any(e.type == 'plan.rejected' for e in bound_review[0].writer.event_log.read_all())


def test_stale_plan_cannot_authorize_requirement_amendment(bound_review):
    service = bound_review[0]
    draft = propose(bound_review)
    service.writer.emit('plan.approved', payload={'plan_id': 'P1'})
    with pytest.raises(PreviewReviewError):
        service.apply(draft['draft_ref'])
    assert not any(e.payload.get('requirement_amendment_ref') for e in service.writer.event_log.read_all())


def test_plan_package_binds_owner_revision_even_with_old_inline_requirement(bound_review):
    from zf.runtime.plan_artifact_package import prepare_plan_artifact_package
    from zf.runtime.run_contract import write_run_contract, stable_json_sha256
    from zf.runtime.plan_requirement_amendment import current_owner_amendment, amended_task_map_baseline

    service, _, original, previous_map, _ = bound_review
    service.apply(propose(bound_review)['draft_ref'])
    amendment = current_owner_amendment(service.state_dir, 'run-1')
    path = service.state_dir / 'artifacts/new-task-map.json'
    path.write_text(json.dumps(amended_task_map_baseline(service.state_dir, 'run-1', previous_map)))
    contract = {'schema_version': 'run-contract.v1', 'workflow': {'kind': 'prd'}}
    write_run_contract(service.state_dir, {**contract, 'contract_digest': stable_json_sha256(contract)})
    package, _, _ = prepare_plan_artifact_package(
        state_dir=service.state_dir, project_root=service.project_root,
        events=service.writer.event_log.read_all(), workflow_run_id='run-1',
        flow_kind='prd', producer_stage_id='prd-plan', goal_id='goal',
        metadata={'artifact_package': {'mode': 'blocking'}},
        payload={'task_map_ref': str(path), 'plan_ports': [
            {'logical_name': 'requirement_spec', 'body': original}]})
    port = next(p for p in package['produced'] if p['logical_name'] == 'requirement_spec')
    assert port['ref'] == amendment['requirement']['ref']
    assert port['sha256'] == amendment['requirement']['sha256']


def test_agent_event_cannot_authorize_an_owner_contract_amendment(bound_review):
    from zf.runtime.plan_requirement_amendment import current_owner_amendment
    from zf.core.events.model import ZfEvent

    service, *_ = bound_review
    service.apply(propose(bound_review)['draft_ref'])
    accepted = next(e for e in service.writer.event_log.read_all() if e.type == 'plan.rejected')
    forged = ZfEvent(type=accepted.type, actor='planner', payload=accepted.payload)
    assert current_owner_amendment(service.state_dir, 'run-1', events=[forged]) == {}


def test_owner_revision_uses_intake_requirement_instead_of_derived_prd(bound_review):
    service, _, original, _, _ = bound_review
    path = service.state_dir / 'artifacts/intake.json'
    path.write_text(json.dumps({**original, 'revision': 10}))
    service.writer.emit('workflow.invoke.requested', payload={
        'request_id': 'run-1', 'requirement_spec_ref': 'artifacts/intake.json',
        'requirement_spec_digest': hashlib.sha256(path.read_bytes()).hexdigest()})
    result = propose(bound_review)
    service.apply(result['draft_ref'])
    from zf.runtime.plan_requirement_amendment import current_owner_amendment
    amendment = current_owner_amendment(service.state_dir, 'run-1')
    assert hydrate_sidecar_ref(service.state_dir, amendment['requirement']).payload['revision'] == 11


def test_tampered_requirement_is_a_typed_conflict_before_agent_dispatch(bound_review):
    service, *_ = bound_review
    (service.state_dir / 'artifacts/requirement.json').write_text('{}')
    with pytest.raises(PreviewReviewError, match='digest mismatch') as error:
        propose(bound_review)
    assert error.value.status == 409


def test_replan_marker_preserves_owner_revision_over_stale_recovery_payload(bound_review):
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.plan_requirement_amendment import current_owner_amendment

    service = bound_review[0]
    service.apply(propose(bound_review)['draft_ref'])
    source = next(e for e in service.writer.event_log.read_all() if e.type == 'plan.rejected')
    amendment = current_owner_amendment(service.state_dir, 'run-1')
    requested = service.writer.emit('control.action.requested', actor='operator', payload={})
    result = ControlledActionService(service.state_dir, service.writer, actor='zf-cli').execute(
        action='candidate-rework-apply', requested_action='candidate-rework-apply', requested=requested,
        payload={'pdd_id': 'P1', 'workflow_run_id': 'run-1', 'trace_id': 'run-1',
                 'candidate_rework_action': 'replan', 'source_event_id': source.id,
                 'source_event_type': 'plan.rejected', 'requirement_spec_ref': 'artifacts/requirement.json'})
    assert result['ok'], result
    marker = next(e for e in service.writer.event_log.read_all() if e.type == 'orchestrator.replan_requested')
    assert marker.payload['requirement_spec_ref'] == amendment['requirement']['ref']
    assert marker.payload['requirement_amendment_ref'] == amendment['descriptor']
