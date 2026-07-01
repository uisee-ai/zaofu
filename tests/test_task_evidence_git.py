"""Tests for Gap #1: git log + files_touched in TaskEvidence.

TaskEvidence gains two new fields:
  - commits: list[str]   — SHAs between dispatch HEAD and completion HEAD
  - files_touched: list[str] — files changed across those commits

git_capture gains two new helpers:
  - capture_commits_since(project_root, since_sha) -> list[str]
  - capture_files_changed(project_root, since_sha) -> list[str]

Orchestrator hooks:
  - _dispatch_task records HEAD into _dispatch_heads[task_id]
  - _on_test_passed captures evidence and emits task.files_touched event
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from zf.core.task.schema import Task, TaskEvidence


# -- Schema tests --

class TestTaskEvidenceNewFields:
    def test_evidence_has_commits_field(self):
        ev = TaskEvidence(commits=["abc123", "def456"])
        assert ev.commits == ["abc123", "def456"]

    def test_evidence_has_files_touched_field(self):
        ev = TaskEvidence(files_touched=["src/foo.py", "tests/test_foo.py"])
        assert ev.files_touched == ["src/foo.py", "tests/test_foo.py"]

    def test_evidence_defaults_empty(self):
        ev = TaskEvidence()
        assert ev.commits == []
        assert ev.files_touched == []

    def test_evidence_serializes(self):
        ev = TaskEvidence(
            commit="abc123",
            commits=["abc123"],
            files_touched=["a.py"],
        )
        d = asdict(ev)
        assert d["commits"] == ["abc123"]
        assert d["files_touched"] == ["a.py"]


# -- git_capture helper tests --

@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with two commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    # Commit 1
    (repo / "a.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


def _add_commit(repo: Path, filename: str, content: str, msg: str) -> str:
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=repo, capture_output=True, check=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class TestCaptureCommitsSince:
    def test_returns_commits_after_since_sha(self, git_repo):
        from zf.runtime.git_capture import capture_commits_since

        # Record HEAD before new work
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        since = result.stdout.strip()

        sha1 = _add_commit(git_repo, "b.py", "b = 2\n", "add b")
        sha2 = _add_commit(git_repo, "c.py", "c = 3\n", "add c")

        commits = capture_commits_since(git_repo, since)
        assert len(commits) == 2
        assert sha1 in commits
        assert sha2 in commits

    def test_returns_empty_when_no_new_commits(self, git_repo):
        from zf.runtime.git_capture import capture_commits_since

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        since = result.stdout.strip()

        commits = capture_commits_since(git_repo, since)
        assert commits == []

    def test_returns_empty_on_bad_sha(self, git_repo):
        from zf.runtime.git_capture import capture_commits_since

        commits = capture_commits_since(git_repo, "0000000000000000")
        assert commits == []


class TestCaptureFilesChanged:
    def test_returns_changed_files(self, git_repo):
        from zf.runtime.git_capture import capture_files_changed

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        since = result.stdout.strip()

        _add_commit(git_repo, "b.py", "b = 2\n", "add b")
        _add_commit(git_repo, "c.py", "c = 3\n", "add c")

        files = capture_files_changed(git_repo, since)
        assert set(files) == {"b.py", "c.py"}

    def test_returns_empty_when_no_changes(self, git_repo):
        from zf.runtime.git_capture import capture_files_changed

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        since = result.stdout.strip()

        files = capture_files_changed(git_repo, since)
        assert files == []


class TestCaptureFilesTouchedSince:
    def test_includes_uncommitted_and_untracked_files(self, git_repo):
        from zf.runtime.git_capture import capture_files_touched_since

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        since = result.stdout.strip()

        (git_repo / "a.py").write_text("a = 2\n")
        (git_repo / "new.py").write_text("new = True\n")

        files = capture_files_touched_since(git_repo, since)

        assert "a.py" in files
        assert "new.py" in files


class TestGitDiffContext:
    def test_context_renders_base_commits_files_and_dirty_state(self, git_repo):
        from zf.runtime.git_capture import (
            capture_git_diff_context,
            render_git_diff_context,
        )

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        since = result.stdout.strip()
        _add_commit(git_repo, "b.py", "b = 2\n", "add b")
        (git_repo / "dirty.py").write_text("dirty = True\n")

        context = capture_git_diff_context(git_repo, base_sha=since)
        rendered = render_git_diff_context(context)

        assert context.base_sha == since
        assert context.diff_hash
        assert any("add b" in item for item in context.commits)
        assert "b.py" in context.files_touched
        assert "dirty.py" in context.files_touched
        assert "**Base**" in rendered
        assert "Diff hash" in rendered
        assert "add b" in rendered
        assert "dirty.py" in rendered


# -- Orchestrator integration tests --

class TestDispatchRecordsHead:
    def test_dispatch_stores_head_sha_in_git_repo(self, tmp_path, config, transport):
        """When state_dir is inside a git repo, dispatch records HEAD."""
        repo = tmp_path / "proj"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo, capture_output=True, check=True,
        )
        (repo / "a.py").write_text("a = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )
        # Set up state dir inside git repo
        sd = repo / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        from zf.core.state.session import SessionStore
        from zf.core.task.store import TaskStore
        from zf.runtime.orchestrator import Orchestrator

        EventLog(sd / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        SessionStore(sd / "session.yaml").create(project_root=str(repo))
        (sd / "kanban.json").write_text("[]\n")

        store = TaskStore(sd / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))
        orch = Orchestrator(sd, config, transport)
        orch.run_once()

        assert "T1" in orch._dispatch_heads
        assert len(orch._dispatch_heads["T1"]) == 40  # full SHA

    def test_dispatch_graceful_in_non_git_dir(self, state_dir, config, transport):
        """In a non-git dir, _dispatch_heads stays empty — no crash."""
        from zf.core.task.store import TaskStore
        from zf.runtime.orchestrator import Orchestrator

        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        assert "T1" not in orch._dispatch_heads


class TestOnTestPassedWritesEvidence:
    def test_task_done_emits_files_touched_event(self, tmp_path, config, transport):
        """In a real git repo with commits, completion emits task.files_touched."""
        repo = tmp_path / "proj"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo, capture_output=True, check=True,
        )
        (repo / "a.py").write_text("a = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )
        # Record dispatch HEAD
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        dispatch_head = result.stdout.strip()

        # Simulate dev work: add a file after dispatch
        (repo / "b.py").write_text("b = 2\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add b"],
            cwd=repo, capture_output=True, check=True,
        )

        # Set up state dir inside git repo
        sd = repo / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        from zf.core.state.session import SessionStore
        from zf.core.task.store import TaskStore
        from zf.runtime.orchestrator import Orchestrator

        EventLog(sd / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        SessionStore(sd / "session.yaml").create(project_root=str(repo))
        (sd / "kanban.json").write_text("[]\n")

        store = TaskStore(sd / "kanban.json")
        store.add(Task(
            id="T1", title="x",
            status="testing", assigned_to="dev",
        ))
        log = EventLog(sd / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed", actor="dev",
            payload={"from": "busy", "to": "awaiting_review", "reason": "prior"},
        ))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(sd, config, transport)
        orch._dispatch_heads["T1"] = dispatch_head
        orch.run_once()

        events = log.read_all()
        files_events = [
            e for e in events if e.type == "task.files_touched"
        ]
        assert len(files_events) == 1
        assert files_events[0].task_id == "T1"
        assert "b.py" in files_events[0].payload["files"]
        assert len(files_events[0].payload["commits"]) == 1

    def test_task_done_emits_uncommitted_files_touched(
        self, tmp_path, config, transport,
    ):
        """Completion evidence includes files even when dev did not commit."""
        repo = tmp_path / "proj"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo, capture_output=True, check=True,
        )
        (repo / "a.py").write_text("a = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        dispatch_head = result.stdout.strip()
        (repo / "b.py").write_text("b = 2\n")

        sd = repo / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        from zf.core.state.session import SessionStore
        from zf.core.task.store import TaskStore
        from zf.runtime.orchestrator import Orchestrator

        EventLog(sd / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        SessionStore(sd / "session.yaml").create(project_root=str(repo))
        (sd / "kanban.json").write_text("[]\n")

        store = TaskStore(sd / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))
        log = EventLog(sd / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed", actor="dev",
            payload={"from": "busy", "to": "awaiting_review", "reason": "prior"},
        ))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(sd, config, transport)
        orch._dispatch_heads["T1"] = dispatch_head
        orch.run_once()

        files_event = next(e for e in log.read_all() if e.type == "task.files_touched")
        assert "b.py" in files_event.payload["files"]
        assert files_event.payload["commits"] == []

    def test_no_files_event_when_no_dispatch_head(self, state_dir, config, transport):
        """Without a recorded dispatch HEAD, no task.files_touched event."""
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        from zf.core.task.store import TaskStore
        from zf.runtime.orchestrator import Orchestrator

        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x",
            status="testing", assigned_to="dev",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed", actor="dev",
            payload={"from": "busy", "to": "awaiting_review", "reason": "prior"},
        ))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        events = log.read_all()
        files_events = [
            e for e in events if e.type == "task.files_touched"
        ]
        assert len(files_events) == 0


# -- Reuse fixtures from test_worker_state_events --

@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.state.session import SessionStore

    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config():
    from zf.core.config.schema import (
        ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
    )
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))
