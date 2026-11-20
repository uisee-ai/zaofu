"""Independent host watchdog protocol, without installing or running a service."""
import os
import socket
from pathlib import Path

import pytest
from tests.test_cli_start import project_dir

from zf.core.config.loader import _build_runtime, ConfigError
from zf.runtime.host_supervision import render_user_unit


def test_watchdog_is_opt_in_and_rendered_from_runtime_config(tmp_path):
    config = _build_runtime({'host_watchdog_seconds': 120})
    assert config.host_watchdog_seconds == 120
    body = render_user_unit(project_root=tmp_path, state_dir=tmp_path / 'state',
        python=Path('/usr/bin/python3'), source_root=tmp_path,
        watchdog_seconds=config.host_watchdog_seconds)
    assert 'Type=notify' in body and 'WatchdogSec=120s' in body
    assert 'NotifyAccess=main' in body and 'WatchdogSignal=SIGKILL' in body
    assert 'KillMode=process' in body and 'recover host-check' in body
    assert _build_runtime({}).host_watchdog_seconds == 0


@pytest.mark.parametrize('value', [-1, True, 'abc', float('nan'), float('inf'), 1])
def test_invalid_watchdog_interval_rejected(value):
    with pytest.raises(ConfigError):
        _build_runtime({'host_watchdog_seconds': value})


def test_only_current_main_process_sends_loop_completion(tmp_path, monkeypatch):
    from zf.runtime.host_watchdog import notify_host_watchdog
    from zf.runtime.process_guard import SingleOwnerProcessGuard
    address = str(tmp_path / 'notify.sock')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
        listener.bind(address)
        listener.settimeout(0.1)
        monkeypatch.setenv('NOTIFY_SOCKET', address)
        monkeypatch.setenv('WATCHDOG_USEC', '120000000')
        monkeypatch.setenv('WATCHDOG_PID', str(os.getpid()))
        guard = SingleOwnerProcessGuard(tmp_path / 'watcher.json')
        assert guard.acquire().acquired
        assert notify_host_watchdog(guard, ready=True)
        assert listener.recv(1024) == b'READY=1\nWATCHDOG=1'
        assert notify_host_watchdog(guard)
        assert listener.recv(1024) == b'WATCHDOG=1'
        # Another process cannot inherit the parent's notification authority.
        monkeypatch.setenv('WATCHDOG_PID', str(os.getpid() + 100))
        assert not notify_host_watchdog(guard)
        monkeypatch.setenv('WATCHDOG_PID', str(os.getpid()))
        guard.epoch = 'replaced-owner'
        assert not notify_host_watchdog(guard)
        with pytest.raises(TimeoutError):
            listener.recv(1024)


@pytest.mark.parametrize('fault', ['none', 'kernel', 'services'])
def test_start_only_notifies_after_whole_controller_tick(project_dir, monkeypatch, fault):
    from zf.cli.main import main
    from zf.runtime.watcher import EventWatcher
    calls = []
    def kernel(*args):
        calls.append('kernel')
        if fault == 'kernel':
            raise ValueError('mock stuck/failing kernel')
    def services(*args, **kwargs):
        calls.append('services')
        if fault == 'services':
            raise ValueError('mock common-fault watchdog service')
    def poll(watcher):
        try:
            watcher.on_tick()
        except ValueError:
            assert fault == 'services'
        return []
    monkeypatch.setattr('zf.cli.start._run_orchestrator_idle_tick', kernel)
    monkeypatch.setattr('zf.runtime.tick_services.run_standard_tick_services', services)
    monkeypatch.setattr('zf.runtime.host_watchdog.notify_host_watchdog', lambda *a, **k: calls.append('notify'))
    monkeypatch.setattr(EventWatcher, 'poll_once', poll)
    assert main(['start', '--dry-run', '--skip-workflow-inspect']) == 0
    assert calls == (['kernel', 'services', 'notify'] if fault == 'none' else ['kernel', 'services'])
