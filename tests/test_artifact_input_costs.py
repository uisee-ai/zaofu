import json

import pytest
from fastapi.testclient import TestClient
from tests.test_artifact_read_snapshot import published, IDENTITY
from tests.test_artifact_read_capability import _grant
from tests.test_artifact_input_recovery import current_dispatch
from zf.cli.main import main
from zf.core.events import EventLog, ZfEvent
from zf.core.cost.tracker import CostTracker
from zf.runtime.housekeeping import apply_agent_usage_event
from zf.runtime.artifact_input_costs import input_cost_report, compare_input_costs
from zf.runtime.artifact_query import ArtifactQueryService
from zf.web.server import create_app


def test_cli_usage_accounting_keeps_navigation_body_recovery_and_provider_distinct(tmp_path, monkeypatch, capsys):
    manifest, required, _ = published(tmp_path, body={'shared': '规则', 'tasks': [{} for _ in range(8)]})
    _, writer = current_dispatch(tmp_path, required)
    _grant(tmp_path, monkeypatch, manifest=manifest, operation_id='op')
    common = ['--attempt','a','--state-dir',str(tmp_path)]
    assert main(['artifact','index',*common]) == 0
    index_output = capsys.readouterr().out
    assert main(['artifact','read',*common,'--required','--reason','initial']) == 0
    capsys.readouterr()
    assert main(['artifact','read',*common,'--required','--reason','recovery']) == 0
    capsys.readouterr()
    event = writer.append(ZfEvent(type='agent.usage', actor='dev-1', payload={
        'backend':'codex','model':'unknown-model','workflow_run_id':'r','operation_id':'op','active_attempt_id':'a',
        'usage_semantics':'delta','usage_sample_id':'sample',
        'usage':{'input_tokens':150,'cached_input_tokens':80,'output_tokens':20}}))
    tracker = CostTracker(tmp_path / 'cost.jsonl')
    apply_agent_usage_event(tracker,event)
    apply_agent_usage_event(tracker,event)
    assert main(['artifact','costs',*common]) == 0
    report=json.loads(capsys.readouterr().out)
    assert report['navigation']['observed_calls'] == 1
    assert report['navigation']['output_bytes'] == len(index_output.encode())
    assert report['read_deliveries'] == 2
    assert report['produced_body_bytes'] == 2*report['repeated_exact_body_bytes']
    assert report['read_bytes_by_reason']['recovery'] == report['repeated_exact_body_bytes']
    assert report['host_read_ms'] is not None
    assert report['paging']['truncation_repair_calls'] is None
    assert report['provider_tool_calls'] is None
    assert report['timing']['model_reasoning_ms'] is None
    assert report['provider_usage']['input_tokens'] == 70
    assert report['provider_usage']['cache_read_tokens'] == 80
    assert report['provider_usage']['sample_count'] == 1
    assert report['provider_usage']['estimated_usd'] is None
    assert report['provider_usage']['billed_usd'] is None
    api = TestClient(create_app(tmp_path, project_root=tmp_path.parent)).get('/api/attempts/a/input-costs')
    assert api.status_code == 200 and api.json() == report


def test_costs_filter_scope_and_never_attribute_another_consumer_or_unbound_usage(tmp_path):
    manifest, required, _ = published(tmp_path, aliases=True)
    _, writer = current_dispatch(tmp_path, required)
    service=ArtifactQueryService(state_dir=tmp_path,project_root=tmp_path.parent)
    report=input_cost_report(state_dir=tmp_path,manifest=manifest,identity=IDENTITY,
        events=writer.event_log.read_all(),reads=[],consumer_actor='verify-1')
    assert report['visible_sources'] == 1 and report['scope_complete'] is False
    assert report['required_input'] is None
    assert report['provider_usage'] is None and report['read_deliveries'] == 0
    assert 'alias' not in json.dumps(report)
    assert service.attempt_input_costs('a',context=service.context(role='verify'))['provider_usage'] is None


def test_cli_costs_distinguish_available_from_frozen_required_selection(tmp_path, monkeypatch, capsys):
    manifest, required, _ = published(tmp_path)
    current_dispatch(tmp_path, required[:1])
    _grant(tmp_path, monkeypatch, manifest=manifest, operation_id='op')
    assert main(['artifact', 'costs', '--attempt', 'a', '--state-dir', str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['selected_bytes_scope'] == 'all_visible_selections_not_required_reads'
    assert report['required_input']['required_rows'] == 1
    assert report['required_input']['distinct_selected_bytes'] == len('规则'.encode())
    assert report['required_input']['unknown_rows'] == 0
    assert report['selected_bytes'] > report['required_input']['distinct_selected_bytes']
    assert report['read_deliveries'] == 0 and report['body_read_evidence'] is False


def test_comparison_requires_same_material_scope_and_qualified_acceptance(tmp_path):
    manifest, required, _=published(tmp_path)
    _, writer = current_dispatch(tmp_path, required)
    def report():
        return input_cost_report(state_dir=tmp_path,manifest=manifest,identity=IDENTITY,events=writer.event_log.read_all(),reads=[],consumer_actor='dev-1')
    with pytest.raises(ValueError,match='identical'):
        compare_input_costs(report(),report())
    manifest.update(contract_revision='c1',plan_artifact_package_digest='p1')
    before=report()
    after={**before,'produced_body_bytes':100,'read_deliveries':2}
    assert compare_input_costs(before,after)['changes']['produced_body_bytes']==100
    assert compare_input_costs(before,after)['provider_savings'] is None
    for change in ({'actor':'verify-1'},{'binding_digest':'changed'},{'comparison_qualified':False}):
        with pytest.raises(ValueError):
            compare_input_costs(before,{**after,**change})


@pytest.mark.parametrize('rich_descriptor', [False, True])
def test_review_outcome_uses_exact_admitted_attempt_and_does_not_invent_quality(tmp_path, rich_descriptor):
    from tests.test_plan_local_review import _policy_fixture
    from zf.runtime.artifact_input_costs import review_outcome
    _, _, events = _policy_fixture(tmp_path, 'rejected')
    admission = next(e for e in events if e.type == 'workflow.call.result.admitted')
    if rich_descriptor:
        # Production admission retains sidecar metadata omitted by the envelope.
        admission.payload['control_result_ref'] = {
            **admission.payload['control_result_ref'], 'required': True,
            'retention': {'class': 'audit_required'}, 'source_event_id': 'old-synth'}
    operation = {'operation_id': 'old-operation', 'workflow_run_id': 'run-1'}
    outcome = review_outcome(tmp_path, events, operation, 'old-attempt')
    assert outcome['verdict'] == 'rejected'
    assert outcome['finding_status_counts'] == {'unspecified': 1}
    assert outcome['quality_attribution'] == 'reviewer_declared_not_independent_ground_truth'
    assert review_outcome(tmp_path, events, operation, 'new-attempt') is None
    assert review_outcome(tmp_path, [*events, admission], operation, 'old-attempt') == outcome
    original = dict(admission.payload['control_result_ref'])
    for key in ('ref', 'sha256', 'schema_version'):
        admission.payload['control_result_ref'] = {**original, key: 'wrong'}
        assert review_outcome(tmp_path, events, operation, 'old-attempt') is None


def test_review_selection_uses_bound_dispatch_and_retains_unknown_on_conflict():
    from copy import deepcopy
    from zf.runtime.artifact_input_costs import review_selection
    from zf.runtime.call_result_envelope import canonical_json_sha256
    manifest = {'attempt_id': 'a', 'workflow_run_id': 'r', 'sources': []}
    operation = {'operation_id': 'op', 'request_hash': 'request'}
    value = {'schema_version': 'plan-review-input-diagnostics.v1', 'mode': 'full'}
    event = ZfEvent(type='fanout.synth.dispatched', origin='kernel', payload={
        **operation, 'attempt_id': 'a', 'workflow_run_id': 'r',
        'attempt_source_manifest_digest': canonical_json_sha256(manifest),
        'plan_review_optimization': value})
    assert review_selection([event, event], operation, manifest) == value
    for key in ('operation_id', 'request_hash', 'workflow_run_id', 'attempt_id', 'attempt_source_manifest_digest'):
        invalid = deepcopy(event)
        invalid.payload[key] = 'wrong'
        assert review_selection([invalid], operation, manifest) is None
    conflict = deepcopy(event)
    conflict.payload['plan_review_optimization']['mode'] = 'incremental'
    assert review_selection([event, conflict], operation, manifest) is None
    event.origin = 'agent'
    assert review_selection([event], operation, manifest) is None
