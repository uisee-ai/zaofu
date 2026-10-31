"""Reader findings must survive the downstream fanout report contract."""
import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.fanout import validate_fanout_report
from zf.runtime.plan_candidate_submit_validation import validate_plan_candidate_semantic
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.workflow_read_result import normalize_workflow_read_result
from tests.test_plan_candidate_preflight import _PlanSynthHarness, _source_index, _task_map
from tests.test_result_submit import _running_workflow_read_operation


@pytest.mark.parametrize('finding', [
    {'id': 'SUP01', 'severity': 'info', 'detail': 'Cold setup is wired.'},
    {'message': '  '}, {'message': 42}, {'message': 'gap', 'severity': 'invented'},
    {'message': 'gap', 'path': 42}, 'gap', None,
])
def test_reader_rejects_findings_that_fanout_cannot_project(finding):
    raw = {'status': 'passed', 'summary': 'Reader completed.', 'findings': [finding]}
    downstream = validate_fanout_report(raw, child_id='planner')
    assert not downstream.valid
    _, issues = normalize_workflow_read_result(ZfEvent(type='prd.plan.child.completed', payload={
        'result_semantics': 'artifact_production', 'report': raw,
    }))
    assert issues
    assert [x['message'] for x in issues] == downstream.diagnostics


def test_plan_validate_rejects_bad_findings_without_writing_state(tmp_path):
    semantic = {'task_map': _task_map(), 'source_index': _source_index(),
                'source_index_ref': 'inline:source-index', 'plan_md': '# Plan',
                'findings': [{'id': 'SUP02', 'detail': 'Partitions are bound.'}]}
    state = tmp_path / 'state'
    result = validate_plan_candidate_semantic(state_dir=state, project_root=tmp_path,
        profile_id='workflow-read', semantic=semantic, request={'plan_candidate_validation': {
            'manifest': {'trigger_payload': {'flow_kind': 'prd', 'prd_ref': 'requirements.md'}},
            'metadata': {'flow_kind': 'prd'},
        }})
    assert result['status'] == 'failed', result
    assert any('findings[0].message' in x.get('message', '') for x in result['errors'])
    assert not state.exists()


def test_bad_finding_blocks_actual_critic_dispatch(tmp_path):
    harness = _PlanSynthHarness(tmp_path, [{'child_id': 'planner', 'report': {
        'task_map': _task_map(), 'source_index': _source_index(),
        'source_index_ref': 'inline:source-index', 'plan_md': '# Plan',
        'findings': [{'id': 'SUP01', 'detail': 'Cold setup is wired.'}],
    }}])
    harness._dispatch_fanout_synth('fanout-plan', {
        'fanout_id': 'fanout-plan', 'trace_id': 'run-plan', 'stage_id': 'issue-triage',
        'trigger_event_id': 'evt-input', 'children': [],
        'trigger_payload': {'flow_kind': 'issue', 'issue_ref': 'docs/issue.md'},
        'aggregate_config': {'success_event': 'task_map.ready',
            'failure_event': 'issue.plan.failed', 'synth_role': 'plan-critic'},
    }, 'wait_for_all', 'plan-critic')
    assert not harness.critic_dispatch_attempted
    assert harness.finalized.payload['failure_class'] == 'plan_candidate_preflight'
    assert 'findings[0].message' in str(harness.finalized.payload)


def test_submit_rejects_bad_finding_and_allows_corrected_retry(tmp_path):
    runtime, prepared, service, token = _running_workflow_read_operation(tmp_path)
    raw = {'status': 'passed', 'summary': 'Complete scan.', 'recommendation': 'approve',
           'findings': [{'id': 'SUP01', 'severity': 'warning', 'detail': 'Keep the budget caveat.'}]}
    with pytest.raises(ResultSubmitError) as rejected:
        service.submit(operation_id=prepared.operation_id, semantic_result=raw,
                       role_instance='scan-verification', credential=token)
    assert 'findings[0].message' in str(rejected.value.details)
    assert not any(e.type == 'workflow.child.completed' for e in runtime.event_log.read_all())

    raw['findings'][0]['message'] = raw['findings'][0].pop('detail')
    submitted = service.submit(operation_id=prepared.operation_id, semantic_result=raw,
                               role_instance='scan-verification', credential=token)
    event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    report = hydrate_profiled_control_result_event(runtime.state_dir, event).payload['report']
    projected = validate_fanout_report(report, child_id='scan-verification')
    assert projected.valid
    assert projected.report['status'] == 'passed'
    assert projected.report['findings'][0]['message'] == 'Keep the budget caveat.'
    assert projected.report['findings'][0]['severity'] == 'medium'


@pytest.mark.parametrize('finding', [
    {'message': 'gap', 'severity': 'high', 'evidence_refs': ['artifacts/proof.json']},
    {'summary': 'gap', 'severity': 'warning'},
    {'description': 'gap'}, {'reason': 'gap'}, {'observed_gap': 'gap'},
])
def test_valid_finding_aliases_preserve_artifact_producer_verdict(finding):
    report, issues = normalize_workflow_read_result(ZfEvent(type='workflow.child.completed', payload={
        'result_semantics': 'artifact_production', 'summary': 'Scan complete.',
        'recommendation': 'needs_rework', 'findings': [finding],
    }))
    assert issues == []
    assert report['verdict'] == 'passed'
    assert report['subject_verdict'] == 'needs_rework'
    assert report['findings'] == [finding]
    assert validate_fanout_report(report, child_id='reader').valid
