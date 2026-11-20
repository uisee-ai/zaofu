"""Cleanup resumes exact approved resources, including a lost deletion receipt."""
import subprocess

import pytest

from tests.test_stop_clean_workdirs import _init_product_repo, _add_worker_worktree, _git
from zf.cli.stop import clean_state_dir_workdirs
from zf.core.events import EventLog


def interrupted_cleanup(root, state, monkeypatch):
    original = subprocess.run
    def interrupted(argv, **kwargs):
        if argv[:3] == ["git", "branch", "-d"]:
            raise SystemExit("crash after worktree removal")
        return original(argv, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", interrupted)
        with pytest.raises(SystemExit):
            clean_state_dir_workdirs(root, state)


def test_restart_reconciles_removed_worktree_and_remaining_branch(tmp_path, monkeypatch):
    _init_product_repo(tmp_path)
    path = _add_worker_worktree(tmp_path, "custom-state", "worker")
    state = tmp_path / "custom-state"
    interrupted_cleanup(tmp_path, state, monkeypatch)
    assert not path.exists()
    assert any(e.type == "workspace.cleanup.requested" for e in EventLog(state / "events.jsonl").read_all())
    removed, branches = clean_state_dir_workdirs(tmp_path, state)
    assert removed == 0 and branches == ["worker/worker"]
    assert clean_state_dir_workdirs(tmp_path, state) == (0, [])
    assert len([e for e in EventLog(state / "events.jsonl").read_all()
                if e.type == "workspace.cleanup.completed"]) == 1


def test_replay_does_not_delete_reused_path_or_recreated_branch(tmp_path, monkeypatch):
    from zf.runtime.workspace_cleanup_recovery import resume_cleanup

    _init_product_repo(tmp_path)
    old = _add_worker_worktree(tmp_path, "custom-state", "worker")
    state = tmp_path / "custom-state"
    interrupted_cleanup(tmp_path, state, monkeypatch)
    assert _git(tmp_path, "branch", "-d", "worker/worker").returncode == 0
    recreated = _add_worker_worktree(tmp_path, "custom-state", "worker")
    (recreated / "uncommitted.txt").write_text("do not remove")
    assert resume_cleanup(tmp_path, state) == (0, [])
    assert old.exists() and (recreated / "uncommitted.txt").read_text() == "do not remove"
    events = EventLog(state / "events.jsonl").read_all()
    assert any(e.type == "workspace.cleanup.blocked" and "identity" in e.payload["reason"] for e in events)


def test_active_canonical_task_blocks_cleanup_even_without_process(tmp_path):
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore

    _init_product_repo(tmp_path)
    path = _add_worker_worktree(tmp_path, "custom-state", "worker")
    state = tmp_path / "custom-state"
    TaskStore(state / "kanban.json").add(Task(id="T1", status="in_progress", assigned_to="dev"))
    assert clean_state_dir_workdirs(tmp_path, state) == (0, [])
    assert path.exists()
    assert any(e.type == "workspace.cleanup.blocked" for e in EventLog(state / "events.jsonl").read_all())


def test_corrupt_ledger_is_preserved_and_never_authorizes_cleanup(tmp_path):
    _init_product_repo(tmp_path)
    path = _add_worker_worktree(tmp_path, "custom-state", "worker")
    state = tmp_path / "custom-state"
    corrupt = b'{"type":"task.dispatched",broken}\n'
    (state / "events.jsonl").write_bytes(corrupt)
    assert clean_state_dir_workdirs(tmp_path, state) == (0, [])
    assert path.exists()
    assert (state / "events.jsonl").read_bytes().startswith(corrupt)
    assert any(e.type == "workspace.cleanup.blocked" and "integrity unknown" in e.payload["reason"]
               for e in EventLog(state / "events.jsonl").read_all())


def test_resume_cli_does_not_enqueue_new_workspaces(tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    from zf.cli.stop import _maybe_clean_workdirs

    _init_product_repo(tmp_path)
    _add_worker_worktree(tmp_path, "custom-state", "worker")
    state = tmp_path / "custom-state"
    interrupted_cleanup(tmp_path, state, monkeypatch)
    new = _add_worker_worktree(tmp_path, "custom-state", "new-worker")
    _maybe_clean_workdirs(Namespace(clean_workdirs=False, resume_cleanup=True), tmp_path, state)
    assert new.exists()
    assert "1 worker branch(es) deleted" in capsys.readouterr().out
    assert _git(tmp_path, "branch", "--list", "worker/new-worker").stdout.strip()


@pytest.mark.parametrize("lost_receipt", ["worktree_removed", "completed"])
def test_effect_applied_but_receipt_lost_is_reconciled_without_repeating_deletion(tmp_path, monkeypatch, lost_receipt):
    from zf.core.events import EventWriter
    from zf.runtime.workspace_cleanup_recovery import resume_cleanup

    _init_product_repo(tmp_path)
    path = _add_worker_worktree(tmp_path, "custom-state", "worker")
    state = tmp_path / "custom-state"
    append = EventWriter.append
    def crash(self, event, *args, **kwargs):
        if event.type == "workspace.cleanup." + lost_receipt:
            raise SystemExit("effect applied, receipt not written")
        return append(self, event, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(EventWriter, "append", crash)
        with pytest.raises(SystemExit):
            clean_state_dir_workdirs(tmp_path, state)
    assert not path.exists()
    assert not any(e.type == "workspace.cleanup.completed" for e in EventLog(state / "events.jsonl").read_all())
    removed, _ = resume_cleanup(tmp_path, state)
    assert removed == 0
    assert not _git(tmp_path, "branch", "--list", "worker/worker").stdout.strip()
    assert resume_cleanup(tmp_path, state) == (0, [])
    assert len([e for e in EventLog(state / "events.jsonl").read_all()
                if e.type == "workspace.cleanup.completed"]) == 1


def test_valid_rejection_event_is_not_confused_with_corrupt_ledger(tmp_path):
    from zf.core.events import EventWriter, ZfEvent

    _init_product_repo(tmp_path)
    path = _add_worker_worktree(tmp_path, "custom-state", "worker")
    state = tmp_path / "custom-state"
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(type="event.malformed", origin="kernel",
        payload={"reason": "missing_task_id"}))
    assert clean_state_dir_workdirs(tmp_path, state)[0] == 1
    assert not path.exists()


def test_stop_parser_separates_new_cleanup_from_resume_only():
    import argparse
    from zf.cli.stop import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(["stop", "--resume-cleanup"])
    assert args.resume_cleanup and not args.clean_workdirs
    with pytest.raises(SystemExit):
        parser.parse_args(["stop", "--resume-cleanup", "--clean-workdirs"])
