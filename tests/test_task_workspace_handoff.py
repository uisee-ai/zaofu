"""A settled stage releases its process before the independent successor starts."""
from pathlib import Path

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.runtime.environment_usage import EnvironmentInUse
from zf.runtime.task_pipeline_runtime import _activate_task_stage_binding, TaskPipelineWaiting


@pytest.mark.parametrize('condition', ['closed', 'open_turn', 'active_operation', 'other_task', 'residual_child', 'new_activity'])
def test_stage_handoff_releases_only_its_settled_predecessor(tmp_path, monkeypatch, condition):
    runtime, predecessor, transport = _runtime(tmp_path)
    successor = RoleConfig(name='verify', instance_id='verify-1', backend='claude-code',
                           lifecycle=RoleLifecycleConfig(mode='on_demand'))
    runtime.config.roles.append(successor)
    workspace = tmp_path / 'task-workspace'
    workspace.mkdir()
    registry = RoleSessionRegistry(runtime.state_dir / 'role_sessions.yaml', project_root=str(tmp_path))
    binding = registry.bind_task_stage_session(workflow_run_id='run', task_id='T1', stage='impl',
        rework_affinity_id='map:impl', role_instance=predecessor.instance_id,
        role_config_digest='impl-digest', workspace_generation=1, placement_epoch=1, backend='claude-code')
    registry.activate_task_stage_session(binding_key=binding['binding_key'], role_instance=predecessor.instance_id)
    registry.update_instance_meta(predecessor.instance_id, active_task_workspace=str(workspace), lifecycle_state='active')
    transport.alive.add(predecessor.instance_id)
    identity = {'operation_id': 'impl-1', 'role_instance': predecessor.instance_id,
                'workflow_run_id': 'run', 'task_id': 'T1', 'task_map_generation': 'map',
                'task_pipeline_stage': 'impl', 'task_stage_session_binding': binding['binding_key']}
    runtime.event_log.append(ZfEvent(type='workflow.operation.requested', payload=identity))
    if condition != 'active_operation':
        runtime.event_log.append(ZfEvent(type='workflow.call.result.admitted', payload={**identity, 'semantic_verdict': 'passed'}))
        runtime.event_log.append(ZfEvent(type='workflow.operation.settled', payload=identity))
    if condition != 'open_turn':
        runtime.event_log.append(ZfEvent(type='provider.turn.closed', actor=predecessor.instance_id,
                                        payload={'provider_status': 'completed'}))
    if condition == 'new_activity':
        runtime.event_log.append(ZfEvent(type='agent.tool.use', actor=predecessor.instance_id))
    def idle(path):
        if predecessor.instance_id in transport.alive or condition == 'residual_child':
            raise EnvironmentInUse('process holder')
    monkeypatch.setattr('zf.runtime.environment_usage.assert_workspace_idle', idle)
    args = dict(runtime=runtime, role=successor, task=Task(id='OTHER' if condition == 'other_task' else 'T1'),
        workflow_run_id='run', task_map_generation='map', stage='verify', workspace_generation=1,
        placement_epoch=2, role_config_digest='verify-digest', project_path=workspace, workspace_root=tmp_path)
    if condition == 'closed':
        _activate_task_stage_binding(**args)
        assert transport.terminated == [predecessor.instance_id]
        assert transport.spawned[-1][0] == successor.instance_id
        assert registry.task_stage_bindings()[binding['binding_key']]['session_id'] == binding['session_id']
    else:
        with pytest.raises(TaskPipelineWaiting):
            _activate_task_stage_binding(**args)
        assert not transport.spawned
        assert transport.terminated == ([predecessor.instance_id] if condition == 'residual_child' else [])
