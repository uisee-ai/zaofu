"""Opt-in installed-CLI configuration evidence; never start a model turn."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tests.test_codex_restricted_runtime import isolated_spawn  # noqa: F401
from tests.test_codex_hook_settings import _RECORDED_HASH_VECTORS
from zf.core.config.schema import ConstraintsConfig, RoleConfig
from zf.runtime.codex_restricted_runtime import verify_restricted_codex_config
from zf.runtime import codex_restricted_runtime as restricted

pytestmark = [pytest.mark.host, pytest.mark.serial]


@pytest.mark.parametrize("writable", [False, True])
def test_installed_codex_accepts_spawn_and_preserves_strict_policy(
    isolated_spawn, monkeypatch, writable, record_property,
):
    if os.environ.get("ZF_RUN_CODEX_PERMISSION_HOST") != "1":
        pytest.skip("set ZF_RUN_CODEX_PERMISSION_HOST=1 for metadata-only installed CLI validation")
    if not shutil.which("codex"):
        pytest.skip("Codex CLI unavailable")
    coordinator, transport, _, global_config, project, state = isolated_spawn
    global_config.write_text('approval_policy = "never"\n'
                             f'[projects.{json.dumps(str(project))}]\ntrust_level="trusted"\n', encoding="utf-8")
    original = global_config.read_bytes()
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True, timeout=10)
    (project / ".codex").mkdir()
    (project / ".codex/config.toml").write_text('approval_policy = "never"\n', encoding="utf-8")
    monkeypatch.setattr("zf.runtime.spawn_coordinator.verify_restricted_codex_config",
                        verify_restricted_codex_config)
    evidence = {}
    read_metadata = restricted._read_effective_config

    def capture_metadata(**kwargs):
        evidence.update(read_metadata(**kwargs))
        return evidence

    monkeypatch.setattr(restricted, "_read_effective_config", capture_metadata)
    role = RoleConfig(name="dev", backend="codex", permission_mode="restricted",
                      constraints=ConstraintsConfig(allowed_paths=[str(project)] if writable else []))
    coordinator.spawn(role, cwd=project)
    captured = transport.spawn.call_args.args[1]
    argv = captured[captured.index("codex"):]
    role_home = state / "workdirs/dev/codex-home"
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TERM", "LANG") if key in os.environ}
    env["CODEX_HOME"] = str(role_home)
    accepted = subprocess.run([*argv, "--help"], cwd=project, env=env, capture_output=True,
                              text=True, timeout=20)
    assert accepted.returncode == 0, accepted.stderr
    rendered = subprocess.run([*argv, "-C", str(project), "debug", "prompt-input"], cwd=project,
                             env=env, capture_output=True, text=True, timeout=20)
    assert rendered.returncode == 0, rendered.stderr

    def texts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from texts(child)
        elif isinstance(value, list):
            for child in value:
                yield from texts(child)

    permissions = [match for text in texts(json.loads(rendered.stdout)) for match in
                   re.findall(r"<permissions instructions>.*?</permissions instructions>", text, re.S)]
    assert len(permissions) == 1
    assert '`approval_policy` is `unless-trusted`' in permissions[0]
    sandbox = "workspace-write" if writable else "read-only"
    assert f'`sandbox_mode` is `{sandbox}`' in permissions[0]
    assert global_config.read_bytes() == original
    assert (project / ".codex/config.toml").read_text() == 'approval_policy = "never"\n'
    record_property("scope", "installed CLI metadata; Mock physical worker; no thread/turn/model")
    record_property("sandbox", sandbox)
    record_property("approval", "unless-trusted")
    record_property("hook_evidence", json.dumps([
        {key: hook[key] for key in ("key", "eventName", "source", "currentHash", "trustStatus", "enabled")}
        for hook in evidence["hook_discovery"]["data"][0]["hooks"]
    ]))


@pytest.mark.parametrize('state_dir', sorted({row['state_dir'] for row in _RECORDED_HASH_VECTORS}))
def test_installed_codex_reports_recorded_hook_hashes(tmp_path, state_dir, record_property):
    """Compare independent literal inputs to provider hashes; never compute the oracle."""
    if os.environ.get('ZF_RUN_CODEX_PERMISSION_HOST') != '1':
        pytest.skip('set ZF_RUN_CODEX_PERMISSION_HOST=1 for metadata-only installed CLI validation')
    if not shutil.which('codex'):
        pytest.skip('Codex CLI unavailable')
    home, project = tmp_path / 'private-codex', tmp_path / 'project'
    home.mkdir()
    project.mkdir()
    (home / 'config.toml').write_text('', encoding='utf-8')
    rows = [row for row in _RECORDED_HASH_VECTORS if row['state_dir'] == state_dir]
    document = {'hooks': {row['engine']: [{'matcher': '', 'hooks': [{
        'type': 'command', 'command': row['command'],
    }]}] for row in rows}}

    def discover():
        (home / 'hooks.json').write_text(json.dumps(document), encoding='utf-8')
        result = restricted._read_effective_config(codex_home=home, project_root=project)
        [data] = result['hook_discovery']['data']
        assert data['cwd'] == str(project) and not data['errors']
        hooks = data['hooks']
        assert len(hooks) == 5
        assert all(hook['source'] == 'user' and hook['sourcePath'] == str(home / 'hooks.json')
                   and hook['enabled'] is True and hook['trustStatus'] == 'untrusted' for hook in hooks)
        return {hook['key']: hook['currentHash'] for hook in hooks}

    expected = {f'{home / "hooks.json"}:{row["label"]}:0:0': row['current_hash'] for row in rows}
    original = discover()
    assert original == expected
    document['hooks']['Stop'][0]['hooks'][0]['command'] += ' --testkit-hash-sentinel'
    changed = discover()
    stop = f'{home / "hooks.json"}:stop:0:0'
    assert changed[stop] != expected[stop]
    assert {key: value for key, value in changed.items() if key != stop} == {
        key: value for key, value in expected.items() if key != stop
    }
    record_property('scope', 'literal commands to hooks/list currentHash; no hook execution or model')
    record_property('hash_evidence', json.dumps({'state_dir': state_dir, 'original': original, 'changed': changed}))
