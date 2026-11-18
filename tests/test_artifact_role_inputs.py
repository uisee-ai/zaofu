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


def test_replan_planner_sees_one_executable_draft_command(tmp_path):
    import shlex
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    source = dict(attempt_id="current", plan_artifact_package_digest="a" * 64,
                  sources=[{"source_id": name} for name in ("plan-rework-package", "baseline-plan-port-task_map",
                                                            "baseline-plan-port-source_index")])
    descriptor = write_immutable_json_sidecar(tmp_path, source, root="attempts", kind="manifest",
                                              schema_version="attempt-source-manifest.v1", created_by="test")
    payload = dict(attempt_id="current", attempt_source_manifest_ref=descriptor["ref"],
                   attempt_source_manifest_digest=descriptor["sha256"])
    rendered = render_attempt_source_briefing(payload, state_dir=tmp_path, role_name="planner")
    cmd = next(l.split("`")[1] for l in rendered.splitlines() if "draft-baseline" in l)
    assert shlex.split(cmd)[-9:] == ["plan", "draft-baseline", "--workspace", "--attempt", "current",
                                     "--port", "task_map", "--port", "source_index"]
    assert not any(ch in cmd for ch in "|><;")
    assert "draft-baseline" not in render_attempt_source_briefing(payload, state_dir=tmp_path, role_name="critic")
    assert "draft-baseline" not in render_attempt_source_briefing({**payload, "attempt_id": "stale"},
                                                                  state_dir=tmp_path, role_name="planner")
