import pytest

from zf.web.issue_lifecycle import IssueLifecycleProjection, stage_for_state


def test_active_states_map_to_real_stage():
    assert stage_for_state('triaging') == 'triage'
    assert stage_for_state('fixing') == 'fix'
    assert stage_for_state('failed', 'fix') == 'fix'
    assert stage_for_state('owner_rejected') == 'delivery'


def test_only_observed_previous_stage_can_be_completed():
    projection = IssueLifecycleProjection()
    projection.observe('mirrored', 'pr_open', 'pr-recorded')
    value = projection.to_dict()
    assert value['current'] == 'delivery'
    assert value['stages']['triage']['outcome'] == 'unknown'
    assert value['stages']['fix']['outcome'] == 'unknown'
    assert value['stages']['repository']['outcome'] == 'unknown'


def test_records_exact_state_before_completion():
    projection = IssueLifecycleProjection()
    transitions = [('mirrored','mirrored'), ('mirrored','triage_queued'), ('triage_queued','triaging'), ('triaging','awaiting_fix_approval'), ('awaiting_fix_approval','fix_queued'), ('fix_queued','fixing')]
    for i,(before,after) in enumerate(transitions):
        projection.observe(before, after, str(i))
    stages = projection.to_dict()['stages']
    assert stages['triage']['state'] == 'triaging'
    assert stages['triage']['outcome'] == 'completed'
    assert stages['decision']['state'] == 'awaiting_fix_approval'
    assert stages['decision']['outcome'] == 'completed'
    assert stages['fix']['outcome'] == 'active'
    assert stages['delivery']['outcome'] == 'unknown'


def test_retry_does_not_imply_old_approval_is_current():
    projection = IssueLifecycleProjection()
    projection.observe('mirrored','triage_queued','queued')
    projection.observe('triage_queued','needs_info','needs')
    projection.observe('needs_info','triage_queued','retry')
    stages = projection.to_dict()['stages']
    assert stages['triage']['outcome'] == 'active'
    assert stages['decision']['state'] == ''
    assert stages['decision']['outcome'] == 'unknown'
    assert stages['decision']['evidence_refs'] == ['needs']


def test_failure_and_cancel_do_not_mark_stage_complete():
    projection = IssueLifecycleProjection()
    projection.observe('mirrored','triage_queued','queued')
    projection.observe('triage_queued','triage_cancelled','cancel')
    assert projection.to_dict()['stages']['triage']['outcome'] == 'attention'
    projection.observe('triage_cancelled','triage_queued','queued-again')
    projection.observe('triage_queued','failed','failure')
    assert projection.to_dict()['current'] == 'triage'
    assert projection.to_dict()['stages']['triage']['outcome'] == 'failed'


def test_repository_mirror_is_evidence_without_received_event():
    projection = IssueLifecycleProjection()
    projection.record_repository('open', 'github:repository:37')
    projection.observe('mirrored', 'triage_queued', 'queued-37')
    projection.observe('triage_queued', 'triaging', 'admitted-37')
    stages = projection.to_dict()['stages']
    assert stages['repository']['state'] == 'open'
    assert stages['repository']['outcome'] == 'completed'
    assert stages['repository']['evidence_refs'] == ['github:repository:37']
    assert stages['triage']['outcome'] == 'active'
    assert stages['decision']['outcome'] == 'unknown'


def test_mirror_does_not_invent_skipped_stages():
    projection = IssueLifecycleProjection()
    projection.record_repository('closed', 'github:repository:37')
    projection.observe('mirrored', 'pr_approved', 'review-37')
    stages = projection.to_dict()['stages']
    assert stages['repository']['outcome'] == 'completed'
    assert stages['repository']['state'] == 'closed'
    assert all(stages[stage]['outcome'] == 'unknown' for stage in ('triage', 'decision', 'fix'))


def test_observed_full_flow_has_completed_stages_and_exact_states():
    projection = IssueLifecycleProjection()
    projection.record_repository('open', 'github:repository:37')
    before = 'mirrored'
    for i, state in enumerate(['triage_queued', 'triaging', 'awaiting_fix_approval', 'fix_queued', 'fixing', 'verifying', 'verified_candidate', 'approved_for_pr', 'publication_prepared', 'pr_open', 'pr_approved', 'merged']):
        projection.observe(before, state, f'event-{i}')
        before = state
    assert all(stage['outcome'] == 'completed' for stage in projection.to_dict()['stages'].values())
    assert projection.to_dict()['stages']['triage']['state'] == 'triaging'
    assert projection.to_dict()['stages']['fix']['state'] == 'verifying'
    assert projection.to_dict()['stages']['delivery']['state'] == 'pr_approved'


def test_invalid_repository_seed_is_not_completion_evidence():
    projection = IssueLifecycleProjection()
    projection.record_repository('open', '')
    projection.observe('mirrored', 'triage_queued', 'queued')
    assert projection.to_dict()['stages']['repository']['outcome'] == 'unknown'


@pytest.mark.parametrize('github_state', ['open', 'closed'])
def test_receipt_preserves_upstream_state_after_triage_cancellation(github_state):
    projection = IssueLifecycleProjection()
    projection.record_repository(github_state, 'github:repository:37')
    projection.observe('mirrored', 'mirrored', 'received')
    projection.observe('mirrored', 'triage_queued', 'queued')
    projection.observe('triage_queued', 'triage_cancelled', 'cancelled')
    stages = projection.to_dict()['stages']
    assert stages['repository']['state'] == github_state
    assert stages['repository']['outcome'] == 'completed'
    assert stages['triage']['outcome'] == 'attention'
    assert all(stages[s]['outcome'] == 'unknown' for s in ('decision', 'fix', 'delivery', 'complete'))
