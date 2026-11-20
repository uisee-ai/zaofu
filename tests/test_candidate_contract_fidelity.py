import pytest
from zf.runtime.candidate_verification_authority import _aggregate_contract_snapshot, CandidateVerificationAuthorityError


def aggregate(commands):
    return _aggregate_contract_snapshot([{'task_id': f'T{i}', 'verification_commands': [c]}
        for i, c in enumerate(commands)], workflow_run_id='run', task_id='candidate',
        contract_revision='r1', task_map_generation='g1', base_commit='base', task_ref='candidate',
        candidate_event_id='event', freeze_descriptor={}, child_authorities=[], package_identity={})


def test_same_command_keeps_all_producer_paths_and_nondeterministic_flag():
    command = {'command_id': 'c', 'command_digest': 'a'*64, 'command': 'browser', 'tier': 'e2e'}
    result = aggregate([{**command, 'producer_paths': ['trace.zip']},
                        {**command, 'producer_paths': ['video.webm'], 'deterministic': False}])
    row = result['verification_commands'][0]
    assert row['producer_paths'] == ['trace.zip', 'video.webm']
    assert row['deterministic'] is False
    assert result['verification_tiers'] == ['e2e']


def test_conflicting_command_tiers_cannot_be_silently_coalesced():
    command = {'command_id': 'c', 'command_digest': 'a'*64, 'command': 'browser'}
    with pytest.raises(CandidateVerificationAuthorityError, match='conflicting'):
        aggregate([{**command, 'tier': 'e2e'}, {**command, 'tier': 'integration'}])


def test_mixed_commands_keep_their_original_verification_tiers():
    result = aggregate([
        {'command_id': 'api', 'command_digest': 'a'*64, 'command': 'api-test',
         'tier': 'integration', 'producer_paths': ['api.json']},
        {'command_id': 'browser', 'command_digest': 'b'*64, 'command': 'browser-test',
         'tier': 'e2e', 'producer_paths': ['trace.zip', 'video.webm']},
    ])
    assert result['verification_tier'] == 'integration'  # Candidate stage identity.
    assert [(row['tier'], row['producer_paths']) for row in result['verification_commands']] == [
        ('integration', ['api.json']), ('e2e', ['trace.zip', 'video.webm']),
    ]
