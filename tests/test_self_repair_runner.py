"""doc 80 P3 activation (R20-B2): the authorized self-repair dispatch consumer
runs from the watcher tick, not just the operator-run CLI.

R20 dead-ended because autoresearch emitted ``autoresearch.repair.dispatch_requested``
but nothing ran the consumer — the stall never self-healed. ``dispatch_pending_self_repairs``
is that consumer (shared by the CLI and the tick): pending dispatch_requested →
isolated zaofu worktree + briefing + emit ``autoresearch.repair.dispatched`` + (spawn)
a headless agent. Idempotent + events-derived (a dispatched present → not pending).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.repair_dispatch import RepairRequest, write_repair_contract
from zf.runtime.self_repair_runner import (
    CLOSEOUT_REQUIRED,
    DISPATCH_BLOCKED,
    REPAIR_TERMINATED,
    dispatch_pending_self_repairs,
    emit_self_repair_closeouts,
)
from zf.runtime.sidecar_refs import sidecar_path


def _dispatch_requested(fp: str = "stall:X", attempt: int = 0) -> ZfEvent:
    return ZfEvent(
        type="autoresearch.repair.dispatch_requested",
        payload={
            "fingerprint": fp,
            "attempt": attempt,
            "candidate_id": "C-1",
            "candidate_path": "/x/cand.md",
            "repair_task_payload": {"contract": {"scope": ["src/zf/**"], "verification": "pytest x"}},
        },
    )


def _writer(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    return log, EventWriter(log)


def _git(cwd, *args):
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_pending_dispatch_emits_dispatched_and_spawns(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()], writer,
            root=str(tmp_path),
            spawn=True,
            backend="claude-code",
            tmp_root=str(tmp_path),
        )
    assert n == 1
    dispatched = [e for e in log.read_all() if e.type == "autoresearch.repair.dispatched"]
    assert dispatched, "must emit autoresearch.repair.dispatched (closes the dead-end)"
    assert dispatched[0].payload["fingerprint"] == "stall:X"
    assert dispatched[0].payload["skill"] == "zf-self-repair"
    assert dispatched[0].payload["repair_contract_ref"]["ref"].startswith(
        "artifacts/self-repair/contracts/"
    )
    assert dispatched[0].payload["repair_contract_digest"] == (
        dispatched[0].payload["repair_contract_ref"]["sha256"]
    )
    mpopen.assert_called_once()  # headless self-repair agent spawned


def test_claude_code_backend_uses_claude_cli_spawn_command(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="claude-code",
            tmp_root=str(tmp_path),
        )
    assert n == 1
    spawned = next(
        event for event in log.read_all()
        if event.type == "autoresearch.repair.spawned"
    )
    launch = json.loads(sidecar_path(
        tmp_path,
        spawned.payload["launch_ref"]["ref"],
    ).read_text(encoding="utf-8"))
    command = launch["argv"]
    assert command[:3] == ["claude", "--permission-mode", "default"]


def test_codex_backend_uses_codex_exec_spawn_command(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="codex",
            tmp_root=str(tmp_path),
        )
    assert n == 1
    spawned = next(
        event for event in log.read_all()
        if event.type == "autoresearch.repair.spawned"
    )
    launch = json.loads(sidecar_path(
        tmp_path,
        spawned.payload["launch_ref"]["ref"],
    ).read_text(encoding="utf-8"))
    command = launch["argv"]
    assert command[:4] == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
    ]
    assert command[-1].startswith("# Authorized self-repair")


def test_spawn_without_backend_is_blocked_not_guessed(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="",
            tmp_root=str(tmp_path),
        )

    events = log.read_all()
    assert n == 1
    mpopen.assert_not_called()
    blocked = [event for event in events if event.type == DISPATCH_BLOCKED]
    assert blocked
    assert blocked[-1].payload["reason"] == "self_repair_backend_not_configured"


def test_no_pending_is_no_op(tmp_path):
    log, writer = _writer(tmp_path)
    n = dispatch_pending_self_repairs([], writer, root=str(tmp_path), tmp_root=str(tmp_path))
    assert n == 0
    assert not list(log.read_all())


def test_idempotent_when_already_dispatched(tmp_path):
    """A dispatched already present → not pending → no re-dispatch (doc 80 inv 4)."""
    log, writer = _writer(tmp_path)
    events = [
        _dispatch_requested(),
        ZfEvent(type="autoresearch.repair.dispatched", payload={"fingerprint": "stall:X", "attempt": 0}),
    ]
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(events, writer, root=str(tmp_path), tmp_root=str(tmp_path))
    assert n == 0


def test_no_spawn_still_prepares(tmp_path):
    """spawn=False still emits dispatched (prepare) — the dead-end is closed even
    without the agent launch; the spawn is the extra unattended step."""
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()], writer, root=str(tmp_path), spawn=False, tmp_root=str(tmp_path),
        )
    assert n == 1
    assert any(e.type == "autoresearch.repair.dispatched" for e in log.read_all())
    mpopen.assert_not_called()


def test_spawn_failure_emits_dispatch_blocked(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        mpopen.side_effect = FileNotFoundError("codex not found")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="codex",
            tmp_root=str(tmp_path),
        )

    events = log.read_all()
    assert n == 1
    assert any(event.type == "autoresearch.repair.dispatched" for event in events)
    blocked = [event for event in events if event.type == DISPATCH_BLOCKED]
    assert blocked
    assert blocked[-1].payload["reason"] == "spawn_failed"
    assert blocked[-1].payload["backend"] == "codex"


def test_closeout_required_emitted_when_repair_worktree_has_commit(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    base_commit = _git(root, "rev-parse", "HEAD")
    branch = "self-repair/stall-X-a0"
    worktree = tmp_path / "worktree"
    _git(root, "worktree", "add", "-B", branch, str(worktree), "HEAD")
    (worktree / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git(worktree, "add", "fix.txt")
    _git(worktree, "commit", "-q", "-m", "fix: repair stall")
    log, writer = _writer(tmp_path)
    repair_contract, repair_contract_ref = write_repair_contract(
        tmp_path,
        RepairRequest(
            fingerprint="stall:X",
            attempt=0,
            candidate_id="C-1",
            candidate_path="/x/cand.md",
            repair_task_payload={
                "contract": {
                    "scope": ["fix.txt"],
                    "verification": "focused regression passes",
                    "verification_commands": ["pytest x"],
                },
                "continuation": {
                    "schema_version": "self-repair.request-continuation.v1",
                    "recovery_case_id": "case-original",
                    "workflow_run_id": "workflow-original",
                    "task_map_generation": "generation-original",
                    "target_commit": "candidate-original",
                    "active_attempt_id": "attempt-original",
                    "run_id": "",
                    "fanout_id": "",
                    "source_event_ids": [],
                    "checkpoint_id": "checkpoint-1",
                    "safe_resume_action": "needs_stage_dispatch",
                },
            },
        ),
        base_commit=base_commit,
        created_by="test",
    )
    dispatched = ZfEvent(
        type="autoresearch.repair.dispatched",
        payload={
            "fingerprint": "stall:X",
            "attempt": 0,
            "candidate_id": "C-1",
            "branch": branch,
            "worktree": str(worktree),
            "base_commit": base_commit,
            "continuation": repair_contract["continuation"],
            "repair_contract_ref": repair_contract_ref,
            "repair_contract_digest": repair_contract_ref["sha256"],
        },
    )

    count = emit_self_repair_closeouts([dispatched], writer, root=str(root))

    assert count == 1
    closeout = [event for event in log.read_all() if event.type == CLOSEOUT_REQUIRED][-1]
    assert closeout.payload["fingerprint"] == "stall:X"
    assert closeout.payload["source_title"] == "fix: repair stall"
    assert closeout.payload["base_commit"] == base_commit
    assert closeout.payload["repair_commit"] == closeout.payload["source_commit"]
    assert closeout.payload["restart_required"] is False
    assert (
        closeout.payload["restart_strategy"]
        == "control_plane_restart_preserve_run_manager"
    )
    assert closeout.payload["safe_boundary"] == "terminal_or_operator_approved_checkpoint"
    assert closeout.payload["state_snapshot_required"] is True
    assert closeout.payload["replay_required"] is True
    assert closeout.payload["auto_merge"] is False
    assert closeout.payload["risk_classification"]["risk"] == "high"
    assert closeout.payload["risk_classification"]["human_approval_required"] is True
    assert closeout.payload["changed_files"] == ["fix.txt"]
    assert closeout.payload["verification_plan"][0]["command"] == "git diff --check"
    assert closeout.payload["verification_plan"][1]["command"] == "pytest x"
    assert closeout.payload["repair_contract_digest"] == repair_contract_ref["sha256"]
    assert closeout.payload["continuation"]["resume_original_workflow"] is True
    assert closeout.payload["continuation"]["checkpoint_id"] == "checkpoint-1"
    assert (
        closeout.payload["continuation"]["safe_resume_action"]
        == "needs_stage_dispatch"
    )
    assert closeout.payload["continuation"]["restart_required"] is False
    assert (
        closeout.payload["continuation"]["resume_strategy"]
        == "snapshot_replay_then_preserve_run_manager_control_plane_restart"
    )

    # Exercise the actual consumer, not just a few projected fields. The live
    # request includes schema, empty identity slots and recovery bindings.
    from zf.runtime.repair_dispatch import validate_repair_contract_action, RepairContractError
    validate_repair_contract_action(tmp_path, closeout.payload)
    for field in repair_contract["continuation"]:
        changed = dict(closeout.payload)
        changed["continuation"] = {**closeout.payload["continuation"], field: "drift"}
        with pytest.raises(RepairContractError, match="continuation"):
            validate_repair_contract_action(tmp_path, changed)

    second = emit_self_repair_closeouts(
        [dispatched, closeout],
        writer,
        root=str(root),
    )
    assert second == 0


def test_closeout_missing_immutable_contract_is_terminal_blocked(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    log, writer = _writer(tmp_path)
    dispatched = ZfEvent(
        type="autoresearch.repair.dispatched",
        payload={
            "fingerprint": "stall:missing-contract",
            "attempt": 0,
            "candidate_id": "C-MISSING",
            "worktree": str(root),
            "base_commit": _git(root, "rev-parse", "HEAD"),
        },
    )

    assert emit_self_repair_closeouts([dispatched], writer, root=str(root)) == 0

    terminal = next(
        event for event in log.read_all() if event.type == REPAIR_TERMINATED
    )
    assert terminal.payload["status"] == "blocked"
    assert terminal.payload["reason"] == "repair_contract_missing"


def test_spawned_repair_success_without_commit_is_terminal(tmp_path):
    root, worktree, base_commit = _repair_worktree(tmp_path)
    log, writer = _writer(tmp_path)
    dispatched, spawned = _repair_process_events(
        tmp_path,
        worktree=worktree,
        base_commit=base_commit,
        status="completed",
        returncode=0,
    )

    assert emit_self_repair_closeouts(
        [dispatched, spawned], writer, root=str(root)
    ) == 0

    terminal = next(
        event for event in log.read_all() if event.type == REPAIR_TERMINATED
    )
    assert terminal.payload["status"] == "no_commit"
    assert terminal.payload["process_result"]["returncode"] == 0


@pytest.mark.parametrize(
    ("process_status", "returncode", "expected_status"),
    [("failed", 3, "failed"), ("timeout", 124, "timeout")],
)
def test_spawned_repair_failure_has_terminal_fact(
    tmp_path,
    process_status,
    returncode,
    expected_status,
):
    root, worktree, base_commit = _repair_worktree(tmp_path)
    log, writer = _writer(tmp_path)
    dispatched, spawned = _repair_process_events(
        tmp_path,
        worktree=worktree,
        base_commit=base_commit,
        status=process_status,
        returncode=returncode,
    )

    assert emit_self_repair_closeouts(
        [dispatched, spawned], writer, root=str(root)
    ) == 0

    terminal = next(
        event for event in log.read_all() if event.type == REPAIR_TERMINATED
    )
    assert terminal.payload["status"] == expected_status
    assert terminal.payload["process_result"]["returncode"] == returncode


def _repair_worktree(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    base_commit = _git(root, "rev-parse", "HEAD")
    worktree = tmp_path / "worktree"
    _git(root, "worktree", "add", "-B", "self-repair/test", str(worktree), "HEAD")
    return root, worktree, base_commit


def _repair_process_events(
    state_dir: Path,
    *,
    worktree: Path,
    base_commit: str,
    status: str,
    returncode: int,
) -> tuple[ZfEvent, ZfEvent]:
    contract, descriptor = write_repair_contract(
        state_dir,
        RepairRequest(
            fingerprint="stall:process",
            attempt=0,
            candidate_id="C-PROCESS",
            candidate_path="/x/candidate.md",
            repair_task_payload={
                "contract": {
                    "scope": ["src/zf/**"],
                    "verification_commands": ["git diff --check"],
                },
            },
        ),
        base_commit=base_commit,
        created_by="test",
    )
    dispatched = ZfEvent(
        type="autoresearch.repair.dispatched",
        payload={
            "fingerprint": contract["fingerprint"],
            "attempt": contract["attempt"],
            "candidate_id": contract["candidate_id"],
            "worktree": str(worktree),
            "base_commit": base_commit,
            "repair_contract_ref": descriptor,
            "repair_contract_digest": descriptor["sha256"],
        },
    )
    operation_id = "self-repair-test-operation"
    result_ref = f"operator/self-repair/processes/{operation_id}.json"
    result_path = sidecar_path(state_dir, result_ref)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({
        "schema_version": "self-repair.process-result.v1",
        "operation_id": operation_id,
        "repair_contract_digest": descriptor["sha256"],
        "status": status,
        "returncode": returncode,
        "reason": "provider_timeout" if status == "timeout" else "",
    }) + "\n", encoding="utf-8")
    spawned = ZfEvent(
        type="autoresearch.repair.spawned",
        payload={
            "fingerprint": contract["fingerprint"],
            "attempt": contract["attempt"],
            "candidate_id": contract["candidate_id"],
            "operation_id": operation_id,
            "provider_operation_id": operation_id,
            "result_ref": result_ref,
            "repair_contract_digest": descriptor["sha256"],
            "timeout_seconds": 60,
            "supervisor_pid": 0,
        },
    )
    return dispatched, spawned


def test_self_repair_never_disables_provider_sandbox_and_accepts_explicit_model():
    from zf.runtime.self_repair_runner import _spawn_command
    command = _spawn_command('codex', worktree=Path('/tmp/isolated-repair'), briefing='repair',
                             model='gpt-6.1-terra', effort='high')
    assert not any('dangerously' in arg for arg in command)
    assert command[command.index('--sandbox') + 1] == 'workspace-write'
    assert command[command.index('--model') + 1] == 'gpt-6.1-terra'
    assert 'model_reasoning_effort="high"' in command


def test_closeout_calls_authorized_kernel_publisher_before_independent_validation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zf.core.config.schema import ZfConfig
    root, worktree, base = _repair_worktree(tmp_path)
    log, writer = _writer(tmp_path)
    dispatched, spawned = _repair_process_events(tmp_path, worktree=worktree, base_commit=base,
                                                status='completed', returncode=0)
    dispatched.payload['branch'] = 'self-repair/test'
    config = ZfConfig()
    policy = config.runtime.run_manager.source_repair
    policy.enabled = policy.publish_candidate = True
    policy.apply_policy = 'verified_checkpoint_apply'
    policy.allow_paths = ['src/zf/**']
    monkeypatch.setattr('zf.core.config.project_context.resolve_project_context', lambda **kwargs: SimpleNamespace(config=config))
    (worktree / 'src/zf').mkdir(parents=True)
    (worktree / 'src/zf/fixed.py').write_text('fixed = True\n')
    assert emit_self_repair_closeouts([dispatched, spawned], writer, root=str(root)) == 1
    closeout = next(e for e in log.read_all() if e.type == CLOSEOUT_REQUIRED)
    assert closeout.payload['repair_commit'] != base
    assert closeout.payload['repair_contract_digest'] == dispatched.payload['repair_contract_digest']
    assert _git(root, 'rev-parse', 'HEAD') == base
    assert not any(e.type == 'run.manager.repair.merge.merged' for e in log.read_all())


def test_dispatch_freezes_checkout_relative_contract_and_absolute_evidence_locator(tmp_path):
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    log, writer = _writer(tmp_path)
    event = _dispatch_requested()
    event.payload['repair_task_payload']['contract'].update(
        scope=[str(tmp_path / 'src/zf/fix.py')],
        verification_commands=[f'cd {tmp_path} && .venv/bin/python -m pytest tests/test_fix.py'],
    )
    with patch('zf.runtime.self_repair_runner.subprocess.run') as run:
        run.return_value = MagicMock(returncode=0, stderr='')
        assert dispatch_pending_self_repairs([event], writer, root=str(tmp_path), tmp_root=str(tmp_path)) == 1
    dispatched = next(e for e in log.read_all() if e.type == 'autoresearch.repair.dispatched')
    contract = hydrate_sidecar_ref(tmp_path, dispatched.payload['repair_contract_ref']).payload
    assert contract['scope'] == ['src/zf/fix.py']
    assert 'PYTHONPATH=src' in contract['verification_commands'][0]
    briefing = Path(dispatched.payload['briefing_path']).read_text()
    assert str(tmp_path / dispatched.payload['repair_contract_ref']['ref']) in briefing
    assert 'kernel dispatch authorization' in briefing
