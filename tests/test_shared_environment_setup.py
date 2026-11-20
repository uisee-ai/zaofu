import pytest
from tests.test_environment_resources import shared_workspaces
from zf.runtime.environment_usage import environment_in_use
from zf.runtime.worktree_env import run_project_setup


def setup(root, **kwargs):
    return run_project_setup(root, 'echo prepared >> node_modules/install-count',
        setup_inputs=['lock.txt'], required_paths=['node_modules'],
        setup_scope='shared_resources', **kwargs)


def fixture(tmp_path):
    source, first, second = shared_workspaces(tmp_path)
    for root in (first, second): (root / 'lock.txt').write_text('version-one')
    return source, first, second


def test_first_preparation_is_reused_with_live_shared_consumer(tmp_path):
    source, first, second = fixture(tmp_path)
    assert setup(first).ran
    with environment_in_use(first):
        reused = setup(second)
        assert reused.ok and not reused.ran
    assert (source / 'node_modules/install-count').read_text().splitlines() == ['prepared']


@pytest.mark.parametrize('fault', ['input', 'instance', 'mutation'])
def test_stale_shared_receipt_does_not_authorize_reuse(tmp_path, fault):
    source, first, second = fixture(tmp_path)
    assert setup(first).ok
    if fault == 'input': (second / 'lock.txt').write_text('version-two')
    if fault == 'instance':
        (source / 'node_modules').rename(source / 'old')
        (source / 'node_modules').mkdir()
    if fault == 'mutation':
        assert run_project_setup(first, 'touch node_modules/changed', required_paths=['node_modules']).ok
    with environment_in_use(first):
        result = setup(second)
        assert not result.ok and result.waiting_for_resources
    assert setup(second).ok


def test_workspace_setup_is_never_silently_reused(tmp_path):
    _, first, second = fixture(tmp_path)
    assert setup(first).ok
    with environment_in_use(first):
        result = run_project_setup(second, 'touch local-output', required_paths=['node_modules'])
        assert not result.ok and result.waiting_for_resources
        assert not (second / 'local-output').exists()


def test_shared_setup_rejects_missing_declarations(tmp_path):
    with pytest.raises(ValueError, match='requires'):
        run_project_setup(tmp_path, 'true', setup_scope='shared_resources')


def test_shared_setup_is_wired_through_workdir_entry(tmp_path, monkeypatch):
    from zf.core.config.schema import ZfConfig, ProjectConfig, RoleConfig
    from zf.runtime.workdirs import WorkdirManager
    _, first, second = fixture(tmp_path)
    config = ZfConfig(project=ProjectConfig(setup_script='echo prepared >> node_modules/install-count',
        setup_inputs=['lock.txt'], setup_scope='shared_resources'))
    config.runtime.workdirs.required_paths = ['node_modules']
    manager = WorkdirManager(state_dir=tmp_path/'.zf', project_root=tmp_path, config=config)
    monkeypatch.setattr('zf.runtime.required_environment.require_workspace_environment', lambda *a, **k: None)
    manager._run_declared_setup(RoleConfig(name='one', instance_id='one'), first)
    with environment_in_use(first):
        manager._run_declared_setup(RoleConfig(name='two', instance_id='two'), second)
    assert not (second / 'local-output').exists()


@pytest.mark.parametrize('scope', ['workspace', 'shared_resources'])
def test_setup_scope_config_round_trip(tmp_path, scope):
    import yaml
    from zf.core.config.loader import load_config
    from zf.core.config.render import renderable_config_to_primitive
    config_path = tmp_path / 'zf.yaml'
    config_path.write_text(yaml.safe_dump({'version': '1.0', 'project': {
        'name': 'test', 'setup_scope': scope, 'setup_inputs': ['lock.txt'],
        'scripts': {'setup': 'true'}}, 'runtime': {'workdirs': {'required_paths': ['node_modules']}}}))
    config = load_config(config_path)
    assert config.project.setup_scope == scope
    config_path.write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
    assert load_config(config_path).project.setup_scope == scope


def test_setup_scope_invalid_config_is_rejected(tmp_path):
    from zf.core.config.loader import load_config, ConfigError
    config_path = tmp_path / 'zf.yaml'
    config_path.write_text('version: "1.0"\nproject:\n  name: test\n  setup_scope: auto\n')
    with pytest.raises(ConfigError, match='setup_scope'):
        load_config(config_path)


def test_failed_in_place_install_invalidates_shared_receipt(tmp_path):
    _, first, second = fixture(tmp_path)
    assert setup(first).ok
    assert not run_project_setup(first, 'touch node_modules/partial; exit 1',
        required_paths=['node_modules']).ok
    with environment_in_use(first):
        assert setup(second).waiting_for_resources
    assert setup(second).ran


def test_shared_receipt_requires_same_logical_declarations(tmp_path):
    source, first, second = fixture(tmp_path)
    assert setup(first).ok
    (second / 'alternate').symlink_to(source / 'node_modules')
    with environment_in_use(first):
        result = run_project_setup(second, 'echo prepared >> node_modules/install-count',
            setup_inputs=['lock.txt'], required_paths=['alternate'], setup_scope='shared_resources')
        assert not result.ok and result.waiting_for_resources
    assert (source / 'node_modules/install-count').read_text().splitlines() == ['prepared']


def test_shared_setup_missing_output_never_publishes_ready(tmp_path):
    (tmp_path / 'lock.txt').write_text('v1')
    for _ in range(2):
        result = run_project_setup(tmp_path, 'true', setup_inputs=['lock.txt'],
            required_paths=['node_modules'], setup_scope='shared_resources')
        assert not result.ok
