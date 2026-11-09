"""Gap amendments must retain supplied task declarations at the real writer boundary."""
from copy import deepcopy
import json

import pytest

from zf.runtime.module_gap_plan import write_gap_task_map_amend_artifact
from zf.runtime.writer_fanout_admission import writer_task_items


def test_published_gap_preserves_authored_context_and_writer_capabilities(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'base.json').write_text(json.dumps({'schema_version': 'task-map.v1', 'tasks': []}))
    declarations = {
        'description': 'Business write and authorization lock share the same transaction.',
        'source_excerpt': 'A rejected write must leave all business rows unchanged.',
        'source_ref': 'requirements.md#transaction',
        'owner_decision_ids': ['Q-transaction'],
        'lane_affinity': 'ledger',
        'skills_required': ['zf-harness-done-contract'],
        'explicit_non_goals': ['Do not replace final real-provider verification.'],
        'exclusive_files': ['src/ledger/service.py'],
        'verification': {'commands': ['pytest -q'], 'scope': 'isolated_branch'},
    }
    raw = {
        'task_id': 'LEDGER', 'title': 'Ledger', 'owner_role': 'dev',
        'claim_paths': ['src/ledger/', 'evidence/ledger/'],
        'acceptance_criteria': [{'id': 'AC-1', 'statement': 'Atomic write', 'mandatory': True,
                                'verification_owner': 'task_verify', 'verification_tier': 'runtime',
                                'verification_command_ids': ['CHECK']}],
        'verify_commands': ['pytest -q'], 'source_refs': ['event:observed-gap'],
        'validation': {'commands': [{'id': 'CHECK', 'command': 'pytest -q',
                                    'acceptance_ids': ['AC-1'], 'owner': 'task_verify', 'tier': 'runtime',
                                    'deterministic': True, 'reusable': True, 'timeout_seconds': 60,
                                    'rolling_smoke': True}]},
        **deepcopy(declarations),
    }
    before = deepcopy(raw)
    result = write_gap_task_map_amend_artifact(
        state_dir=state, project_root=tmp_path, base_task_map_ref='base.json',
        pdd_id='P', source_event_id='gap-1', gap_tasks=[raw],
    )
    published = json.loads((state / result['task_map_ref']).read_text())
    task = published['tasks'][0]
    assert {key: task.get(key) for key in declarations} == declarations
    writer = writer_task_items(published)[0]
    assert writer['skills_required'] == declarations['skills_required']
    assert writer['exclusive_files'] == declarations['exclusive_files']
    assert writer['source_excerpt'] == declarations['source_excerpt']
    assert writer['source_ref'] == declarations['source_ref']
    assert writer['raw_task']['description'] == declarations['description']
    assert raw == before


@pytest.mark.parametrize('value', [[], ['zf-harness-done-contract', 'zf-harness-done-contract']])
def test_gap_declaration_preservation_keeps_explicit_empty_and_invalid_values(value):
    from zf.runtime.module_gap_plan import build_gap_task_map_amend

    gap = {'task_id': 'NEW', 'claim_paths': ['src/'], 'acceptance': ['works'],
           'verify_commands': ['pytest -q'], 'source_refs': ['event:gap'],
           'skills_required': value}
    amended = build_gap_task_map_amend(
        {'schema_version': 'task-map.v1', 'tasks': []}, gap_tasks=[gap], supersedes_task_map_ref='old.json')
    assert amended['tasks'][0].get('skills_required') == value
    if value:
        # Preserve the authored declaration; admission must still reject duplicates.
        with pytest.raises(RuntimeError, match='duplicate'):
            writer_task_items(amended)
    else:
        assert writer_task_items(amended)[0]['skills_required'] == []
