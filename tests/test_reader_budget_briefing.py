"""Reader briefing must show the budget guard's admitted operation limits."""
import pytest
from zf.core.config.schema import (
    ExecutionConfig, ExecutionProfileConfig, ExecutionProfileLimitsConfig,
    FanoutAggregateConfig, ProjectConfig, RoleConfig, WorkflowConfig, ZfConfig,
)
from zf.core.events.log import EventLog
from zf.runtime.fanout import FanoutContext
from zf.runtime.orchestrator import Orchestrator


def test_reader_briefing_uses_authoritative_operation_limits(tmp_path, monkeypatch):
    state = tmp_path / '.zf'
    state.mkdir()
    role = RoleConfig(name='issue-triage', backend='mock', role_kind='reader',
                      execution=ExecutionConfig(default_profile='bounded-direct-v1',
                                                profile_allowlist=['bounded-direct-v1']))
    config = ZfConfig(project=ProjectConfig(name='test'), roles=[role],
        workflow=WorkflowConfig(execution_profiles={'bounded-direct-v1': ExecutionProfileConfig(
            limits=ExecutionProfileLimitsConfig(max_usage_samples=60, cost_budget_usd=8))}))
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir, orch.project_root, orch.config = state, tmp_path, config
    orch.event_log = EventLog(state / 'events.jsonl')
    calls = []
    def effective(state_dir, log, operation_id):
        calls.append((state_dir, log, operation_id))
        return {'max_usage_samples': 0, 'timeout_seconds': 0, 'token_budget': 0, 'cost_budget_usd': 8}
    monkeypatch.setattr('zf.runtime.workflow_budget_status.effective_operation_limits', effective)
    context = FanoutContext(fanout_id='fanout', stage_id='issue-triage', topology='fanout_reader',
                            trace_id='run', trigger_event_id='trigger', target_ref='issue.md', expected_children=[])
    path = orch._write_fanout_briefing(role=role, context=context, child_id='triage', run_id='attempt',
        aggregate=FanoutAggregateConfig(mode='wait_for_all', child_success_event='issue.triage.child.completed',
            child_failure_event='issue.triage.child.failed', success_event='task_map.ready', failure_event='issue.triage.failed'),
        child_payload={'task_id': 'TASK-1', 'operation_id': 'operation', 'attempt_id': 'attempt',
                       'flow_kind': 'issue', 'trigger_payload': {'flow_kind': 'issue'}},
        effective_operation_id='operation')
    text = path.read_text()
    assert calls == [(state, orch.event_log, 'operation')]
    assert 'max_usage_samples=`0`' in text
    assert 'Finish within `60`' not in text
    assert 'cost_budget_usd=`8`' in text


def test_durable_reader_dispatch_passes_its_registered_operation(tmp_path, monkeypatch):
    from tests.test_reader_fanout_runtime import _durable_reader_state

    calls = []
    def effective(state, log, operation_id):
        calls.append(operation_id)
        return {'max_usage_samples': 0, 'timeout_seconds': 0, 'token_budget': 0, 'cost_budget_usd': 8}
    monkeypatch.setattr('zf.runtime.workflow_budget_status.effective_operation_limits', effective)
    _, log, transport, _, _, child = _durable_reader_state(tmp_path)
    dispatched = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched')
    assert calls == [dispatched.payload['operation_id']]
    assert child['payload']['operation_id'] == calls[0]
    briefing = transport.sent[0][1].read_text()
    assert 'max_usage_samples=`0`' in briefing
    assert 'Finish within' not in briefing


def test_reader_retry_reuses_effective_budget_of_existing_operation(tmp_path, monkeypatch):
    from tests.test_reader_fanout_runtime import _durable_reader_state, _manifest

    state, log, transport, orch, config, child = _durable_reader_state(tmp_path)
    first = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched')
    operation_id = first.payload['operation_id']
    manifest = _manifest(state, first.payload['fanout_id'])
    child['payload']['semantic_result_submit_mode'] = 'blocking'
    child['payload']['execution_profile_id'] = 'bounded-direct-v1'
    child['payload'].pop('execution_profile_digest', None)
    role = config.roles[0]
    role.execution = ExecutionConfig(default_profile='bounded-direct-v1',
                                     profile_allowlist=['bounded-direct-v1'])
    config.workflow.execution_profiles['bounded-direct-v1'] = ExecutionProfileConfig(
        limits=ExecutionProfileLimitsConfig(max_usage_samples=60, cost_budget_usd=8))
    calls = []

    def effective(_state, _log, operation):
        calls.append(operation)
        return {'max_usage_samples': 0, 'timeout_seconds': 0, 'token_budget': 0, 'cost_budget_usd': 8}

    monkeypatch.setattr('zf.runtime.workflow_budget_status.effective_operation_limits', effective)
    monkeypatch.setattr(orch, '_ensure_fanout_role_dispatchable', lambda **_kwargs: True)
    assert orch._retry_fanout_child(manifest=manifest, child=child,
                                    previous_dispatch=first, attempt=1)
    assert calls == [operation_id]
    briefing = transport.sent[-1][1].read_text()
    assert 'max_usage_samples=`0`' in briefing
    assert 'Finish within `60`' not in briefing
    assert 'cost_budget_usd=`8`' in briefing


@pytest.mark.parametrize('field', ['fanout_id', 'child_id', 'role_instance', 'trace_id', 'operation_id'])
def test_reader_retry_does_not_borrow_limits_from_unbound_dispatch(tmp_path, monkeypatch, field):
    from tests.test_reader_fanout_runtime import _durable_reader_state, _manifest

    state, log, transport, orch, config, child = _durable_reader_state(tmp_path)
    first = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched')
    manifest = _manifest(state, first.payload['fanout_id'])
    child['payload']['semantic_result_submit_mode'] = 'blocking'
    child['payload']['execution_profile_id'] = 'bounded-direct-v1'
    child['payload'].pop('execution_profile_digest', None)
    first.payload[field] = '' if field == 'operation_id' else 'unrelated'
    role = config.roles[0]
    role.execution = ExecutionConfig(default_profile='bounded-direct-v1',
                                     profile_allowlist=['bounded-direct-v1'])
    config.workflow.execution_profiles['bounded-direct-v1'] = ExecutionProfileConfig(
        limits=ExecutionProfileLimitsConfig(max_usage_samples=60, cost_budget_usd=8))
    calls = []
    monkeypatch.setattr('zf.runtime.workflow_budget_status.effective_operation_limits',
                        lambda _state, _log, operation: calls.append(operation))
    monkeypatch.setattr(orch, '_ensure_fanout_role_dispatchable', lambda **_kwargs: True)

    assert orch._retry_fanout_child(manifest=manifest, child=child,
                                    previous_dispatch=first, attempt=1)
    assert calls == []
    assert 'max_usage_samples=`60`' in transport.sent[-1][1].read_text()
