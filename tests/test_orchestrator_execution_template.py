"""The OA example must bind stage labels to existing Plan tasks."""
import json
from types import SimpleNamespace

from tests import test_orchestrator_agent_run_plan as fixture
from zf.runtime.orchestrator_agent_briefing import build_orchestrator_agent_operation_briefing
from zf.runtime.orchestrator_agent_execution_policy import execution_decision_rejection


def test_generated_pre_impl_example_has_admissible_task_and_stage_selectors(tmp_path, monkeypatch):
    runtime, trigger, loaded, _, _ = fixture._runtime(tmp_path)
    _, prepared = fixture._checkpoint(runtime, trigger, loaded)
    build_orchestrator_agent_operation_briefing(state_dir=runtime.state_dir, prepared=prepared)
    template = json.loads((runtime.state_dir / prepared.result_scratch_ref).read_text())
    unit = template['run_plan']['graph']['work_units'][0]
    # Fill the example's selectors as the agent would, without inventing new keys.
    assert 'task_ids' in unit and 'stage_ids' in unit
    unit.update(work_unit_id='implementation', task_ids=['TASK-1'], stage_ids=['impl'])
    runtime.config.workflow.orchestration.autonomous_actions = ['delegate']
    context = {'autonomous_actions': ['delegate'], 'plan_tasks': [{'task_id': 'TASK-1'}],
               'task_bindings': {}}
    monkeypatch.setattr('zf.runtime.orchestrator_agent_execution_policy.hydrate_sidecar_ref',
        lambda _state, ref: SimpleNamespace(payload=(
            {'execution_context_ref': 'context'} if ref == 'input' else context)))
    request = {'checkpoint': 'pre_impl', 'checkpoint_input_ref': 'input'}
    assert execution_decision_rejection(runtime, decision=template, request=request) == ''
    unit['task_ids'] = ['UNPLANNED-TASK']
    assert execution_decision_rejection(runtime, decision=template, request=request) == 'execution_task_outside_plan'
    unit.pop('task_ids')
    unit.pop('stage_ids')
    assert execution_decision_rejection(runtime, decision=template, request=request) == 'execution_task_outside_plan'
