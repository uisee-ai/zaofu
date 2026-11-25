"""Real Git/pytest repair path; diagnosis, provider and reboot are test inputs."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict

import pytest
import yaml

from zf.core.config.loader import load_config

from zf.core.config.schema import RuntimeRecoveryAuthorization
from zf.core.events import ZfEvent
from zf.runtime.run_admission import admit_workflow_invoke

from tests.test_run_manager import _config, _state
from tests.test_source_repair_apply import _git
from zf.runtime.run_manager import (
    _emit_repair_acceptance_from_agent, _execute_repair_closeout_apply,
    _execute_repair_closeout_validate, build_run_manager_projection,
)
from zf.runtime.self_repair_runner import dispatch_pending_self_repairs, emit_self_repair_closeouts
from zf.runtime.repair_candidate_publish import publish_repair_candidate
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json


@pytest.mark.parametrize("preauthorized,signed", [(False, False), (True, False), (True, True)])
def test_repair_candidate_validates_applies_and_reopens_exact_checkpoint(tmp_path, monkeypatch, preauthorized, signed):
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
    launches = []
    def launch(**kwargs):
        launches.append(kwargs)
        return os.getpid()
    monkeypatch.setattr('zf.runtime.run_manager._spawn_source_repair_restart_actuator', launch)
    run = {}
    if preauthorized:
        run = {"workflow_run_id": "original-run"}
        config.runtime.run_manager.recovery_authorization = RuntimeRecoveryAuthorization(
            "owner:fixture-policy", max_checkpoint_restarts_per_run=1, safe_resume_actions=["needs_stage_dispatch"])
        raw = {"project": {"name": "restart-policy", "state_dir": str(state)}, "runtime": {"run_manager": {
            "source_repair": asdict(policy),
            "recovery_authorization": asdict(config.runtime.run_manager.recovery_authorization)}}}
        if signed:
            monkeypatch.setenv("ZF_TEST_CHECKPOINT_HMAC", os.urandom(32).hex())
            raw["security"] = {"event_signing": {"enabled": True, "secret_env": "ZF_TEST_CHECKPOINT_HMAC"}}
        config_path = tmp_path / "zf.yaml"
        config_path.write_text(yaml.safe_dump(raw))
        config = load_config(config_path)
        if signed:
            from zf.core.events.factory import event_log_from_project
            from zf.core.events.writer import EventWriter

            log = event_log_from_project(state, config=config)
            writer = EventWriter(log, default_origin="kernel")
        policy = config.runtime.run_manager.source_repair
        runtime = SimpleNamespace(state_dir=state, project_root=tmp_path, config=config, event_log=log, event_writer=writer)
        assert admit_workflow_invoke(runtime, ZfEvent(type="workflow.invoke.requested", payload=run)).status == "admitted"
    # An admitted semantic diagnosis is the input; this test does not simulate an LLM.
    assert _emit_repair_acceptance_from_agent(writer, {**run, 'checkpoint_id': 'original-checkpoint',
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
    assert apply['preflight']['status'] == 'passed', apply['preflight']
    assert apply['validation_result']['tested_candidate']['source_commit'] == commit
    if preauthorized:
        forged = {**apply, "continuation_checkpoint_id": "unrelated-checkpoint"}
        assert _execute_repair_closeout_apply(state_dir=state, writer=writer, project_root=tmp_path,
            config=config, action=forged, causation_id=dispatched.id) == "blocked"
        assert _git(root, "rev-parse", "HEAD") == base
    assert _execute_repair_closeout_apply(state_dir=state, writer=writer, project_root=tmp_path,
        config=config, action=apply, causation_id=dispatched.id) == 'applied'
    assert subprocess.run(command, cwd=root, capture_output=True).returncode == 0
    if preauthorized:
        assert _execute_repair_closeout_apply(state_dir=state, writer=writer, project_root=tmp_path,
            config=config, action=apply, causation_id=dispatched.id) == "blocked"
        assert sum(e.type == "run.manager.repair.merge.merging" for e in log.read_all()) == 1
        from zf.runtime import source_repair_restart_actuator as actuator
        assert len(launches) == 1
        merge_id = launches[0]["authorized_merge_event"]
        argv = ["--project-root", str(tmp_path), "--state-dir", str(state), "--authorized-merge-event", merge_id]
        assert launches[0]["require_signed_ledger"] is signed
        if signed:
            argv.append("--require-signed-ledger")
        commands = []
        def supervise(command):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "1234\n", "")
        monkeypatch.setattr(actuator.time, "sleep", lambda _: None)
        monkeypatch.setattr(actuator, "_recorded_watcher_pid", lambda _: 1234)
        monkeypatch.setattr(actuator, "_systemctl", supervise)
        monkeypatch.setenv("ZF_HOST_SUPERVISOR_UNIT", "zf-test.service")
        assert actuator.main(argv) == 0
        assert len(commands) == 2
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
    if preauthorized:
        # A policy change between the queued restart and the watcher signal is a human boundary.
        del raw["runtime"]["run_manager"]["recovery_authorization"]
        config_path.write_text(yaml.safe_dump(raw))
        assert actuator.main(argv) == 1
        assert len(commands) == 2
        assert any(e.type == "human.escalate" and e.payload.get("reason") == "run_recovery_authorization_changed"
                   for e in log.read_all())
        assert not any(e.type == "event.malformed" for e in log.read_all())
