"""Real Git/pytest repair path; diagnosis, provider and reboot are test inputs."""
import subprocess
import sys
from pathlib import Path

from tests.test_run_manager import _config, _state
from tests.test_source_repair_apply import _git
from zf.runtime.run_manager import (
    _emit_repair_acceptance_from_agent, _execute_repair_closeout_apply,
    _execute_repair_closeout_validate, build_run_manager_projection,
)
from zf.runtime.self_repair_runner import dispatch_pending_self_repairs, emit_self_repair_closeouts
from zf.runtime.repair_candidate_publish import publish_repair_candidate
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json


def test_repair_candidate_validates_applies_and_reopens_exact_checkpoint(tmp_path, monkeypatch):
    root = tmp_path / 'harness'
    (root / 'src/zf').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'scripts').mkdir()
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'src/zf/fault.py').write_text('VALUE = 0\n')
    (root / 'tests/test_fault.py').write_text(
        'from pathlib import Path\ndef test_fixed():\n    assert "VALUE = 1" in Path("src/zf/fault.py").read_text()\n')
    # The fixture has one module and one test; this is its entire impact closure.
    (root / 'scripts/dev-verify.py').write_text(
        'import subprocess,sys\nif sys.argv[1] == "run":\n'
        '    raise SystemExit(subprocess.call([sys.executable,"-m","pytest","tests/test_fault.py","-q","-c","/dev/null","--no-cov"]))\n')
    _git(root, 'init', '-q'); _git(root, 'config', 'user.email', 'test@example.invalid'); _git(root, 'config', 'user.name', 'Test')
    _git(root, 'add', '--', '.gitignore', 'src/zf/fault.py', 'tests/test_fault.py', 'scripts/dev-verify.py')
    _git(root, 'commit', '-qm', 'chore: broken fixture')
    base = _git(root, 'rev-parse', 'HEAD')
    command = [sys.executable, '-m', 'pytest', 'tests/test_fault.py', '-q', '-c', '/dev/null', '--no-cov']
    assert subprocess.run(command, cwd=root, capture_output=True).returncode == 1
    state, log, writer = _state(tmp_path)
    writer.default_origin = 'kernel'
    config = _config()
    policy = config.runtime.run_manager.source_repair
    policy.enabled = policy.publish_candidate = True
    policy.apply_policy = 'verified_checkpoint_apply'
    policy.restart_policy = 'operator_approved'
    policy.allow_paths = ['src/zf/**', 'tests/**', 'tasks/**']
    monkeypatch.setattr('zf.runtime.self_repair_runner.harness_root', lambda arg=None: root)
    monkeypatch.setattr('zf.runtime.run_manager._spawn_source_repair_restart_actuator', lambda **kwargs: 1234)
    # An admitted semantic diagnosis is the input; this test does not simulate an LLM.
    assert _emit_repair_acceptance_from_agent(writer, {'checkpoint_id': 'original-checkpoint',
        'safe_resume_action': 'needs_stage_dispatch', 'fingerprint': 'fixture-failure'},
        {'verification_commands': ['pytest tests/test_fault.py -q -c /dev/null --no-cov'],
         'scope': ['src/zf/**', 'tests/**']}, causation_id='fixture-failure')
    assert dispatch_pending_self_repairs(log.read_all(), writer, root=str(root), tmp_root=str(tmp_path),
        request_types=('run.manager.repair.accepted',)) == 1
    dispatched = next(e for e in log.read_all() if e.type == 'autoresearch.repair.dispatched')
    payload = dispatched.payload
    work = Path(payload['worktree'])
    contract = hydrate_sidecar_ref(state, payload['repair_contract_ref']).payload
    # Deterministic provider substitute: only the scoped source and audit record.
    (work / 'src/zf/fault.py').write_text('VALUE = 1\n')
    (work / 'tasks').mkdir()
    (work / 'tasks/repair.md').write_text('> 状态: active\n\n等待独立验证。\n')
    commit = publish_repair_candidate(writer=writer, state_dir=state, harness=root, root=work,
        payload=payload, contract=contract, policy=policy)
    assert commit != base
    assert emit_self_repair_closeouts(log.read_all(), writer, root=str(root)) == 1

    def actions():
        return build_run_manager_projection(state, events=log.read_all(), config=config)['pending_actions']

    validation = next(a for a in actions() if a['action'] == 'repair-closeout-validate')
    assert _execute_repair_closeout_validate(state_dir=state, writer=writer, action=validation, causation_id=dispatched.id) == 'applied'
    apply = next(a for a in actions() if a['action'] == 'repair-closeout-apply')
    assert apply['preflight']['status'] == 'passed'
    assert apply['validation_result']['tested_candidate']['source_commit'] == commit
    assert _execute_repair_closeout_apply(state_dir=state, writer=writer, project_root=tmp_path,
        action=apply, causation_id=dispatched.id) == 'applied'
    assert subprocess.run(command, cwd=root, capture_output=True).returncode == 0
    assert not any(a['action'] == 'workflow-batch-resume' for a in actions())
    applied = next(e.payload['apply_receipt'] for e in log.read_all() if e.type == 'run.manager.repair.merge.merged')
    ref = write_sidecar_json(state, 'diagnostics/runtime-load/mock-reboot.json', {
        'schema_version': 'runtime-load.v1', 'state_dir': str(state.resolve()),
        'source_root': str(root), 'source_revision': applied['applied_commit'],
        'source_tree_sha256': applied['source_tree_sha256'],
    }, kind='runtime-load', schema_version='runtime-load.v1', created_by='test', required=True)
    writer.emit('loop.started', payload={'runtime_load_ref': ref})
    resume = next(a for a in actions() if a['action'] == 'workflow-batch-resume')
    assert resume['checkpoint_id'] == 'original-checkpoint'
    assert not any(a['action'] == 'repair-closeout-apply' for a in actions())
    assert emit_self_repair_closeouts(log.read_all(), writer, root=str(root)) == 0
