"""Opt-in user service deployment; lifecycle authority remains in host-check."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess


def _quote(value: str, *, command: bool = False) -> str:
    if any(c in value for c in ('\n', '\r', '\x00')):
        raise ValueError('service values must be single-line')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if command:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def render_user_unit(*, project_root: Path, state_dir: Path, python: Path,
                     source_root: Path, unit_name: str = '', environment: dict[str, str] | None = None) -> str:
    root, state = project_root.resolve(), state_dir.resolve()
    executable = _quote(str(python.absolute()), command=True)
    cli = f'{executable} -m zf.cli.main'
    environment = environment or {}
    env = {'ZF_PROJECT_ROOT': str(root), 'ZF_STATE_DIR': str(state),
           'PYTHONPATH': str(source_root.resolve() / 'src'),
           'PATH': str(python.absolute().parent) + ':' + environment.get('PATH', '/usr/bin:/bin')}
    # Only non-secret process-placement values are persisted. No inherited tokens.
    for key in ('TMUX_TMPDIR', 'ZF_TMUX_SESSION'):
        if environment.get(key):
            env[key] = environment[key]
    if unit_name:
        if not re.fullmatch(r'zf-[a-zA-Z0-9_-]{1,100}', unit_name):
            raise ValueError('invalid unit name')
        env['ZF_HOST_SUPERVISOR_UNIT'] = unit_name + '.service'
    lines = ['[Unit]', 'Description=ZaoFu control plane (explicitly authorized project)',
             'StartLimitIntervalSec=300', 'StartLimitBurst=5', '', '[Service]', 'Type=simple',
             f'WorkingDirectory={_quote(str(root))[1:-1]}', 'Restart=on-failure', 'RestartSec=5s',
             'KillMode=process', 'TimeoutStopSec=30s']
    lines += ['Environment=' + _quote(k + '=' + v) for k, v in env.items()]
    lines += [f'ExecCondition={cli} recover host-check --state-dir {_quote(str(state), command=True)}',
              f'ExecStart={cli} start --control-plane-only --skip-workflow-inspect',
              '', '[Install]', 'WantedBy=default.target', '']
    return '\n'.join(lines)


def install_user_unit(name: str, body: str, *, directory: Path | None = None) -> Path:
    if not re.fullmatch(r'zf-[a-zA-Z0-9_-]{1,100}', name):
        raise ValueError('unit name must be zf- followed by letters, digits, _ or -')
    folder = directory or Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (name + '.service')
    if target.is_symlink() or (target.exists() and target.read_text() != body):
        raise ValueError('existing service has different content; review before replacing')
    if not target.exists():
        with target.open('x') as stream:
            stream.write(body)
    for command in (['systemctl', '--user', 'daemon-reload'],
                    ['systemctl', '--user', 'enable', name + '.service']):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise ValueError(f'service installation incomplete: {result.stderr.strip()} (file retained: {target})')
    return target
