"""Request identities and source references preserve the intake ownership boundary."""
from pathlib import Path

from zf.runtime.workflow_request_identity import request_source_ref, stable_request_id


def test_bound_channel_ref_wins_over_colliding_project_file(tmp_path: Path):
    state = tmp_path / 'runtime-state'
    ref = 'channels/ch/prd.json'
    for root in (state, tmp_path):
        path = root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('different content')
    payload = {'source_ref': ref, 'task_input_contract': {
        'source_ref': ref, 'evidence_contract': {'channel_id': 'ch'},
    }}
    assert request_source_ref(tmp_path, state, payload) == ref
    # A file reference without the exact Channel binding follows normal lookup.
    payload['source_ref'] = 'other.json'
    (state / 'other.json').write_text('{}')
    assert request_source_ref(tmp_path, state, payload) == str(state / 'other.json')


def test_unbound_source_priority_fallback_and_missing_input(tmp_path: Path):
    state = tmp_path / 'custom-state'
    state.mkdir()
    (state / 'source.md').write_text('state')
    payload = {'artifact_ref': 'source.md'}
    assert request_source_ref(tmp_path, state, payload) == str(state / 'source.md')
    (tmp_path / 'source.md').write_text('project')
    assert request_source_ref(tmp_path, state, payload) == str(tmp_path / 'source.md')
    assert request_source_ref(tmp_path, state, {}) == ''
    assert request_source_ref(tmp_path, state, {'source_ref': 'missing.md'}) == 'missing.md'


def test_replay_identity_is_stable_but_isolates_project_request_and_objective(tmp_path: Path):
    original = stable_request_id(tmp_path, 'event-1', 'fix login')
    assert stable_request_id(tmp_path / '.', 'event-1', 'fix login') == original
    assert original not in {
        stable_request_id(tmp_path / 'other', 'event-1', 'fix login'),
        stable_request_id(tmp_path, 'event-2', 'fix login'),
        stable_request_id(tmp_path, 'event-1', 'explain login'),
    }
