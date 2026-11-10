"""Exercise the fake boundary against the production Plan contract."""
from pathlib import Path

import pytest

from tests.e2e.prd_ui.provider import reply
from tests.e2e.prd_ui.worker import candidate
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.source_index_gate import evaluate_source_index_gate


def test_ui_provider_writes_prd_only_when_user_requests_it(tmp_path: Path):
    reply('User message:\nUI_PRD_DISCUSS', tmp_path)
    assert not (tmp_path / 'docs').exists()
    reply('User message:\nUI_PRD_ANSWER', tmp_path)
    text = (tmp_path / 'docs/prd/ui-requirement.md').read_text()
    assert 'README.md' in text and 'missing file' in text
    assert not (tmp_path / '.zf').exists()
    assert not (tmp_path / '.ui-state').exists()


def test_unknown_provider_turn_fails_instead_of_fabricating_success(tmp_path: Path):
    with pytest.raises(ValueError, match='unrecognized'):
        reply('User message:\nunknown request', tmp_path)


def test_scripted_plan_meets_preflight_and_keeps_state_root(tmp_path: Path):
    (tmp_path / 'zf.yaml').write_text('version: "1.0"\nproject:\n  name: fixture\n  state_dir: isolated-state\n')
    trigger = {'flow_kind': 'prd', 'prd_ref': 'docs/prd/ui-requirement.md'}
    result = candidate(tmp_path, {'plan_candidate_validation': {'manifest': {'trigger_payload': trigger}}}, 'op-1')
    state = tmp_path / 'isolated-state'
    assert (state / result['task_map_ref']).is_file()
    assert (state / result['plan_artifact_ref']).read_text() == result['plan_md']
    assert Path(result['source_index_ref']).is_file()
    assert not (state / 'events.jsonl').exists()
    assert not (state / 'kanban.json').exists()
    evaluated = evaluate_plan_candidate_preflight(
        state_dir=state, project_root=tmp_path, reports=[{'report': result}],
        manifest={'trigger_payload': trigger}, metadata={'flow_kind': 'prd'},
    )
    assert not evaluated['errors'], evaluated
    anchors = evaluate_source_index_gate(task_items=result['task_map']['tasks'],
                                        source_index=result['source_index'], findings=None, harness_profile='strict')
    assert anchors.passed
