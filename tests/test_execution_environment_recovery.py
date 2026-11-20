from zf.runtime.candidate_incremental import _rolling_smoke_environment_error


def test_no_dependency_package_does_not_require_node_modules(tmp_path):
    (tmp_path / 'package.json').write_text('{"scripts":{"test":"node --test"}}')
    assert _rolling_smoke_environment_error('npm test', tmp_path) is None


def test_declared_dependency_can_resolve_from_parent(tmp_path):
    app = tmp_path / 'app'; app.mkdir()
    (app / 'package.json').write_text('{"devDependencies":{"typescript":"*"}}')
    dependency = tmp_path / 'node_modules/typescript'; dependency.mkdir(parents=True)
    (dependency / 'package.json').write_text('{"name":"typescript"}')
    assert _rolling_smoke_environment_error('npm run build', app) is None


def test_empty_local_module_directory_does_not_prove_dependencies(tmp_path):
    (tmp_path / 'package.json').write_text('{"dependencies":{"missing":"*"}}')
    (tmp_path / 'node_modules').mkdir()
    assert _rolling_smoke_environment_error('npm test', tmp_path)
