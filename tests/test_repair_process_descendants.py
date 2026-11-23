"""Repair worker/verification termination must include detached descendants."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from zf.runtime.self_repair_process import LAUNCH_SCHEMA, run_launch
from zf.runtime.run_manager_repair_validation import execute_repair_verification_plan

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux repair supervisor')


def spawn_source(root, *, timeout=False, double_fork=False):
    child = "import os,time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); " + \
            "exec(\"while True:\\n Path('pulse').write_text(str(time.monotonic_ns()))\\n time.sleep(.02)\")"
    if double_fork:
        child = "import os; os.setsid(); pid=os.fork(); " + \
            "exec(\"if pid: os._exit(0)\"); " + child
    return ("import os,subprocess,sys,time\nfrom pathlib import Path\n" +
        f"subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session={not double_fork!r})\n" +
        "deadline=time.monotonic()+5\nwhile not Path('pulse').exists():\n assert time.monotonic()<deadline\n time.sleep(.01)\n" +
        ("time.sleep(30)\n" if timeout else ""))


def cleanup_child(root):
    if (root/'child.pid').exists():
        pid = int((root/'child.pid').read_text())
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def assert_drained(root):
    pid = int((root/'child.pid').read_text())
    assert not Path(f'/proc/{pid}').exists(), 'repair command left its detached descendant alive'
    pulse = (root/'pulse').read_text()
    time.sleep(.06)
    assert (root/'pulse').read_text() == pulse


@pytest.mark.parametrize('mode', ['normal', 'timeout', 'double_fork'])
def test_worker_terminal_waits_for_detached_descendants(tmp_path, mode):
    probe = tmp_path/'provider.py'
    probe.write_text(spawn_source(tmp_path, timeout=mode=='timeout', double_fork=mode=='double_fork'))
    launch = tmp_path/'launch.json'
    launch.write_text(json.dumps({'schema_version':LAUNCH_SCHEMA, 'operation_id':'test-repair',
        'cwd':str(tmp_path), 'argv':[sys.executable,str(probe)], 'timeout_seconds':1}))
    unrelated = subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    try:
        result = run_launch(launch,tmp_path/'result.json')
        assert result['status'] == ('timeout' if mode=='timeout' else 'completed'), result
        assert_drained(tmp_path)
        assert result['process_cleanup']['cleanup'] == 'complete'
        assert unrelated.poll() is None
    finally:
        cleanup_child(tmp_path)
        unrelated.terminate(); unrelated.wait(timeout=5)


def test_independent_verification_drains_detached_descendants(tmp_path):
    (tmp_path/'test_probe.py').write_text(spawn_source(tmp_path)+'\ndef test_probe(): assert True\n')
    try:
        result = execute_repair_verification_plan(worktree=str(tmp_path), verification_plan=[{
            'kind':'focused_tests','command':f'{sys.executable} -m pytest test_probe.py -q --no-cov'}])
        assert result['ok'],result
        assert_drained(tmp_path)
        assert result['results'][0]['process_cleanup']['cleanup'] == 'complete'
    finally:
        cleanup_child(tmp_path)


def test_unknown_cleanup_cannot_be_an_optional_validation_success(tmp_path, monkeypatch):
    from zf.runtime.repair_process_supervisor import RepairProcessCleanupError
    calls = []
    def uncertain(*args, **kwargs):
        calls.append(args)
        raise RepairProcessCleanupError('lost helper')
    monkeypatch.setattr('zf.runtime.run_manager_repair_validation.run_repair_command', uncertain)
    result = execute_repair_verification_plan(worktree=str(tmp_path), verification_plan=[
        {'kind':'optional', 'command':'git diff --check', 'required':False},
        {'kind':'next', 'command':'git diff --check'}])
    assert not result['ok'] and len(calls)==1
    assert result['reason'].startswith('repair_process_cleanup_unknown')
    assert result['results'][0]['process_cleanup']['cleanup']=='unknown'


def test_unknown_worker_cleanup_is_durable_failure(tmp_path, monkeypatch):
    from zf.runtime.repair_process_supervisor import RepairProcessCleanupError
    def uncertain(*args, **kwargs):
        raise RepairProcessCleanupError('lost helper')
    monkeypatch.setattr('zf.runtime.self_repair_process.run_repair_command', uncertain)
    launch = tmp_path/'launch.json'
    launch.write_text(json.dumps({'schema_version':LAUNCH_SCHEMA,'operation_id':'unknown-test',
        'argv':['fake-provider'],'cwd':str(tmp_path)}))
    result = run_launch(launch,tmp_path/'result.json')
    assert result['status']=='failed' and result['returncode']==125
    assert result['process_cleanup']['cleanup']=='unknown'
    assert json.loads((tmp_path/'result.json').read_text())==result


def test_helper_timeout_is_unknown_not_clean_command_timeout(tmp_path, monkeypatch):
    from zf.runtime.repair_process_supervisor import RepairProcessCleanupError,run_repair_command
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0],kwargs['timeout'])
    monkeypatch.setattr('zf.runtime.repair_process_supervisor.subprocess.run',timeout)
    with pytest.raises(RepairProcessCleanupError,match='cleanup unknown'):
        run_repair_command(['fake-provider'],cwd=str(tmp_path),env={},timeout=1)


def test_supervisor_drains_after_its_calling_worker_dies(tmp_path):
    probe = tmp_path/'provider.py'
    probe.write_text(spawn_source(tmp_path, timeout=True))
    parent = tmp_path/'parent.py'
    source = Path(__file__).resolve().parents[1]/'src'
    parent.write_text(f"import sys,os; sys.path.insert(0,{str(source)!r}); "
        "from zf.runtime.repair_process_supervisor import run_repair_command; "
        f"run_repair_command([sys.executable,{str(probe)!r}],cwd={str(tmp_path)!r},env=dict(os.environ),timeout=30)")
    process = subprocess.Popen([sys.executable,str(parent)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/'pulse').exists():
            assert process.poll() is None and time.monotonic()<deadline
            time.sleep(.01)
        process.kill(); process.wait(timeout=5)
        pid=int((tmp_path/'child.pid').read_text())
        deadline=time.monotonic()+5
        while Path(f'/proc/{pid}').exists() and time.monotonic()<deadline:
            time.sleep(.02)
        assert_drained(tmp_path)
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
        cleanup_child(tmp_path)


def test_stale_parent_link_cannot_signal_an_unrelated_process(monkeypatch):
    from zf.runtime import repair_process_supervisor as supervisor
    sent, closed = [], []
    monkeypatch.setattr(supervisor, '_pidfd_open', lambda _pid: 99)
    monkeypatch.setattr(supervisor, '_stat', lambda _pid: (os.getpid()+1,123,'S'))
    monkeypatch.setattr(supervisor, '_pidfd_signal', lambda *args: sent.append(args))
    monkeypatch.setattr(supervisor.os, 'close', closed.append)
    assert supervisor._signal(456,123,signal.SIGTERM) is False
    assert not sent and closed == [99]
