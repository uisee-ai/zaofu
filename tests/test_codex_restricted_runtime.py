"""Restricted Codex launch preserves approvals without touching operator config."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tomllib
from unittest.mock import Mock
from uuid import uuid4

import pytest

from zf.core.config.schema import ConstraintsConfig, RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.backend import CodexAdapter
from zf.runtime.spawn_coordinator import SpawnCoordinator
from zf.runtime.transport import TransportAdapter
from zf.runtime import codex_restricted_runtime as restricted


@pytest.fixture
def isolated_spawn(tmp_path, monkeypatch):
    operator_home = tmp_path / "operator"
    global_codex = operator_home / ".codex"
    global_codex.mkdir(parents=True)
    project = tmp_path / 'project.v1 "quoted"'
    project.mkdir()
    state = tmp_path / "custom-state"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: operator_home))
    monkeypatch.setattr("os.path.expanduser", lambda s: str(operator_home / s[2:])
                        if s.startswith("~/") else s)
    monkeypatch.delenv("ZF_CODEX_WORKER_SANDBOX", raising=False)
    config = global_codex / "config.toml"
    config.write_text(
        '# operator comment\napproval_policy = "never"\nmodel = "test-model"\n'
        f'[projects.{json.dumps(str(project))}]\ntrust_level = "trusted"\n'
        '[model_providers.example]\nname = "keep-provider"\n', encoding="utf-8",
    )
    transport = Mock(spec=TransportAdapter)
    coordinator = SpawnCoordinator(
        state_dir=state, project_root=str(project), transport=transport,
        registry=RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(project)),
    )
    # Offline contract: host protocol is exercised separately, never launch an
    # installed CLI from this ordinary test tier.
    preflight = Mock()
    monkeypatch.setattr("zf.runtime.spawn_coordinator.verify_restricted_codex_config",
                        preflight, raising=False)
    return coordinator, transport, preflight, config, project, state


@pytest.mark.parametrize("mode", ["restricted", "allowlist"])
@pytest.mark.parametrize("paths", [[], ["/private/allowed"]])
def test_adapter_defers_approval_to_verified_project_policy(mode, paths, monkeypatch):
    monkeypatch.delenv("ZF_CODEX_WORKER_SANDBOX", raising=False)
    argv = CodexAdapter().build_command(RoleConfig(
        name="dev", permission_mode=mode, constraints=ConstraintsConfig(allowed_paths=paths),
    ))
    assert "-a" not in argv and "untrusted" not in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert argv[argv.index("-s") + 1] == ("workspace-write" if paths else "read-only")
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "--add-dir"] == paths


@pytest.mark.parametrize("mode", ["restricted", "allowlist"])
def test_spawn_installs_user_hooks_and_preflights_private_approval(isolated_spawn, mode):
    coordinator, transport, preflight, global_config, project, state = isolated_spawn
    original = global_config.read_bytes()
    role = RoleConfig(name="dev", backend="codex", permission_mode=mode,
                      constraints=ConstraintsConfig(allowed_paths=[str(project)]))
    coordinator.spawn(role, cwd=project)
    role_home = state / "workdirs/dev/codex-home"
    content = (role_home / "config.toml").read_text()
    parsed = tomllib.loads(content)
    assert "approval_policy" not in parsed
    assert parsed["model"] == "test-model"
    assert parsed["model_providers"]["example"]["name"] == "keep-provider"
    assert "# operator comment" in content
    hooks = json.loads((role_home / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    assert all(str(state) in row["hooks"][0]["command"] for rows in hooks.values() for row in rows)
    user_keys = [key for key in parsed["hooks"]["state"] if key.startswith(str(role_home / "hooks.json"))]
    assert len(user_keys) == 5
    argv = transport.spawn.call_args.args[1]
    assert f'projects={{{json.dumps(str(project))}={{trust_level="untrusted"}}}}' in argv
    assert "-a" not in argv
    assert preflight.call_count == 1
    assert preflight.call_args.kwargs["codex_home"] == role_home
    first = (role_home / "config.toml").read_bytes()
    coordinator.spawn(role, cwd=project)
    assert (role_home / "config.toml").read_bytes() == first
    assert global_config.read_bytes() == original


def test_failed_effective_policy_check_prevents_physical_spawn(isolated_spawn):
    coordinator, transport, preflight, _, project, _ = isolated_spawn
    preflight.side_effect = RuntimeError("Codex restricted policy has an explicit inherited approval")
    with pytest.raises(RuntimeError, match="inherited approval"):
        coordinator.spawn(RoleConfig(name="dev", backend="codex", permission_mode="restricted"), cwd=project)
    transport.spawn.assert_not_called()


def test_resume_keeps_exact_session_cwd_and_strict_project_override(isolated_spawn, monkeypatch):
    coordinator, transport, preflight, _, project, _ = isolated_spawn
    session = str(uuid4())
    coordinator.registry.bind_codex_session('dev', session)
    coordinator.registry.mark_spawned('dev')
    monkeypatch.setattr(coordinator, '_codex_session_exists', lambda *_: True)
    monkeypatch.setattr('zf.runtime.codex_session_ownership.codex_session_terminal_error_for_role',
                        lambda *a, **kw: None)
    coordinator.spawn(RoleConfig(name='dev', backend='codex', permission_mode='restricted'), cwd=project)
    argv = transport.spawn.call_args.args[1]
    index = argv.index('resume')
    assert argv[index + 1] == session and '--last' not in argv
    assert argv[index - 2:index] == ['-C', str(project)]
    assert restricted.restricted_project_override(project) in argv[:index]
    preflight.assert_called_once()


def test_two_roles_keep_independent_user_hook_trust(isolated_spawn):
    coordinator, _, preflight, _, project, state = isolated_spawn
    snapshots = []
    for name in ('dev', 'qa'):
        coordinator.spawn(RoleConfig(name=name, backend='codex', permission_mode='restricted'), cwd=project)
        role_home = state / f'workdirs/{name}/codex-home'
        snapshots.append((role_home / 'config.toml').read_text())
        keys = tomllib.loads(snapshots[-1])['hooks']['state']
        assert all(key.startswith(str(role_home / 'hooks.json')) for key in keys)
    assert (state / 'workdirs/dev/codex-home/config.toml').read_text() == snapshots[0]
    assert snapshots[0] != snapshots[1] and preflight.call_count == 2


@pytest.mark.parametrize('composite', [False, True])
def test_dry_run_records_safe_argv_without_invoking_installed_cli(isolated_spawn, composite):
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import CompositeTransport, TmuxTransport

    coordinator, _, preflight, _, project, _ = isolated_spawn
    transport = TmuxTransport(TmuxSession('zf-permission-dry-run', dry_run=True))
    coordinator.transport = CompositeTransport({'dev': transport}) if composite else transport
    preflight.side_effect = AssertionError('dry-run must not run installed Codex')
    coordinator.spawn(RoleConfig(name='dev', backend='codex', permission_mode='restricted'), cwd=project)
    preflight.assert_not_called()
    assert transport.tmux.command_log


@pytest.mark.parametrize("mode", ["bypass", "default"])
def test_other_modes_do_not_migrate_approval_or_run_restricted_probe(isolated_spawn, mode):
    coordinator, transport, preflight, global_config, project, state = isolated_spawn
    original = global_config.read_bytes()
    coordinator.spawn(RoleConfig(name="dev", backend="codex", permission_mode=mode), cwd=project)
    role_home = state / "workdirs/dev/codex-home"
    assert tomllib.loads((role_home / "config.toml").read_text())["approval_policy"] == "never"
    assert not (role_home / "hooks.json").exists()
    preflight.assert_not_called()
    assert transport.spawn.call_count == 1
    assert global_config.read_bytes() == original


@pytest.mark.parametrize("policy", [
    'approval_policy = "never" # old policy\n',
    '"approval_policy" = "on-request"\n',
    "'approval_policy' = '''untrusted'''\n",
    'approval_policy = """\nnever"""\n',
    'approval_policy = { granular = { sandbox_approval = true } }\n',
    '[approval_policy.granular]\nsandbox_approval = true\n',
    'approval_policy.granular.sandbox_approval = true\n',
])
def test_strip_policy_preserves_unrelated_toml_semantics_and_comments(policy):
    source = '# keep this\nmodel = "unchanged"\n' + policy + (
        '[other]\napproval_policy = "keep-nested"\n'
        'description = """literal approval_policy = never\n[not-a-table]"""\n'
    )
    expected = tomllib.loads(source)
    expected.pop("approval_policy")
    output = restricted.strip_explicit_approval(source)
    assert tomllib.loads(output) == expected
    assert '# keep this\nmodel = "unchanged"\n' in output
    assert restricted.strip_explicit_approval(output) == output


@pytest.mark.parametrize("bad", ['profile="legacy"\n', '[profiles.legacy]\nmodel="keep"\n', 'invalid = ['])
def test_invalid_or_retired_config_fails_before_physical_spawn(isolated_spawn, bad):
    coordinator, transport, _, global_config, project, _ = isolated_spawn
    global_config.write_text(bad)
    with pytest.raises((RuntimeError, tomllib.TOMLDecodeError)):
        coordinator.spawn(RoleConfig(name="dev", backend="codex", permission_mode="restricted"), cwd=project)
    transport.spawn.assert_not_called()
    assert global_config.read_text() == bad


@pytest.mark.parametrize("target", ["codex-home", "config.toml", "hooks.json"])
def test_restricted_home_rejects_symlink_redirection(isolated_spawn, target):
    coordinator, transport, _, global_config, project, state = isolated_spawn
    role_home = state / 'workdirs/dev/codex-home'
    role_home.parent.mkdir(parents=True)
    original = global_config.read_bytes()
    if target == 'codex-home':
        role_home.symlink_to(global_config.parent, target_is_directory=True)
    else:
        role_home.mkdir()
        (role_home / target).symlink_to(global_config)
    with pytest.raises(RuntimeError, match="private|symlink|state directory"):
        coordinator.spawn(RoleConfig(name="dev", backend="codex", permission_mode="restricted"), cwd=project)
    transport.spawn.assert_not_called()
    assert global_config.read_bytes() == original


def test_private_policy_replacement_breaks_hardlink_without_mutating_source(tmp_path):
    original = tmp_path / 'operator.toml'
    original.write_text('approval_policy="never"\nmodel="keep"\n')
    destination = tmp_path / 'role.toml'
    os.link(original, destination)
    before = original.read_bytes()
    restricted.prepare_restricted_approval(destination)
    assert original.read_bytes() == before
    assert tomllib.loads(destination.read_text()) == {'model': 'keep'}


@pytest.mark.parametrize("damage", [None, "never", "on-request", "missing-policy", "wrong-trust",
    "active-project", "no-layers", "bad-layer", "wrong-project-shape", "hook-cwd", "missing-hook",
    "untrusted-hook", "disabled-hook", "hook-errors"])
def test_effective_policy_gate_rejects_unproven_or_overridden_policy(tmp_path, monkeypatch, damage):
    config = {'approval_policy': None, 'projects': {str(tmp_path): {'trust_level': 'untrusted'}}}
    discovery = {'cwd': str(tmp_path), 'errors': [], 'hooks': [
        {'eventName': engine[0].lower() + engine[1:], 'source': 'user', 'sourcePath': str(tmp_path / 'hooks.json'),
         'key': f'{tmp_path / "hooks.json"}:{event.rsplit(".", 1)[-1]}:0:0',
         'enabled': True, 'trustStatus': 'trusted'} for engine, event in restricted.CODEX_HOOK_EVENTS
    ]}
    result = {'config': config, 'layers': [{'name': {'type': 'sessionFlags'}}],
              'hook_discovery': {'data': [discovery]}}
    if damage in {'never', 'on-request'}:
        config['approval_policy'] = damage
    elif damage == 'missing-policy':
        config.pop('approval_policy')
    elif damage == 'wrong-trust':
        config['projects'][str(tmp_path)]['trust_level'] = 'trusted'
    elif damage == 'active-project':
        result['layers'].append({'name': {'type': 'project'}})
    elif damage == 'no-layers':
        result['layers'] = []
    elif damage == 'bad-layer':
        result['layers'].append('bad')
    elif damage == 'wrong-project-shape':
        config['projects'] = []
    elif damage == 'hook-cwd':
        discovery['cwd'] = '/wrong'
    elif damage == 'missing-hook':
        discovery['hooks'].pop()
    elif damage == 'untrusted-hook':
        discovery['hooks'][0]['trustStatus'] = 'untrusted'
    elif damage == 'disabled-hook':
        discovery['hooks'][0]['enabled'] = False
    elif damage == 'hook-errors':
        discovery['errors'].append('private error; do not expose')
    monkeypatch.setattr(restricted, '_read_effective_config', lambda **_: result)
    if damage:
        with pytest.raises(RuntimeError, match='Codex restricted'):
            restricted.verify_restricted_codex_config(codex_home=tmp_path, project_root=tmp_path)
    else:
        restricted.verify_restricted_codex_config(codex_home=tmp_path, project_root=tmp_path)


@pytest.mark.parametrize('scenario', ['success', 'wrong-home', 'malformed', 'timeout', 'rpc-error',
                                    'child-timeout', 'oversize'])
def test_metadata_protocol_uses_only_read_calls_and_reaps_process(tmp_path, monkeypatch, scenario):
    binary = tmp_path / 'codex'
    binary.write_text(f'#!{sys.executable}\n' + '''
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path(os.environ['CODEX_HOME'])
(root / 'pid').write_text(str(os.getpid()))
case = os.environ['ZF_TEST_CODEX_CONFIG_PROBE_CASE']
for line in sys.stdin:
    request = json.loads(line)
    with (root / 'methods').open('a') as log:
        log.write(request['method'] + '\\n')
    if request['method'] == 'initialized':
        continue
    if request['method'] == 'initialize':
        result = {'codexHome': '/wrong' if case == 'wrong-home' else str(root)}
    else:
        if case == 'child-timeout':
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            (root / 'child-pid').write_text(str(child.pid))
            def stop(signum, frame):
                child.wait(timeout=2)
                sys.exit(0)
            signal.signal(signal.SIGTERM, stop)
            time.sleep(30)
        if case == 'oversize':
            print('x' * (9 * 1024 * 1024), flush=True)
            continue
        if case == 'timeout':
            time.sleep(30)
        if case == 'malformed':
            print('PRIVATE_SENTINEL not-json', flush=True)
            continue
        if case == 'rpc-error':
            print(json.dumps({'id': request['id'], 'error': {'message': 'PRIVATE_SENTINEL'}}), flush=True)
            continue
        result = ({'data': []} if request['method'] == 'hooks/list' else
                  {'config': {'approval_policy': None, 'projects': {}}, 'layers': []})
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''')
    binary.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.setenv('ZF_TEST_CODEX_CONFIG_PROBE_CASE', scenario)
    if scenario == 'success':
        result = restricted._read_effective_config(codex_home=tmp_path, project_root=tmp_path, timeout=2)
        assert result['config']['approval_policy'] is None
    else:
        # The deadline includes interpreter startup. Give the fixture enough
        # time to reach its injected fault; do not accept an early timeout as
        # evidence of malformed/RPC/oversize response handling.
        expected_error = {
            'wrong-home': 'role-local home',
            'malformed': 'Expecting value',
            'timeout': 'timed out',
            'rpc-error': 'request was rejected',
            'child-timeout': 'timed out',
            'oversize': 'response budget',
        }[scenario]
        with pytest.raises((RuntimeError, ValueError), match=expected_error) as caught:
            restricted._read_effective_config(codex_home=tmp_path, project_root=tmp_path, timeout=5)
        assert 'PRIVATE_SENTINEL' not in str(caught.value)
    methods = (tmp_path / 'methods').read_text().splitlines()
    expected = ['initialize'] if scenario == 'wrong-home' else ['initialize', 'initialized', 'config/read']
    if scenario == 'success':
        expected.append('hooks/list')
    assert methods == expected
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / 'pid').read_text()), 0)
    if scenario == 'child-timeout':
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / 'child-pid').read_text()), 0)
