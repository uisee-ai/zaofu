"""Local review and truthful task input failure at their production entrypoints."""
from copy import deepcopy

import pytest

from tests.test_result_submit import _runtime
from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.result_submit import SemanticResultSubmitService, ResultSubmitError, provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.plan_local_review import scoped_port_paths


def _unread_impl(tmp_path):
    runtime = _runtime(tmp_path)
    token = provision_role_submit_credential(runtime.state_dir, 'dev-1').read_text().strip()
    source = runtime.project_root / 'input.json'
    source.write_text('{"contract":"input not consumed"}')
    ref = materialize_attempt_source_ref(state_dir=runtime.state_dir, project_root=runtime.project_root,
        ref=str(source), source_id='critical-input')
    payload = {'workflow_run_id': 'run-1', 'role_instance': 'dev-1', 'run_id': 'attempt-1',
        'task_id': 'T1', 'output_profile_id': 'implementation', 'input_refs': [ref],
        'canonical_success_event': 'dev.build.done', 'canonical_failure_event': 'dev.blocked'}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type='fanout_writer_child',
        operation_key='dev-1-T1', stage_id='impl', task_id='T1', dispatch_id='attempt-1')
    mark_call_operation_started(runtime, prepared, task_id='T1', dispatch_id='attempt-1')
    service = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
        event_writer=runtime.event_writer)
    result = {'execution_status': 'failed', 'verdict': 'blocked', 'failure_class': 'reader_execution_failure',
        'blocker_kind': 'reader_execution_failure', 'target_commit': 'abc123',
        'summary': 'Required input cannot be consumed within this attempt.',
        'evidence_refs': [str(source)], 'self_check': {}, 'changed_files': [], 'known_gaps': []}
    return runtime, service, dict(operation_id=prepared.operation_id, role_instance='dev-1', credential=token), result


def test_unread_impl_can_report_infra_failure_without_success(tmp_path):
    runtime, service, args, result = _unread_impl(tmp_path)
    submitted = service.submit(**args, semantic_result=result)
    assert submitted.canonical_event_type == 'dev.blocked'
    events = runtime.event_log.read_all()
    admitted = next(e for e in events if e.type == 'workflow.call.result.admitted')
    envelope = hydrate_sidecar_ref(runtime.state_dir, admitted.payload['envelope_ref']).payload
    assert envelope['execution']['status'] == 'failed'
    assert envelope['input_consumption']['status'] == 'incomplete'
    assert not any(e.type == 'dev.build.done' for e in events)


@pytest.mark.parametrize('fault', ['passed', 'product', 'no_evidence', 'no_summary', 'wrong_identity', 'bad_ledger'])
def test_input_failure_cannot_bypass_success_or_identity_gates(tmp_path, fault):
    runtime, service, args, result = _unread_impl(tmp_path)
    if fault == 'passed': result.update(execution_status='completed', verdict='passed')
    if fault == 'product': result.update(failure_class='product_rejection', verdict='rejected')
    if fault == 'no_evidence': result['evidence_refs'] = []
    if fault == 'no_summary': result['summary'] = ''
    if fault == 'wrong_identity': args['role_instance'] = 'another-role'
    if fault == 'bad_ledger':
        from zf.runtime.artifact_read_ledger import active_read_ledger_path
        path = active_read_ledger_path(runtime.state_dir, 'attempt-1')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schema_version":"artifact-read.v1","attempt_id":"another-attempt"}\n')
    with pytest.raises(ResultSubmitError): service.submit(**args, semantic_result=result)
    assert not any(e.type == 'dev.build.done' for e in runtime.event_log.read_all())


def _native():
    old_map = {'schema_version': 'task-map.v1', 'tasks': [
        {'task_id': 'A', 'blocked_by': []}, {'task_id': 'B', 'blocked_by': ['A']},
        {'task_id': 'C', 'blocked_by': []}]}
    new_map = deepcopy(old_map);new_map['tasks'][0]['title'] = 'repair A'
    old = {'schema_version': 'execution-spec.v1', 'shared': {'interface': 'v1'},
        'rows': [{'owner': key, 'body': key} for key in ['A', 'B', 'C']],
        'task_input_policy': {'schema_version': 'task-input-policy.v1',
            'shared_paths': ['$.schema_version', '$.shared'],
            'tasks': [{'task_id': key, 'paths': [f'$.rows[{i}]']} for i,key in enumerate(['A','B','C'])]}}
    new = deepcopy(old);new['rows'][0]['body'] = 'repair'
    return old_map, new_map, old, new


def test_local_review_supports_native_declared_partitions():
    om,nm,op,np = _native()
    result = scoped_port_paths(old_map=om,new_map=nm,old_port=op,new_port=np,affected_task_ids=['A'])
    assert result == (['$.schema_version','$.shared','$.rows[0]','$.rows[1]'],)*2


@pytest.mark.parametrize('fault', ['outside','shared','policy','uncovered'])
def test_native_local_review_fails_closed(fault):
    om,nm,op,np = _native()
    if fault == 'outside': np['rows'][2]['body'] = 'unrelated change'
    if fault == 'shared': np['shared']['interface'] = 'v2'
    if fault == 'policy': np['task_input_policy']['tasks'][2]['paths'] = ['$.rows[0]']
    if fault == 'uncovered': np['extra'] = 'unassigned obligation'
    assert scoped_port_paths(old_map=om,new_map=nm,old_port=op,new_port=np,affected_task_ids=['A']) is None


@pytest.mark.parametrize("fault", ["", "product", "passed", "wrong_target", "no_evidence"])
def test_contract_bound_verify_input_failure(tmp_path, fault):
    from tests.test_verification_submit_preflight import _running_verify

    runtime, _, service, credential, result = _running_verify(tmp_path, "task-verify")
    source = runtime.project_root / "unread.json"
    source.write_text('{"needed": true}')
    ref = materialize_attempt_source_ref(state_dir=runtime.state_dir, project_root=runtime.project_root,
        ref=str(source), source_id="unread-input")
    payload = {**result, "role_instance": "verify-1", "run_id": "unread-verify",
        "output_profile_id": "task-verify", "input_refs": [ref],
        "canonical_success_event": "verify.child.completed", "canonical_failure_event": "verify.child.failed"}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_reader_child",
        operation_key="unread-verify", stage_id="verify", task_id=result["task_id"], dispatch_id="unread-verify")
    mark_call_operation_started(runtime, prepared, task_id=result["task_id"], dispatch_id="unread-verify")
    result.update(execution_status="failed", verdict="abstained", failure_class="reader_execution_failure",
        summary="Required source delivery failed", evidence_refs=[str(source)],
        requirement_results=[], probe_receipts=[], reused_command_receipt_ids=[])
    if fault == "product": result.update(execution_status="completed", verdict="rejected", failure_class="product_rejection")
    if fault == "passed": result.update(execution_status="completed", verdict="passed")
    if fault == "wrong_target":
        (runtime.state_dir / result["target_snapshot_ref"]).write_text('{"corrupt": true}')
    if fault == "no_evidence": result["evidence_refs"] = []
    args = dict(operation_id=prepared.operation_id, role_instance="verify-1", credential=credential.read_text().strip())
    if fault:
        with pytest.raises(ResultSubmitError): service.submit(**args, semantic_result=result)
    else:
        submitted = service.submit(**args, semantic_result=result)
        assert submitted.canonical_event_type == "verify.child.failed"
        admitted = next(e for e in runtime.event_log.read_all() if e.type == "workflow.call.result.admitted")
        envelope = hydrate_sidecar_ref(runtime.state_dir, admitted.payload["envelope_ref"]).payload
        assert envelope["input_consumption"]["status"] == "incomplete"
    assert not any(e.type == "verify.child.completed" for e in runtime.event_log.read_all())



def test_planner_validate_exposes_draft_cost_without_mutating_operation(tmp_path):
    from tests.test_result_submit import _running_plan_candidate_operation, _plan_candidate

    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    before = [event.id for event in runtime.event_log.read_all()]
    result = service.validate_plan_candidate(operation_id=prepared.operation_id,
        semantic_result=_plan_candidate(), role_instance="planner", credential=token,
        project_root=runtime.project_root)
    assert result["status"] == "passed"
    cost = result["input_cost"]
    assert cost["distinct_rendered_bytes"] > 0 and cost["minimum_full_port_pages"] >= 1
    assert cost["task_count"] == 1 and cost["tasks_with_input_selection"] == []
    assert not cost["authoritative"]
    assert [event.id for event in runtime.event_log.read_all()] == before


def test_candidate_cost_distinguishes_full_estimate_and_unicode_pages():
    from zf.runtime.plan_candidate_input_cost import candidate_input_cost
    result = candidate_input_cost({}, {"native": {"text": "中" * 6000}},
        {"review_input_max_bytes": 100, "review_input_max_pages": 2})
    assert result["status"] == "review_required"
    assert result["minimum_full_port_pages"] == 2
    assert not result["authoritative"]


def test_actual_review_pages_use_characters_not_utf8_bytes():
    from types import SimpleNamespace
    from zf.runtime.plan_review_input_diagnostics import input_size
    from zf.runtime.plan_review_input_budget import check_review_input_budget
    source = {"source_id": "a", "artifact_id": "a", "sha256": "a" * 64, "read_selections": [
        {"json_path": "$", "selected_sha256": "b" * 64, "selected_bytes": 24000, "selected_chars": 8000}]}
    row = {"source_id": "a", "artifact_id": "a", "artifact_sha256": "a" * 64, "json_path": "$"}
    diagnostic = {"after": input_size({"sources": [source]}, [row])}
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[SimpleNamespace(id="plan",
        aggregate=SimpleNamespace(review_input_max_bytes=0, review_input_max_pages=1))]))
    check_review_input_budget(config, "plan", diagnostic)
    assert diagnostic["preflight_status"] == "passed" and diagnostic["minimum_page_count"] == 1
