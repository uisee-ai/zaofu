import pytest
from zf.runtime.artifact_role_inputs import role_input_view
from zf.runtime.artifact_read_ledger import render_attempt_source_briefing,source_manifest_from_payload


@pytest.mark.parametrize('profile,scope', [
    ('implementation','task'),('task-verify','task'),('integration-acceptance-review','task_integration'),
    ('candidate-verify','candidate'),('thin-judge-goal-closure','goal'),('legacy','declared_manifest'),
])
def test_common_first_dispatch_entry_respects_bound_profile_and_required_reads(tmp_path,profile,scope):
    (tmp_path/'input.json').write_text('{"content":"private full body"}')
    manifest,ref=source_manifest_from_payload(state_dir=tmp_path,project_root=tmp_path,
        payload={'output_profile_id':profile, 'input_refs':[{'ref':'input.json','source_id':'input'}]},
        workflow_run_id='run',task_id='T1',attempt_id='a',dispatch_id='d',
        manifest_metadata={'context_policy':{'output_profile_id':profile}})
    payload={'attempt_id':'a','attempt_source_manifest_ref':ref['ref'],
             'attempt_source_manifest_sha256':ref['sha256'],'required_reads':[
                 {'source_id':'input','artifact_id':'input.json','json_path':'$'}]}
    text=render_attempt_source_briefing(payload,state_dir=tmp_path)
    assert f'`{profile}` / `{scope}`' in text
    assert 'artifact index --attempt a' in text and 'artifact costs' in text
    assert 'artifact read --attempt a --required' in text
    assert 'private full body' not in text
    assert role_input_view(manifest)['authorizes_scope_change'] is False
    assert role_input_view(manifest)['selection_supported'] == (profile in {'implementation','task-verify'})
    payload['attempt_id']='another'
    assert 'binding mismatch' in render_attempt_source_briefing(payload,state_dir=tmp_path)
    payload.pop('attempt_source_manifest_sha256')
    assert 'scope unavailable' in render_attempt_source_briefing(payload,state_dir=tmp_path)
