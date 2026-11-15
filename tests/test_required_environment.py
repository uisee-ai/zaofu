"""Required environment failures stop real workspace preparation callers."""
from pathlib import Path

import pytest
import yaml

from tests.test_task_workspace_binding import _manager, _prepare
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.render import renderable_config_to_primitive


def test_task_workspace_missing_required_environment_blocks(tmp_path):
    manager, role, base = _manager(tmp_path)
    manager.config.runtime.workdirs.required_paths = ['node_modules']
    with pytest.raises(RuntimeError, match='required.*node_modules'):
        _prepare(manager, role, base, 'T')


def test_task_setup_can_supply_and_restore_required_environment(tmp_path):
    manager, role, base = _manager(tmp_path)
    manager.config.runtime.workdirs.required_paths = ['node_modules']
    manager.config.project.setup_script = 'mkdir -p node_modules'
    plan = _prepare(manager, role, base, 'T')
    assert (Path(plan.project_path) / 'node_modules').is_dir()
    (Path(plan.project_path) / 'node_modules').rmdir()
    candidate = Path(plan.project_path) / 'candidate.txt'
    candidate.write_text('preserve in-progress work')
    resumed = _prepare(manager, role, base, 'T')
    assert (Path(resumed.project_path) / 'node_modules').is_dir()
    assert candidate.read_text() == 'preserve in-progress work'


def test_required_paths_roundtrip(tmp_path):
    path = tmp_path / 'zf.yaml'
    path.write_text(yaml.safe_dump({'version':'1.0','project':{'name':'test'},
        'runtime':{'workdirs':{'required_paths':['.venv','web/node_modules']}}}))
    config = load_config(path)
    assert config.runtime.workdirs.required_paths == ['.venv','web/node_modules']
    path.write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
    assert load_config(path).runtime.workdirs.required_paths == config.runtime.workdirs.required_paths


@pytest.mark.parametrize('value', ['node_modules', ['../outside'], ['/tmp/external'], ['.'], [1], ['']])
def test_required_paths_reject_invalid_declarations(tmp_path, value):
    path = tmp_path / 'zf.yaml'
    path.write_text(yaml.safe_dump({'version':'1.0','project':{'name':'test'},
        'runtime':{'workdirs':{'required_paths':value}}}))
    with pytest.raises(ConfigError, match='required_paths'):
        load_config(path)


def test_role_prepare_missing_required_environment_blocks(tmp_path):
    from tests.test_workdirs import _init_repo, _reader_manager

    _init_repo(tmp_path)
    manager = _reader_manager(tmp_path)
    manager.config.runtime.workdirs.mode = 'worktree'
    manager.config.runtime.workdirs.required_paths = ['node_modules']
    with pytest.raises(RuntimeError, match='required.*node_modules'):
        manager.prepare(manager.config.roles[0])


def test_failed_python_bootstrap_residue_cannot_satisfy_required_path(tmp_path, monkeypatch):
    from tests.test_task_workspace_binding import _git

    manager, role, base = _manager(tmp_path)
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="test"\nversion="0.1"\n')
    _git(tmp_path, 'add', 'pyproject.toml')
    _git(tmp_path, 'commit', '-qm', 'test: Python project')
    base = _git(tmp_path, 'rev-parse', 'HEAD')
    manager.config.runtime.workdirs.provision_paths = ['.venv']
    manager.config.runtime.workdirs.required_paths = ['.venv']
    def failed(root, **kwargs):
        (root / '.venv').mkdir(exist_ok=True)
        return False
    monkeypatch.setattr('zf.runtime.worktree_env._bootstrap_uv_dev_env', failed)
    with pytest.raises(RuntimeError, match='required.*successful bootstrap receipt'):
        _prepare(manager, role, base, 'T')


def test_candidate_required_environment_blocks_quality_checks(tmp_path):
    from tests.test_candidates import (_init_repo, _state, _config, _task_commit,
        _record_task_ref, _add_task, _approve, _rebuilder)
    from zf.core.config.schema import QualityGateConfig
    from zf.core.events import EventWriter

    _init_repo(tmp_path)
    state, _, log = _state(tmp_path)
    config = _config(state, quality_gates={'candidate': QualityGateConfig(
        enabled=True, required_checks=['touch quality-gate-ran'])})
    config.runtime.workdirs.required_paths = ['node_modules']
    commit = _task_commit(tmp_path, branch='worker/TASK-1', file_name='a.txt', content='candidate', message='test')
    _record_task_ref(tmp_path, state, config, task_id='TASK-1', commit=commit, branch='worker/TASK-1')
    _add_task(state, log, task_id='TASK-1')
    _approve(log, 'TASK-1')
    result = _rebuilder(tmp_path, state, config, log).rebuild('F-11111111', event_writer=EventWriter(log))
    assert result.status == 'quality_failed'
    assert 'required environment' in result.payload['candidate_environment']['detail']
    assert not (Path(result.payload['merger_worktree']) / 'quality-gate-ran').exists()


def test_real_stage_dispatch_stops_before_creating_provider_operation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zf.core.config.schema import RoleLifecycleConfig
    from zf.core.events import EventLog, EventWriter
    from zf.core.task.schema import Task
    from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage

    manager, role, base = _manager(tmp_path)
    manager.config.runtime.workdirs.required_paths = ['node_modules']
    role.lifecycle = RoleLifecycleConfig(mode='on_demand')
    log = EventLog(manager.state_dir / 'events.jsonl')
    runtime = SimpleNamespace(config=manager.config, state_dir=manager.state_dir,
        project_root=tmp_path, event_log=log, event_writer=EventWriter(log),
        _find_role_by_instance=lambda _: role)
    monkeypatch.setattr('zf.runtime.call_result_runtime.prepare_call_operation',
        lambda *a, **kw: pytest.fail('provider operation created before environment admission'))
    with pytest.raises(RuntimeError, match='required.*node_modules'):
        dispatch_task_pipeline_stage(runtime, policy={}, task=Task(id='T', title='Implement'),
            assignment={'stage':'impl', 'role_instance':role.instance_id},
            generation_context={'workflow_run_id':'run-1', 'task_map_generation':'map-1',
                                'dispatch_base_commit':base}, operation_rows=[], attempt_rows=[])
    assert not any(event.type == 'workflow.operation.requested' for event in log.read_all())
