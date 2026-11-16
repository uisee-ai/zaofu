"""Deployment is reproducible and preserves lifecycle guards."""
from pathlib import Path
from types import SimpleNamespace
import argparse

import pytest

from zf.cli.recover_host import register_recover_host
from zf.runtime.host_supervision import render_user_unit, install_user_unit


def test_unit_keeps_exact_paths_and_readonly_restart_guard(tmp_path):
    text = render_user_unit(project_root=tmp_path / 'space %n $HOME', state_dir=tmp_path / 'custom-state' / '$HOME',
                            python=Path('/usr/bin/python3'), source_root=tmp_path / 'source')
    assert 'WorkingDirectory="' not in text
    assert 'Restart=on-failure' in text
    assert 'KillMode=process' in text
    assert 'recover host-check' in text
    assert 'start --control-plane-only' in text
    assert str(tmp_path / 'custom-state') in text
    assert '%%n' in text and '$$HOME' in text
    assert 'WantedBy=default.target' in text
    assert 'ZF_WEB_ACTION_TOKEN' not in text


def test_install_refuses_overwrite_and_does_not_start_runtime(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('zf.runtime.host_supervision.subprocess.run',
        lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stderr=''))
    target = install_user_unit('zf-project', 'unit body', directory=tmp_path)
    assert target.read_text() == 'unit body'
    assert calls == [['systemctl', '--user', 'daemon-reload'],
                     ['systemctl', '--user', 'enable', 'zf-project.service']]
    with pytest.raises(ValueError, match='different'):
        install_user_unit('zf-project', 'other body', directory=tmp_path)
    with pytest.raises(ValueError):
        install_user_unit('../outside', 'body', directory=tmp_path)


def test_deployment_registered_on_existing_cli():
    parser = argparse.ArgumentParser()
    register_recover_host(parser.add_subparsers())
    args = parser.parse_args(['host-unit', '--name', 'zf-project'])
    assert not args.install
