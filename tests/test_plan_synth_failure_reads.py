"""A Critic can report an execution failure without claiming unread evidence."""

import pytest

from tests.test_result_submit import _runtime
from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path, materialize_attempt_source_ref, read_attempt_artifact,
)
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.orchestrator_fanout import FanoutCoordinationMixin
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _setup(tmp_path):
    runtime = _runtime(tmp_path)
    token = provision_role_submit_credential(runtime.state_dir, 'plan-critic').read_text().strip()
    source = runtime.project_root / 'required.json'
    source.write_text('{"input":"must be consumed for a conclusion"}')
    ref = materialize_attempt_source_ref(state_dir=runtime.state_dir,
        project_root=runtime.project_root, ref=str(source), source_id='plan-synth-contract')
    prepared = prepare_call_operation(runtime, payload={
        'workflow_run_id': 'run-plan', 'role_instance': 'plan-critic',
        'fanout_id': 'fanout-plan', 'stage_id': 'plan', 'child_id': 'synth',
        'run_id': 'attempt-plan-synth', 'input_refs': [ref],
        'canonical_success_event': 'fanout.synth.completed',
        'canonical_failure_event': 'fanout.synth.completed',
        'output_profile_id': 'plan-synth', 'output_profile_revision': '1',
        'plan_revision': 'plan-r1', 'plan_synth_contract_ref': 'artifacts/contracts/plan-r1.json',
        'plan_synth_contract_digest': 'a' * 64, 'reviewed_plan_candidate_digest': 'a' * 64,
    }, operation_type='fanout_synth', operation_key='synth@plan', stage_id='plan',
       task_id='', dispatch_id='attempt-plan-synth')
    mark_call_operation_started(runtime, prepared, task_id='', dispatch_id='attempt-plan-synth')
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, token, ref


def _failure():
    return {
        'status': 'failed', 'execution_status': 'failed',
        'failure_class': 'reader_execution_failure',
        'recommendation': 'needs_rework', 'summary': 'Could not consume required inputs.',
        'report': {'status': 'blocked', 'recommendation': 'needs_rework',
                   'summary': 'Could not consume required inputs.', 'findings': []},
    }


@pytest.mark.parametrize('partial_read', [False, True])
def test_failed_critic_records_incomplete_input_without_passing(tmp_path, partial_read, monkeypatch):
    runtime, prepared, service, token, ref = _setup(tmp_path)
    if partial_read:
        read_attempt_artifact(runtime.state_dir,
            manifest={'attempt_id': 'attempt-plan-synth', 'sources': [ref]},
            source_id=ref['source_id'], artifact_id=ref['artifact_id'], page_start=0, page_size=5)
    submitted = service.submit(operation_id=prepared.operation_id, role_instance='plan-critic',
                               credential=token, semantic_result=_failure())
    assert submitted.canonical_event_type == 'fanout.synth.completed'
    events = runtime.event_log.read_all()
    admitted = next(e for e in events if e.type == 'workflow.call.result.admitted')
    envelope = hydrate_sidecar_ref(runtime.state_dir, admitted.payload['envelope_ref']).payload
    assert envelope['execution']['status'] == 'failed'
    assert envelope['input_consumption']['status'] == 'incomplete'
    result = hydrate_sidecar_ref(runtime.state_dir, admitted.payload['control_result_ref']).payload
    assert result['verdict'] != 'passed'
    canonical = next(e for e in events if e.id == submitted.canonical_event_id)
    manifest = {'fanout_id': 'fanout-plan', 'trace_id': 'run-plan', 'stage_id': 'plan',
                'aggregate_config': {'success_event': 'task_map.ready',
                                     'failure_event': 'prd.plan.failed'}}
    monkeypatch.setattr(runtime, '_fanout_manifest', lambda _: manifest, raising=False)
    monkeypatch.setattr(runtime, '_fanout_identity_stale_reason', lambda _: ('', ''), raising=False)
    monkeypatch.setattr(runtime, '_fanout_flow_identity_payload', lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(runtime, '_fanout_aggregate_started', lambda _: True, raising=False)
    monkeypatch.setattr('zf.runtime.research_delivery.settle_research_delivery', lambda *a: None)
    FanoutCoordinationMixin._finalize_fanout_synth(runtime, canonical)
    events = runtime.event_log.read_all()
    assert any(e.type == 'prd.plan.failed' for e in events)
    assert not any(e.type == 'task_map.ready' for e in events)


@pytest.mark.parametrize('change', [
    {'status': 'completed', 'execution_status': 'completed'},
    {'status': 'completed'},
    {'failure_class': 'product_rejection'},
    {'execution_status': 'completed'},
    {'recommendation': 'approve', 'report': {
        'status': 'passed', 'recommendation': 'approve', 'summary': 'Contradictory approval.'}},
])
def test_critic_semantic_judgment_still_requires_reads(tmp_path, change):
    runtime, prepared, service, token, _ = _setup(tmp_path)
    semantic = _failure() | change
    with pytest.raises(ResultSubmitError, match='required_read_missing'):
        service.submit(operation_id=prepared.operation_id, role_instance='plan-critic',
                       credential=token, semantic_result=semantic)


def test_failed_critic_cannot_admit_corrupt_read_ledger(tmp_path):
    runtime, prepared, service, token, _ = _setup(tmp_path)
    path = active_read_ledger_path(runtime.state_dir, 'attempt-plan-synth')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('corrupt ledger\n')
    with pytest.raises(ResultSubmitError, match='not admitted'):
        service.submit(operation_id=prepared.operation_id, role_instance='plan-critic',
                       credential=token, semantic_result=_failure())
