"""Plan input limits and terminal reader recovery at their real callers."""
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from tests.test_call_result_runtime import _runtime
from tests.test_reader_respawn_continuation import _running_recovered_synth
from zf.core.config.loader import ConfigError, _build_fanout_aggregate
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.core.events.model import ZfEvent


def test_synth_timeout_closes_operation_without_semantic_rejection(tmp_path):
    _, log, runtime, _, dispatch, _ = _running_recovered_synth(tmp_path)
    runtime._now = lambda: time.time() + 100000
    runtime._check_fanout_synth_timeout(
        fanout_id=dispatch.payload['fanout_id'],
        manifest=runtime._fanout_manifest(dispatch.payload['fanout_id']),
        events=log.read_all(), timeout_seconds=1,
    )
    for _ in range(2):
        runtime._recover_unrecorded_reader_fanout_results()
    operation = reduce_workflow_operations(log.read_all())[dispatch.payload['operation_id']]
    assert operation['status'] == 'failed'
    failed = [e for e in log.read_all() if e.type == 'workflow.operation.failed'
              and e.payload['operation_id'] == operation['operation_id']]
    assert len(failed) == 1
    terminal = next(e for e in log.read_all() if e.type == 'fanout.synth.completed')
    assert terminal.payload['failure_kind'] == 'infra'
    assert terminal.payload['semantic_attempt_consumed'] is False


def test_budget_failure_from_actual_synth_dispatch_is_infrastructure(tmp_path):
    from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
    from zf.runtime.failure_kind import failure_kind_from_payload
    _, log, runtime, fanout = _task_map_synth_orchestrator(tmp_path, complete_child=False)
    runtime.config.workflow.stages[0].aggregate.review_input_max_bytes = 1
    runtime.run_once(events=[ZfEvent(type='task_map.child.completed', actor='source-reader',
        correlation_id='trace-product', payload={'fanout_id': fanout, 'child_id': 'source-reader',
            'run_id': f'run-{fanout}-source-reader', 'status': 'completed', 'summary': 'read'})])
    assert not any(e.type == 'fanout.synth.dispatched' for e in log.read_all())
    failure = next(e for e in log.read_all() if e.type == 'task_map.blocked')
    assert failure_kind_from_payload(failure.payload) == 'infra'
    assert failure.payload['plan_review_optimization']['preflight_status'] == 'blocked'
    assert failure.payload['evidence_refs']


@pytest.mark.parametrize('field', ['run_id', 'request_hash', 'attempt_id'])
def test_synth_failure_cannot_close_a_different_dispatch(tmp_path, field):
    from zf.runtime.reader_terminal_recovery import reconcile_exhausted_reader_operations
    _, log, runtime, _, dispatch, _ = _running_recovered_synth(tmp_path)
    payload = {**dispatch.payload, field: 'different'}
    log.append(ZfEvent(type='fanout.synth.dispatched', actor='zf-cli', payload=payload))
    log.append(ZfEvent(type='fanout.synth.completed', actor='zf-cli', payload={
        'fanout_id': dispatch.payload['fanout_id'], 'run_id': dispatch.payload['run_id'],
        'status': 'failed', 'reason': 'synth_timeout'}))
    assert not reconcile_exhausted_reader_operations(runtime, log.read_all())
    assert reduce_workflow_operations(log.read_all())[dispatch.payload['operation_id']]['status'] == 'running'


def test_review_inspection_does_not_create_read_evidence(tmp_path):
    from zf.runtime.plan_review_inspection import inspect_review
    _, log, runtime, _, dispatch, _ = _running_recovered_synth(tmp_path)
    before = {str(p): p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}
    report = inspect_review(runtime.state_dir, dispatch.payload['run_id'], log.read_all(), config=runtime.config)
    assert report['read_only'] and report['read_progress']['proves_understanding'] is False
    assert report['read_progress']['required'] > 0
    assert report['read_progress']['covered'] == 0
    assert before == {str(p): p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}


def test_scope_inspection_reports_unrelated_contract_and_global_changes():
    from copy import deepcopy
    from zf.runtime.plan_review_inspection import compare_task_scope
    old = {'target_commit': 'a', 'tasks': [{'id': 'A'}, {'id': 'B', 'allowed_paths': ['one']}]}
    new = deepcopy(old)
    new['target_commit'] = 'b'
    new['tasks'][1]['allowed_paths'] = ['two']
    report = compare_task_scope(old, new, ['A'])
    assert report['outside_requested_sample'] == ['B']
    assert report['global_changed_fields'] == ['target_commit']
    assert report['scope_certificate'] == 'not_established_by_structural_comparison'


@pytest.mark.parametrize('value', [-1, True, 1.2, '1024'])
def test_review_input_budget_rejects_ambiguous_configuration(value):
    with pytest.raises(ConfigError):
        _build_fanout_aggregate({'review_input_max_bytes': value})


def test_large_critic_input_is_rejected_before_operation_registration(tmp_path):
    from zf.runtime.plan_review_input_budget import PlanReviewInputBudgetExceeded

    runtime = _runtime(tmp_path)
    aggregate = _build_fanout_aggregate({'review_input_max_bytes': 1024})
    runtime.config.workflow.stages = [SimpleNamespace(id='plan', aggregate=aggregate)]
    path = runtime.project_root / 'source.json'
    path.write_text(json.dumps({'history': 'x' * 4096}))
    payload = {'workflow_run_id': 'run-1', 'role_instance': 'critic',
               'output_profile_id': 'plan-synth', 'input_refs': [{
                   'source_id': 'child-artifact-planner-1', 'ref': str(path),
                   'artifact_id': path.name,
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
    with pytest.raises(PlanReviewInputBudgetExceeded) as raised:
        prepare_call_operation(runtime, payload=payload, operation_type='fanout_synth',
                               operation_key='critic', stage_id='plan', task_id='',
                               dispatch_id='attempt-1')
    assert raised.value.diagnostic['after']['distinct_selected_bytes'] > 1024
    assert not any(e.type == 'workflow.operation.requested' for e in runtime.event_log.read_all())


@pytest.mark.parametrize('limits,size,unknown,status', [
    ({}, 99999999, 1, 'not_configured'),
    ({'review_input_max_pages': 1}, 8192, 0, 'passed'),
    ({'review_input_max_pages': 1}, 8193, 0, 'blocked'),
    ({'review_input_max_bytes': 8192}, 1, 1, 'blocked'),
])
def test_review_budget_boundaries(limits, size, unknown, status):
    from zf.runtime.plan_review_input_budget import check_review_input_budget, PlanReviewInputBudgetExceeded
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[
        SimpleNamespace(id='plan', aggregate=_build_fanout_aggregate(limits))]))
    diagnostic = {'after': {'distinct_selected_bytes': size, 'unknown_rows': unknown,
                            'minimum_page_count': (size + 8191) // 8192}}
    if status == 'blocked':
        with pytest.raises(PlanReviewInputBudgetExceeded):
            check_review_input_budget(config, 'plan', diagnostic)
    else:
        check_review_input_budget(config, 'plan', diagnostic)
    assert diagnostic['preflight_status'] == status


def test_operator_review_cli_is_read_only(tmp_path, monkeypatch, capsys):
    from zf.cli.main import main
    import zf.cli.plan_review_inspection as cli
    _, log, runtime, _, dispatch, _ = _running_recovered_synth(tmp_path)
    operation = reduce_workflow_operations(log.read_all())[dispatch.payload['operation_id']]
    stage = next(s for s in runtime.config.workflow.stages if s.id == operation['parent_stage_id'])
    stage.aggregate.review_input_max_bytes = 1
    monkeypatch.setattr(cli, 'resolve_project_context', lambda **kwargs:
                        SimpleNamespace(state_dir=runtime.state_dir, config=runtime.config))
    monkeypatch.setattr(cli, 'event_log_from_project', lambda *args, **kwargs: log)
    before = {str(p): p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}
    assert main(['plan', 'inspect-review', '--attempt', dispatch.payload['run_id']]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['read_only'] and report['read_progress']['covered'] == 0
    assert report['diagnostic']['budget_config_basis'] == 'current_operator_config'
    assert report['diagnostic']['limits']['review_input_max_bytes'] == 1
    assert report['diagnostic']['preflight_status'] == 'blocked'
    assert before == {str(p): p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}


def test_worker_origin_cannot_close_synth_operation(tmp_path):
    from zf.runtime.reader_terminal_recovery import reconcile_exhausted_reader_operations
    _, log, runtime, _, dispatch, _ = _running_recovered_synth(tmp_path)
    log.append(ZfEvent(type='fanout.synth.completed', actor='zf-cli', origin='worker', payload={
        'fanout_id': dispatch.payload['fanout_id'], 'run_id': dispatch.payload['run_id'],
        'status': 'failed', 'reason': 'synth_timeout'}))
    assert not reconcile_exhausted_reader_operations(runtime, log.read_all())
    assert reduce_workflow_operations(log.read_all())[dispatch.payload['operation_id']]['status'] == 'running'


@pytest.mark.parametrize('limit', [4194304, -1, True])
def test_prd_flow_yaml_binds_review_limits_to_plan_only(tmp_path, limit):
    import yaml
    from zf.core.config.loader import load_config
    path = tmp_path / 'zf.yaml'
    path.write_text(yaml.safe_dump_all([{'apiVersion': 'zaofu.dev/v1', 'kind': 'PrdFlow',
        'metadata': {'name': 'review-budget'}, 'spec': {'lanes': 8, 'backend': 'codex',
        'prdRef': 'PRD.md', 'reviewInputMaxBytes': limit, 'reviewInputMaxPages': 512}},
        {'apiVersion': 'zaofu.dev/v1', 'kind': 'ZfConfig',
         'metadata': {'name': 'review-budget'}, 'spec': {'project': {'name': 'review-budget'}}}]))
    if type(limit) is not int or limit < 0:
        with pytest.raises(ConfigError, match='nonnegative integer'):
            load_config(path)
        return
    config = load_config(path)
    plan = next(s for s in config.workflow.stages if s.id == 'prd-plan')
    assert plan.aggregate.review_input_max_bytes == limit
    assert plan.aggregate.review_input_max_pages == 512
    assert all(s.aggregate.review_input_max_bytes == 0 for s in config.workflow.stages if s.id != 'prd-plan')
