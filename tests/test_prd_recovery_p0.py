from zf.runtime.environment_usage import EnvironmentInUse


def test_busy_reader_waits_and_resumes_same_fanout_after_controller_restart(tmp_path, monkeypatch):
    from tests.test_reader_target_pinning import _state, _trigger
    from zf.runtime.workdirs import WorkdirManager
    from zf.runtime.orchestrator import Orchestrator
    head, state, log, transport, runtime = _state(tmp_path, workdirs=True)
    pin = WorkdirManager.pin_reader_target
    monkeypatch.setattr(WorkdirManager, 'pin_reader_target', lambda *_: (_ for _ in ()).throw(EnvironmentInUse('busy')))
    _trigger(runtime, log, target_ref=head)
    events = log.read_all()
    deferred = next(e for e in events if e.type == 'fanout.child.dispatch_deferred')
    assert deferred.payload['transport_alive'] is True
    assert not any(e.type in {'fanout.child.failed', 'judge.failed'} for e in events)
    assert not transport.sent
    restarted = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
    restarted.run_once(events=[])
    assert not transport.sent
    monkeypatch.setattr(WorkdirManager, 'pin_reader_target', pin)
    restarted.run_once(events=[])
    assert len(transport.sent) == 1
    events = log.read_all()
    assert len([e for e in events if e.type == 'fanout.started']) == 1
    assert not any(e.type == 'orchestrator.replan_requested' for e in events)


def test_parent_keeps_explicit_goal_and_reports_foreign_child():
    from zf.runtime.fanout_parent_identity import parent_flow_identity
    identity, conflicts = parent_flow_identity(
        {'trigger_payload': {'goal_id': 'run-goal', 'pdd_id': 'PDD', 'workflow_run_id': 'run-goal'}},
        aggregate_payload={}, payloads=[{'goal_id': 'foreign'}])
    assert identity['goal_id'] == 'run-goal'
    assert conflicts == [{'key': 'goal_id', 'parent_value': 'run-goal', 'child_values': ['foreign']}]


def test_reader_waits_for_native_holder_before_checkout_then_resumes(tmp_path):
    import select
    import subprocess
    import sys
    from tests.test_reader_target_pinning import _state, _trigger
    from zf.runtime.workdirs import WorkdirManager

    head, state, log, transport, runtime = _state(tmp_path, workdirs=True)
    role = runtime.config.roles[0]
    workspace = WorkdirManager(state_dir=state, project_root=tmp_path, config=runtime.config).prepare(role)
    process = subprocess.Popen([sys.executable, '-c', "print('ready', flush=True); input()"],
        cwd=workspace.project_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == 'ready'
        _trigger(runtime, log, target_ref=head)
        assert not transport.sent
        assert not any(e.type == 'fanout.child.failed' for e in log.read_all())
    finally:
        process.communicate('\n', timeout=5)
    runtime.run_once(events=[])
    assert len(transport.sent) == 1


def test_only_managed_idle_reader_is_released(tmp_path, monkeypatch):
    from tests.test_role_lifecycle_runtime import _runtime
    from zf.runtime.reader_target_dispatch import _release_idle_owner

    runtime, role, transport = _runtime(tmp_path)
    transport.alive.add(role.instance_id)
    monkeypatch.setattr(runtime, '_active_provider_turn', lambda _: {'active': True})
    _release_idle_owner(runtime, role)
    assert not transport.terminated
    monkeypatch.setattr(runtime, '_active_provider_turn', lambda _: None)
    _release_idle_owner(runtime, role)
    assert transport.terminated == [role.instance_id]


def test_task_setup_occupancy_waits_before_creating_operation(tmp_path):
    import pytest
    from types import SimpleNamespace
    from tests.test_task_workspace_binding import _manager
    from zf.core.config.schema import RoleLifecycleConfig
    from zf.core.events import EventLog, EventWriter
    from zf.core.task.schema import Task
    from zf.runtime.environment_usage import environment_in_use
    from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting

    manager, role, base = _manager(tmp_path)
    manager.config.project.setup_script = 'touch node_modules/prepared'
    manager.config.runtime.workdirs.required_paths = ['node_modules']
    manager.config.runtime.workdirs.provision_paths = ['node_modules']
    (tmp_path / 'node_modules').mkdir()
    role.lifecycle = RoleLifecycleConfig(mode='on_demand')
    log = EventLog(manager.state_dir / 'events.jsonl')
    runtime = SimpleNamespace(config=manager.config, state_dir=manager.state_dir,
        project_root=tmp_path, event_log=log, event_writer=EventWriter(log),
        _find_role_by_instance=lambda _: role)
    with environment_in_use(tmp_path):
        for _ in range(2):
            with pytest.raises(TaskPipelineWaiting) as waiting:
                dispatch_task_pipeline_stage(runtime, policy={}, task=Task(id='T', title='Implement'),
                    assignment={'stage': 'impl', 'role_instance': role.instance_id},
                    generation_context={'workflow_run_id': 'run-1', 'task_map_generation': 'map-1',
                                        'dispatch_base_commit': base}, operation_rows=[], attempt_rows=[])
            assert waiting.value.reason == 'waiting_for_environment'
        assert not (tmp_path / 'node_modules/prepared').exists()
    assert not any(e.type == 'workflow.operation.requested' for e in log.read_all())
    workspace = manager.prepare(role=role, workflow_run_id='run-1', task_id='T',
        task_map_generation='map-1', workspace_generation=1, base_ref=base)
    assert workspace.enabled
    assert (tmp_path / 'node_modules/prepared').exists()


def test_invalid_reader_target_is_infra_failure_not_resource_wait(tmp_path):
    from tests.test_reader_target_pinning import _state, _trigger
    _, _, log, transport, runtime = _state(tmp_path, workdirs=True)
    _trigger(runtime, log, target_ref='missing-target')
    failed = [e for e in log.read_all() if e.type == 'fanout.child.failed']
    assert len(failed) == 1
    assert failed[0].payload['failure_kind'] == 'infra'
    assert not transport.sent
    assert not any(e.type == 'fanout.child.dispatch_deferred' for e in log.read_all())
