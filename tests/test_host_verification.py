"""Preauthorization cannot become an arbitrary host execution capability."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from zf.core.config.host_verification import HostVerificationConfig, parse_host_verification
from zf.runtime.host_verification import container_command, verify_in_container, reconcile_host_verification
from tests.test_task_host_actions import case, approve
from zf.runtime.task_host_manifest import preview

IMAGE = 'sha256:' + 'a' * 64


def policy(**changes):
    return HostVerificationConfig(enabled=True, approval_ref='owner:test', image=IMAGE,
        commands=['python3 probe.py'], max_executions_per_run=2, **changes)


def test_invalid_config_fails_closed():
    for changes in ({'image': 'image:latest'}, {'commands': []}, {'approval_ref': ''},
                    {'max_executions_per_run': 0}, {'timeout_seconds': 0}, {'unknown': True}, {'enabled': 'yes'}):
        with pytest.raises(ValueError):
            parse_host_verification({**asdict(policy()), **changes})


def test_container_has_no_host_network_credentials_socket_or_shell(tmp_path):
    argv = container_command(policy(), tmp_path / 'input', tmp_path / 'output', 'python3 probe.py', 'zf-probe-test')
    assert argv[:4] == ['docker', 'run', '--pull=never', '--rm']
    assert argv[argv.index('--network') + 1] == 'none'
    assert argv[argv.index('--user') + 1] != '0:0'
    assert '--read-only' in argv and '--cap-drop=ALL' in argv
    assert 'docker.sock' not in ' '.join(argv)
    assert IMAGE in argv
    with pytest.raises(ValueError):
        container_command(policy(), tmp_path, tmp_path, 'unapproved', 'zf-probe-test')


def prepare(case):
    service, root, _ = case
    service.config.runtime.host_verification = policy()
    approve(service, preview(service, operation_id='op-1', kind='commit', paths=['probe.py']))
    return service, root


def test_container_verification_copies_no_git_or_state_and_preserves_target(case, monkeypatch):
    service, root = prepare(case)
    manifest = preview(service, operation_id='op-1', kind='verify', command_id='contract-verification')
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        source_mount = next(v for v in argv if v.startswith('type=bind,src=') and 'dst=/source' in v)
        source = Path(source_mount.split('src=')[1].split(',')[0])
        assert (source / 'probe.py').exists() and not (source / '.git').exists()
        assert not (source / '.state').exists()
        return subprocess.CompletedProcess(argv, 0, 'probe passed', '')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', run)
    result, output = verify_in_container(service, root, manifest, policy())
    assert result['status'] == 'completed'
    assert result['execution_origin'] == 'preauthorized_container'
    assert result['source_stable'] and output == 'probe passed'
    assert len(calls) == 1


def test_reconciliation_is_budgeted_idempotent_and_does_not_mark_product_passed(case, monkeypatch):
    service, _ = prepare(case)
    monkeypatch.setattr('zf.runtime.host_verification.verify_in_container', lambda *args: (
        {'status': 'completed', 'source_stable': True, 'exit_code': 0,
         'execution_origin': 'preauthorized_container'}, 'pass'))
    assert reconcile_host_verification(service) == 1
    assert reconcile_host_verification(service) == 0
    events = service.writer.event_log.read_all()
    assert sum(e.type == 'runtime.action.attempt.started' and e.payload.get('kind') == 'verify' for e in events) == 1
    assert not any(e.type in {'test.passed', 'task.done', 'run.goal.completed'} for e in events)


def test_run_manager_tick_calls_pre_authorized_executor(case, monkeypatch):
    from zf.runtime.run_manager import run_manager_tick
    from tests.test_run_manager import _config
    service, _ = prepare(case)
    config = _config()
    config.runtime.host_verification = policy()
    called = []
    monkeypatch.setattr('zf.runtime.host_verification.reconcile_host_verification', lambda svc: called.append(svc) or 0)
    run_manager_tick(state_dir=service.state_dir, writer=service.writer, config=config,
                     project_root=service.project_root, auto_execute=True)
    assert len(called) == 1 and called[0].actor == 'run-manager'


def test_budget_exhaustion_escalates_once_without_execution(case, monkeypatch):
    service, _ = prepare(case)
    service.config.runtime.host_verification.max_executions_per_run = 1
    service.writer.emit('runtime.action.attempt.started', payload={
        'workflow_run_id': 'run-1', 'approval_ref': 'owner:test', 'manifest_digest': 'other'})
    monkeypatch.setattr('zf.runtime.host_verification.verify_in_container', lambda *args: pytest.fail('budget exhausted'))
    assert reconcile_host_verification(service) == 0
    assert reconcile_host_verification(service) == 0
    assert len([e for e in service.writer.event_log.read_all() if e.type == 'human.escalate']) == 1


@pytest.mark.host
def test_real_container_executes_bounded_probe_and_collects_evidence(case):
    service, root = prepare(case)
    image = subprocess.check_output(['docker', 'image', 'inspect', 'mcp/playwright:latest', '--format', '{{.Id}}'], text=True).strip()
    p = policy()
    p.image = image
    p.commands = ['node -e "require(\'fs\').writeFileSync(\'/evidence/result.txt\', \'actual container probe\'); console.log(\'probe passed\')"']
    manifest = preview(service, operation_id='op-1', kind='verify', command_id='contract-verification')
    # Host tier exercises the executor's actual process/evidence boundary;
    # contract admission and policy selection are covered separately above.
    manifest['command'] = {**manifest['command'], 'command': p.commands[0]}
    result, output = verify_in_container(service, root, manifest, p)
    assert result['exit_code'] == 0 and result['source_stable'], output
    assert 'probe passed' in output
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    evidence = hydrate_sidecar_ref(service.state_dir, result['evidence_ref']).payload
    assert evidence['files'][0]['path'] == 'result.txt'
    assert evidence['image'] == image


def test_modified_protected_oracle_never_starts_container(case, monkeypatch):
    service, root = prepare(case)
    p = policy()
    p.protected_files = {'probe.py': '0' * 64}
    manifest = preview(service, operation_id='op-1', kind='verify', command_id='contract-verification')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', lambda *args, **kwargs: pytest.fail('must reject before executing'))
    with pytest.raises(ValueError, match='modified or removed'):
        verify_in_container(service, root, manifest, p)


@pytest.mark.parametrize('exclusive', [False, True])
def test_container_snapshot_coexists_with_readers_but_waits_for_mutation(case, monkeypatch, exclusive):
    import fcntl
    from zf.runtime.environment_exec import resource_locks
    from zf.runtime.environment_resources import environment_lock_plan
    service, root = prepare(case)
    calls = []
    def verify(*args):
        calls.append(args)
        return {'status': 'completed', 'source_stable': True, 'exit_code': 0}, 'pass'
    monkeypatch.setattr('zf.runtime.host_verification.verify_in_container', verify)
    with resource_locks(environment_lock_plan(root), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH):
        assert reconcile_host_verification(service) == (0 if exclusive else 1)
    assert len(calls) == (0 if exclusive else 1)
    if exclusive:
        assert reconcile_host_verification(service) == 1
