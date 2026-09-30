"""Offline guards for native CLI lifecycle evidence; never invokes a Provider."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.conformance.test_zaofu_testkit_s5_native_cli_lifecycle import (
    NativeCLI, assert_replacement, graceful_snapshot_evidence,
)
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.memory.store import MemoryStore
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.shutdown import GracefulShutdown
from zf.runtime.workflow_operation import WorkflowOperationService


@pytest.mark.parametrize("stage", ["impl", "verify"])
@pytest.mark.parametrize("corruption", [None, "operation", "attempt", "dispatch", "lease", "old-status", "binding", "new-status",
                                       "task_stage_session_binding", "provider_session_id", "workdir"])
def test_replacement_oracle_rejects_false_recovery(corruption, stage):
    old = {"operation_id": "op", "task_pipeline_stage": stage, "task_id": "T",
           "task_stage_session_binding": "binding-1", "provider_session_id": "session-1",
           "workdir": "/tmp/zf-native-oracle/workdir", "target_ref": "base-commit", "target_commit": "commit-1",
           "attempt_id": "a1", "dispatch_id": "d1", "lease_id": "l1"}
    new = {**old, "attempt_id": "a2", "dispatch_id": "d2", "lease_id": "l2"}
    attempts = {"a1": {"status": "superseded", "superseded_by": "a2"},
                "a2": {"status": "succeeded"}}
    if corruption == "operation":
        new["operation_id"] = "foreign"
    elif corruption in {"attempt", "dispatch", "lease"}:
        new[f"{corruption}_id"] = old[f"{corruption}_id"]
    elif corruption == "old-status":
        attempts["a1"]["status"] = "sent"
    elif corruption == "binding":
        attempts["a1"]["superseded_by"] = "foreign"
    elif corruption == "new-status":
        attempts["a2"]["status"] = "sent"
    elif corruption in {"task_stage_session_binding", "provider_session_id", "workdir"}:
        new[corruption] = "foreign"
    before = deepcopy((old, new, attempts))
    if corruption is None:
        assert_replacement(old, new, attempts)
    else:
        with pytest.raises(AssertionError):
            assert_replacement(old, new, attempts)
    assert (old, new, attempts) == before


def test_verify_replacement_rejects_changed_pinned_commit():
    old = {"operation_id": "op", "task_pipeline_stage": "verify", "task_id": "T",
           "task_stage_session_binding": "binding-1", "provider_session_id": "session-1",
           "workdir": "/tmp/zf-native-oracle/workdir", "target_ref": "base-commit", "target_commit": "commit-1",
           "attempt_id": "a1", "dispatch_id": "d1", "lease_id": "l1"}
    new = {**old, "target_commit": "commit-2", "attempt_id": "a2", "dispatch_id": "d2", "lease_id": "l2"}
    with pytest.raises(AssertionError):
        assert_replacement(old, new, {
            "a1": {"status": "superseded", "superseded_by": "a2"},
            "a2": {"status": "succeeded"},
        })


def test_pid_observation_ignores_unspawned_roles_but_records_active_tree():
    cli = NativeCLI.__new__(NativeCLI)
    cli.pids = set()
    cli.runtime = SimpleNamespace(config=SimpleNamespace(roles=[
        SimpleNamespace(instance_id="impl-1"), SimpleNamespace(instance_id="verify-1"),
    ]))
    cli.tmux = SimpleNamespace(
        has_session=lambda: True,
        pane_process_probe=lambda role: (
            {"available": True, "pane_pid": "123", "processes": [{"pid": "124"}]}
            if role == "impl-1" else {"available": False, "pane_pid": "", "processes": []}
        ),
    )
    cli.observe_pids()
    assert cli.pids == {"123", "124"}


@pytest.mark.parametrize("corruption", [None, "missing-log", "memory-copy", "ledger-copy"])
def test_graceful_snapshot_checks_real_shutdown_copies(tmp_path, corruption):
    state = tmp_path / "runtime-state"
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name="snapshot-oracle", state_dir=str(state)),
                      roles=[RoleConfig(name="impl", backend="mock")])
    SessionStore(state / "session.yaml").create(project_root=str(tmp_path))
    TaskStore(state / "kanban.json").add(Task(id="T", title="snapshot"))
    MemoryStore(state / "memory").add(None, "decision", "preserve this memory")
    memory = {p.name: p.read_bytes() for p in (state / "memory").iterdir() if p.is_file()}
    log = EventLog(state / "events.jsonl")
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log))
    op = service.ensure_operation(workflow_run_id="R", operation_id="op", operation_type="agent",
                                  request={"prompt": "work"}, task_id="T", parent_task_id="T")
    service.mark_started(operation_id="op", request_hash=op.request_hash, workflow_run_id="R",
                         task_id="T", dispatch_id="D")
    transport = MagicMock()
    transport.capture_log.return_value = "owned role output\n"
    GracefulShutdown(state, transport, config=config).execute()
    snapshot = state / "last-shutdown"
    if corruption == "missing-log":
        (state / "logs/impl.log").unlink()
    elif corruption == "memory-copy":
        (snapshot / "memory" / next(iter(memory))).write_bytes(b"wrong")
    elif corruption == "ledger-copy":
        (snapshot / "events.jsonl").write_text((state / "events.jsonl").read_text())
    if corruption is None:
        evidence = graceful_snapshot_evidence(state, "op", "impl", memory)
        assert evidence["reason"] == "graceful_stop"
        assert evidence["snapshot_event_count"] < evidence["ledger_event_count"]
        assert "last-shutdown/events.jsonl" in evidence["digests"]
    else:
        with pytest.raises((AssertionError, FileNotFoundError)):
            graceful_snapshot_evidence(state, "op", "impl", memory)
