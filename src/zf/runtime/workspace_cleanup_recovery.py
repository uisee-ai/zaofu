"""Durable, explicitly requested cleanup; replay never expands its resource scope."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.state.locks import locked_path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=30)


def _entries(root: Path) -> dict[str, str]:
    result = _git(root, "worktree", "list", "--porcelain")
    if result.returncode:
        raise OSError("git worktree inventory unavailable")
    entries, path = {}, ""
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = str(Path(line[9:]).resolve())
            entries[path] = ""
        elif line.startswith("branch refs/heads/") and path:
            entries[path] = line[18:]
    return entries


def _file_identity(path: Path) -> dict:
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino, "ctime_ns": info.st_ctime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _workspace_identity(path: Path) -> dict:
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino, "gitfile": _file_identity(path / ".git")}


def _branch_identity(root: Path, branch: str) -> dict:
    if not branch:
        return {}
    revision = _git(root, "rev-parse", "--verify", "refs/heads/" + branch)
    if revision.returncode:
        return {}
    result = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "refs/heads/" + branch)
    if result.returncode:
        raise OSError("branch storage identity unavailable")
    path = Path(result.stdout.strip())
    if not path.exists():
        result = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "packed-refs")
        if result.returncode:
            raise OSError("packed branch identity unavailable")
        path = Path(result.stdout.strip())
    return {"head": revision.stdout.strip(), "storage": str(path), "identity": _file_identity(path)}


def _append(writer: EventWriter, kind: str, request: ZfEvent, **payload) -> None:
    writer.append(ZfEvent(type="workspace.cleanup." + kind, origin="kernel", actor="zf-cli",
        causation_id=request.id, payload={"request_id": request.payload["request_id"],
            "path": request.payload.get("path", ""), **payload}))


def _runtime_blocker(state: Path, events: list[ZfEvent]) -> str:
    from zf.core.state.task_attempts import TaskAttemptStore
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_operation import reduce_workflow_operations, TERMINAL_OPERATION_STATUSES

    if any(e.type == "event.malformed" and e.payload.get("ledger_integrity") == "unknown" for e in events):
        return "ledger integrity unknown; preserve original ledger and workspace"
    lifecycle = [e.type for e in events if e.type in {"loop.started", "loop.stopped"}]
    if lifecycle and lifecycle[-1] == "loop.started":
        return "controller has no stop receipt"
    if (state / "kanban.json").exists():
        raw = json.loads((state / "kanban.json").read_text())
        if not isinstance(raw, list) or any(not isinstance(row, dict) or not row.get("id") or not row.get("status") for row in raw):
            return "canonical task inventory identity unknown"
    if any(t.status not in {"done", "cancelled"} for t in TaskStore(state / "kanban.json").list_all()):
        return "canonical task not delivered or cancelled"
    if any(a.get("status") in {"prepared", "delivering", "sent"}
           for a in TaskAttemptStore(state / "task_attempts.json").current_rows()):
        return "canonical attempt still active"
    if any(row.get("status") not in TERMINAL_OPERATION_STATUSES for row in reduce_workflow_operations(events).values()):
        return "canonical operation still active"
    return ""


def request_cleanup(project_root: Path, state_dir: Path) -> tuple[int, list[str]]:
    """The stop --clean-workdirs entrypoint approves only this inventory snapshot."""
    root, state = project_root.resolve(), state_dir.resolve()
    entries = {path: branch for path, branch in _entries(root).items()
               if Path(path).is_relative_to(state) and Path(path) != root}
    if not entries and not (state / "events.jsonl").exists():
        return 0, []
    log = event_log_from_project(state, warn=False)
    writer = EventWriter(log)
    with locked_path(state / "locks" / "workspace-cleanup"):
        events = log.read_all()
        completed = {e.payload.get("request_id") for e in events
                     if e.type == "workspace.cleanup.completed" and e.origin == "kernel"}
        requests = [e for e in events if e.type == "workspace.cleanup.requested" and e.origin == "kernel"
                    and e.payload.get("request_id") not in completed]
        for path, branch in entries.items():
            identity = _workspace_identity(Path(path))
            if any(e.payload.get("path") == path and e.payload.get("workspace_identity") == identity for e in requests):
                continue
            writer.append(ZfEvent(type="workspace.cleanup.requested", origin="kernel", actor="zf-cli",
                payload={"request_id": uuid4().hex, "project_root": str(root), "state_dir": str(state),
                    "path": path, "branch": branch, "workspace_identity": identity,
                    "branch_identity": _branch_identity(root, branch)}))
        return _resume(root, state, log, writer)


def resume_cleanup(project_root: Path, state_dir: Path) -> tuple[int, list[str]]:
    """Replay prior requests only. A restart never authorizes newly found workdirs."""
    root, state = project_root.resolve(), state_dir.resolve()
    if not (state / "events.jsonl").exists():
        return 0, []
    log = event_log_from_project(state, warn=False)
    with locked_path(state / "locks" / "workspace-cleanup"):
        return _resume(root, state, log, EventWriter(log))


def _resume(root: Path, state: Path, log, writer: EventWriter) -> tuple[int, list[str]]:
    events = log.read_all()
    completed = {e.payload.get("request_id") for e in events
                 if e.type == "workspace.cleanup.completed" and e.origin == "kernel"}
    latest = {e.payload.get("request_id"): e for e in events
              if e.type == "workspace.cleanup.blocked" and e.origin == "kernel"}
    removed, deleted = 0, []
    for request in events:
        if request.type != "workspace.cleanup.requested" or request.origin != "kernel":
            continue
        p = request.payload
        if not p.get("request_id") or p["request_id"] in completed:
            continue
        try:
            path = Path(p["path"])
            if (p.get("project_root") != str(root) or p.get("state_dir") != str(state)
                    or not path.is_absolute() or path.resolve() != path
                    or not path.is_relative_to(state) or path == root):
                raise ValueError("cleanup scope identity changed")
            blocker = _runtime_blocker(state, log.read_all())
            if blocker:
                raise ValueError(blocker)
            entries = _entries(root)
            if path.exists():
                if _workspace_identity(path) != p["workspace_identity"] or entries.get(str(path)) != p.get("branch"):
                    raise ValueError("workspace identity changed; retain replacement")
                if _branch_identity(root, p.get("branch", "")) != p.get("branch_identity", {}):
                    raise ValueError("branch identity changed; retain candidate")
                from zf.runtime.workspace_cleanup import remove_stopped_worktree
                if not remove_stopped_worktree(root, path):
                    raise ValueError("worktree retained by occupancy, candidate or archive checks; see cleanup diagnostic")
                removed += 1
                _append(writer, "worktree_removed", request)
            elif str(path) in entries:
                raise ValueError("missing workspace has unresolved Git registration")
            branch = p.get("branch", "")
            actual = _branch_identity(root, branch)
            if branch and actual:
                if actual != p.get("branch_identity"):
                    raise ValueError("branch identity changed after worktree removal")
                result = _git(root, "branch", "-d", branch)
                if result.returncode:
                    raise ValueError("branch deletion refused: " + result.stderr.strip())
                deleted.append(branch)
            _append(writer, "completed", request, branch=branch)
            completed.add(p["request_id"])
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
            reason = str(exc)
            previous = latest.get(p["request_id"])
            if previous is None or previous.payload.get("reason") != reason:
                _append(writer, "blocked", request, reason=reason)
            print(f"Warning: retaining cleanup request for {p.get('path', '')}: {reason}", file=sys.stderr)
    return removed, deleted
