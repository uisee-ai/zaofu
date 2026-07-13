"""Tests for hook_recv codex.hook.* routing — 1202-T2.

hook_recv must accept Codex hook payloads (which share structure with
Claude but add a few Codex-only fields) and dispatch them under the
codex.hook.* event namespace without breaking the existing claude.hook.*
/ orchestrator.round.complete paths.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zf.cli.hook_recv import run as hook_recv_run
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def _invoke(state_dir: Path, event: str, backend: str, payload: dict,
            monkeypatch) -> int:
    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": staticmethod(lambda: json.dumps(payload))})(),
    )
    args = argparse.Namespace(
        event=event,
        state_dir=str(state_dir),
        backend=backend,
    )
    return hook_recv_run(args)


def test_codex_hook_stop_routes_with_namespace(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    _invoke(
        state_dir,
        event="codex.hook.stop",
        backend="codex",
        payload={"session_id": "abc-codex", "hook_event_name": "Stop"},
        monkeypatch=monkeypatch,
    )

    log = EventLog(state_dir / "events.jsonl")
    events = log.read_all()
    assert any(e.type == "codex.hook.stop" for e in events)
    stop = next(e for e in events if e.type == "codex.hook.stop")
    assert stop.payload["provider_stop_reason"] == "completed_without_terminal_event"


def test_codex_hook_stop_classifies_hook_review_required(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    _invoke(
        state_dir,
        event="codex.hook.stop",
        backend="codex",
        payload={
            "session_id": "abc-codex",
            "hook_event_name": "Stop",
            "reason": "5 hooks need review before they can run",
        },
        monkeypatch=monkeypatch,
    )

    log = EventLog(state_dir / "events.jsonl")
    stop = next(e for e in log.read_all() if e.type == "codex.hook.stop")
    assert stop.payload["provider_stop_reason"] == "hook_review_required"


def test_codex_hook_extracts_codex_specific_fields(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex payload carries turn_id / transcript_path / permission_mode
    that Claude does not — the bridge must preserve these in event payload.
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-codex-1",
            "turn_id": "turn-42",
            "transcript_path": "/home/u/.codex/sessions/2026/04/20/uuid.jsonl",
            "permission_mode": "workspace-write",
            "stop_hook_active": False,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        },
        monkeypatch=monkeypatch,
    )

    log = EventLog(state_dir / "events.jsonl")
    events = [e for e in log.read_all() if e.type == "codex.hook.pre_tool_use"]
    assert events, "codex.hook.pre_tool_use event should be appended"
    pl = events[0].payload
    assert pl["turn_id"] == "turn-42"
    assert pl["transcript_path"].endswith("uuid.jsonl")
    assert pl["permission_mode"] == "workspace-write"
    assert pl["tool_name"] == "Bash"


def test_codex_pre_tool_use_blocks_worker_runtime_task_doc_write(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    transcript = (
        state_dir
        / "workdirs"
        / "dev-1"
        / "codex-home"
        / "sessions"
        / "2026"
        / "06"
        / "01"
        / "rollout.jsonl"
    )

    code = _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-codex-runtime-write",
            "turn_id": "turn-runtime-write",
            "transcript_path": str(transcript),
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "python3 - <<'PY'\n"
                    f"from pathlib import Path\n"
                    f"Path('{state_dir}/task_docs/TASK-1/task.md').write_text('bad')\n"
                    "PY"
                ),
            },
        },
        monkeypatch=monkeypatch,
    )

    assert code == 2
    events = EventLog(state_dir / "events.jsonl").read_all()
    rejected = [event for event in events if event.type == "worker.runtime_write.rejected"]
    assert rejected
    assert rejected[0].payload["worker"] == "dev-1"
    assert "task_docs" in rejected[0].payload["protected_targets"]


def test_codex_pre_tool_use_blocks_worker_task_doc_ingest_command(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    transcript = (
        state_dir
        / "workdirs"
        / "dev-1"
        / "codex-home"
        / "sessions"
        / "2026"
        / "06"
        / "01"
        / "rollout.jsonl"
    )

    code = _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-codex-task-doc-ingest",
            "transcript_path": str(transcript),
            "tool_name": "Bash",
            "tool_input": {"command": "zf task-doc ingest TASK-1"},
        },
        monkeypatch=monkeypatch,
    )

    assert code == 2
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(
        event.type == "worker.runtime_write.rejected"
        and event.payload["reason"] == "worker_task_doc_ingest_forbidden"
        for event in events
    )


def test_codex_hook_resolves_actor_from_role_local_transcript_path(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    session_id = "77777777-7777-7777-7777-777777777777"
    transcript = (
        state_dir
        / "workdirs"
        / "orchestrator"
        / "codex-home"
        / "sessions"
        / "2026"
        / "05"
        / "11"
        / f"rollout-2026-05-11T00-00-00-{session_id}.jsonl"
    )

    _invoke(
        state_dir,
        event="codex.hook.session_start",
        backend="codex",
        payload={
            "session_id": session_id,
            "hook_event_name": "SessionStart",
            "transcript_path": str(transcript),
        },
        monkeypatch=monkeypatch,
    )

    events = EventLog(state_dir / "events.jsonl").read_all()
    hook = next(e for e in events if e.type == "codex.hook.session_start")
    assert hook.actor == "orchestrator"
    assert not any(e.type == "hook.orphan_event" for e in events)

    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    assert registry.get_instance_by_uuid(session_id) == "orchestrator"


def test_codex_hook_transcript_path_repairs_stale_registry_binding(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    session_id = "99999999-9999-9999-9999-999999999999"
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.bind_codex_session(
        "review",
        session_id,
        session_path=state_dir / "workdirs" / "review" / "codex-home" / "sessions" / "old.jsonl",
    )
    transcript = (
        state_dir
        / "workdirs"
        / "dev-1"
        / "codex-home"
        / "sessions"
        / "2026"
        / "05"
        / "11"
        / f"rollout-2026-05-11T00-00-00-{session_id}.jsonl"
    )

    _invoke(
        state_dir,
        event="codex.hook.session_start",
        backend="codex",
        payload={
            "session_id": session_id,
            "hook_event_name": "SessionStart",
            "transcript_path": str(transcript),
        },
        monkeypatch=monkeypatch,
    )

    events = EventLog(state_dir / "events.jsonl").read_all()
    hook = next(e for e in events if e.type == "codex.hook.session_start")
    assert hook.actor == "dev-1"

    reloaded = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    assert reloaded.get_instance_by_uuid(session_id) == "dev-1"
    assert reloaded.get("review") is None


def test_claude_hook_still_works_after_codex_routing_added(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression guard: existing claude path must not regress."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    _invoke(
        state_dir,
        event="claude.hook.stop",
        backend="claude-code",
        payload={"session_id": "abc-claude", "hook_event_name": "Stop"},
        monkeypatch=monkeypatch,
    )

    log = EventLog(state_dir / "events.jsonl")
    events = log.read_all()
    assert any(e.type == "claude.hook.stop" for e in events)


def test_codex_hook_without_backend_flag_still_extracts_by_event_namespace(
    tmp_path: Path, monkeypatch
) -> None:
    """--backend is a convenience hint; the canonical signal is --event
    prefix so payload extraction works even if --backend is omitted.
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    _invoke(
        state_dir,
        event="codex.hook.post_tool_use",
        backend="",  # intentionally omitted
        payload={
            "session_id": "x",
            "turn_id": "t-1",
            "tool_response": {"ok": True},
        },
        monkeypatch=monkeypatch,
    )

    log = EventLog(state_dir / "events.jsonl")
    events = [e for e in log.read_all()
              if e.type == "codex.hook.post_tool_use"]
    assert events
    assert events[0].payload.get("turn_id") == "t-1"


def test_orchestrator_round_complete_unaffected(
    tmp_path: Path, monkeypatch
) -> None:
    """Hooks outside claude.* / codex.* namespace must not be touched
    by the new routing logic.
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    _invoke(
        state_dir,
        event="orchestrator.round.complete",
        backend="",
        payload={"session_id": "orch", "hook_event_name": "Stop"},
        monkeypatch=monkeypatch,
    )

    log = EventLog(state_dir / "events.jsonl")
    events = log.read_all()
    assert any(e.type == "orchestrator.round.complete" for e in events)


def _seed_scoped_worker_task(state_dir: Path, scope: list[str]) -> Path:
    transcript = (
        state_dir / "workdirs" / "dev-1" / "codex-home"
        / "sessions" / "2026" / "06" / "01" / "rollout.jsonl"
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1", title="core", status="in_progress", assigned_to="dev-1",
        contract=TaskContract(scope=list(scope)),
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T1",
        payload={"role": "dev-1", "assignee": "dev-1"},
    ))
    return transcript


def test_codex_apply_patch_blocks_write_outside_allowed_paths(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    transcript = _seed_scoped_worker_task(state_dir, ["app/server.js"])

    code = _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-scope-block",
            "transcript_path": str(transcript),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": (
                    "*** Begin Patch\n"
                    "*** Add File: app/src/api.js\n"
                    "+export const x = 1;\n"
                    "*** End Patch"
                ),
            },
        },
        monkeypatch=monkeypatch,
    )

    assert code == 2
    events = EventLog(state_dir / "events.jsonl").read_all()
    rejected = [e for e in events if e.type == "worker.scope_write.rejected"]
    assert rejected
    assert rejected[0].payload["worker"] == "dev-1"
    assert "app/src/api.js" in rejected[0].payload["offending_paths"]


def test_codex_apply_patch_allows_write_inside_allowed_paths(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    transcript = _seed_scoped_worker_task(
        state_dir, ["app/server.js", "app/tests/api.test.js"]
    )

    code = _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-scope-ok",
            "transcript_path": str(transcript),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": (
                    "*** Begin Patch\n"
                    "*** Add File: app/server.js\n"
                    "+require('http');\n"
                    "*** End Patch"
                ),
            },
        },
        monkeypatch=monkeypatch,
    )

    assert code != 2
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [e for e in events if e.type == "worker.scope_write.rejected"]
