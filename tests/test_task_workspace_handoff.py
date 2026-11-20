"""A settled stage releases its process before the independent successor starts."""
from pathlib import Path
from contextlib import ExitStack

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


@pytest.mark.parametrize('condition', ['transferred', 'unknown_holder', 'unadmitted_writer',
                                      'stale_reader_binding', 'target_occupied',
                                      'wrong_generation', 'reader_exited', 'unknown_transport'])
def test_impl_slot_reuse_preserves_active_verify_in_prior_workspace(tmp_path, monkeypatch, condition):
    """A real reader lock on T1 must not consume T2's independent Impl slot."""
    from zf.runtime.environment_usage import environment_in_use

    runtime, writer, transport = _runtime(tmp_path)
    reader = RoleConfig(name='verify', instance_id='verify-1', backend='claude-code',
                        lifecycle=RoleLifecycleConfig(mode='on_demand'))
    runtime.config.roles.append(reader)
    prior, target = tmp_path / 'T1', tmp_path / 'T2'
    prior.mkdir()
    target.mkdir()
    registry = RoleSessionRegistry(runtime.state_dir / 'role_sessions.yaml', project_root=str(tmp_path))
    for role, stage in [(writer, 'impl'), (reader, 'verify')]:
        binding = registry.bind_task_stage_session(workflow_run_id='run', task_id='T1', stage=stage,
            rework_affinity_id='map:' + stage, role_instance=role.instance_id,
            role_config_digest=stage, workspace_generation=1, placement_epoch=1, backend='claude-code')
        registry.activate_task_stage_session(binding_key=binding['binding_key'], role_instance=role.instance_id)
        registry.update_instance_meta(role.instance_id, active_task_workspace=str(prior),
                                      lifecycle_state='active' if stage == 'verify' else 'suspended')
        identity = {'operation_id': stage + '-1', 'role_instance': role.instance_id,
                    'workflow_run_id': 'run', 'task_id': 'T1', 'task_map_generation': 'map',
                    'operation_generation': 1, 'workspace_generation': 1,
                    'task_pipeline_stage': stage, 'task_stage_session_binding': binding['binding_key']}
        if stage == 'verify' and condition == 'unknown_holder':
            continue
        if stage == 'verify' and condition == 'stale_reader_binding':
            identity['task_stage_session_binding'] = 'stale'
        if stage == 'verify' and condition == 'wrong_generation':
            identity['workspace_generation'] = 2
        runtime.event_log.append(ZfEvent(type='workflow.operation.requested', payload=identity))
        runtime.event_log.append(ZfEvent(type='workflow.operation.started', payload=identity))
        if stage == 'impl':
            if condition != 'unadmitted_writer':
                runtime.event_log.append(ZfEvent(type='workflow.call.result.admitted',
                    payload={**identity, 'semantic_verdict': 'passed'}))
            runtime.event_log.append(ZfEvent(type='workflow.operation.settled', payload=identity))
            runtime.event_log.append(ZfEvent(type='provider.turn.closed', actor=role.instance_id))
    if condition != 'reader_exited':
        transport.alive.add(reader.instance_id)
    if condition == 'unknown_transport':
        original = transport.is_alive
        def unknown_reader(role_name):
            if role_name == reader.instance_id:
                raise RuntimeError('reader liveness unavailable')
            return original(role_name)
        monkeypatch.setattr(transport, 'is_alive', unknown_reader)
    with ExitStack() as holders:
        holders.enter_context(environment_in_use(prior))
        if condition == 'target_occupied':
            holders.enter_context(environment_in_use(target))
        args = dict(runtime=runtime, role=writer, task=Task(id='T2'),
            workflow_run_id='run', task_map_generation='map', stage='impl', workspace_generation=1,
            placement_epoch=2, role_config_digest='impl', project_path=target, workspace_root=tmp_path)
        if condition == 'transferred':
            _activate_task_stage_binding(**args)
            assert transport.spawned[-1][0] == writer.instance_id
            assert transport.spawned[-1][2] == target
        else:
            with pytest.raises(TaskPipelineWaiting):
                _activate_task_stage_binding(**args)
            assert not transport.spawned
        assert (reader.instance_id in transport.alive) == (condition != 'reader_exited')
        assert reader.instance_id not in transport.terminated
