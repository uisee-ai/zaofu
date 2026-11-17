import json
import os
from pathlib import Path
import subprocess
import sys

from zf.runtime.environment_resources import environment_lock_plan
from zf.runtime.environment_usage import environment_in_use
from zf.runtime.provider_exit_evidence import confirmed_provider_exit


def receipt(root, pid, ticks):
    plan = environment_lock_plan(root)
    path = Path(plan['locks'][0] + '.holders') / f'{pid}-{ticks}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'pid': pid, 'start_ticks': ticks}))


def test_missing_identity_is_not_exit_evidence(tmp_path):
    assert not confirmed_provider_exit(tmp_path)


def test_registered_live_provider_vetoes_fast_recovery(tmp_path):
    ticks = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    receipt(tmp_path, os.getpid(), ticks)
    assert not confirmed_provider_exit(tmp_path)


def test_registered_exit_with_detached_holder_waits(tmp_path):
    process = subprocess.Popen([sys.executable, '-c', 'pass'])
    process.wait(timeout=5)
    receipt(tmp_path, process.pid, '1')
    with environment_in_use(tmp_path):
        assert not confirmed_provider_exit(tmp_path)
    # Holder inspection can retire old receipts; restore the historical identity
    # for the second observation rather than fabricating liveness from absence.
    receipt(tmp_path, process.pid, '1')
    assert confirmed_provider_exit(tmp_path)


def test_actual_watchdog_uses_strong_exit_evidence(tmp_path, monkeypatch):
    from tests.test_capture_logs_watchdog import _AliveControllableTransport
    from zf.core.config.schema import ZfConfig, RoleConfig, ProjectConfig
    from zf.core.events import EventLog
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator
    state = tmp_path / '.zf'; state.mkdir()
    role = RoleConfig(name='dev', backend='mock')
    TaskStore(state/'kanban.json').add(Task(id='T', title='work', status='in_progress', assigned_to='dev'))
    transport = _AliveControllableTransport()
    transport.spawn(role, argv=[]); transport.alive_flags['dev'] = False
    runtime = Orchestrator(state, ZfConfig(project=ProjectConfig(name='test'), roles=[role]), transport)
    runtime._dead_threshold = 1
    monkeypatch.setattr(runtime, '_worker_liveness_stale', lambda role: (False, 'fresh heartbeat'))
    monkeypatch.setattr('zf.runtime.provider_exit_evidence.active_provider_exit_confirmed', lambda *a: True)
    runtime._capture_logs()
    assert transport.spawn_calls.count('dev') == 2
