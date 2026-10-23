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
from types import SimpleNamespace

from zf.cli.hook_workdir_guard import bash_command_looks_mutating
from zf.cli.hook_recv import run as hook_recv_run
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_runtime import (
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.result_submit import provision_role_submit_credential


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


def test_controlled_zf_json_payload_is_not_shell_mutation() -> None:
    payload = json.dumps({
        "evidence": (
            "`Path.write_text` and $(literal command example) are quoted facts "
            "about /repo/.zf/facts.json"
        ),
    })

    assert not bash_command_looks_mutating(
        f"uv run zf result submit --operation op-1 --json '{payload}'"
    )
    assert not bash_command_looks_mutating(
        "uv run zf result validate --operation op-1 "
        "--result-file /tmp/.zf/tmp/result-submit/op-1/result.json"
    )
    assert not bash_command_looks_mutating(
        f"zf emit worker.heartbeat --task T-1 --payload '{payload}'"
    )
    assert bash_command_looks_mutating(
        f"zf emit worker.heartbeat --task T-1 --payload '{payload}' ; rm file"
    )
    assert bash_command_looks_mutating(
        'zf emit worker.heartbeat --task T-1 --payload "$(touch /repo/bad)"'
    )
    assert bash_command_looks_mutating(
        'zf emit worker.heartbeat --task T-1 --payload "don\'t $(touch /repo/bad)"'
    )
    assert bash_command_looks_mutating(
        "zf emit worker.heartbeat --task T-1 --payload `touch /repo/bad`"
    )
    assert bash_command_looks_mutating(
        "python -c \"Path('/repo/.zf/facts.json').write_text('bad')\""
    )


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


def test_bound_idle_role_hook_is_not_orphan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    session_id = "12121212-1212-1212-1212-121212121212"
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.bind_codex_session("run-manager", session_id)

    _invoke(
        state_dir,
        event="codex.hook.stop",
        backend="codex",
        payload={"session_id": session_id, "hook_event_name": "Stop"},
        monkeypatch=monkeypatch,
    )

    events = EventLog(state_dir / "events.jsonl").read_all()
    hook = next(event for event in events if event.type == "codex.hook.stop")
    assert hook.actor == "run-manager"
    assert hook.payload["context_state"] == "bound_idle"
    assert not any(event.type == "hook.orphan_event" for event in events)


def test_unresolved_session_emits_one_deduplicated_orphan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    session_id = "34343434-3434-3434-3434-343434343434"

    for event_type in ("codex.hook.pre_tool_use", "codex.hook.post_tool_use"):
        _invoke(
            state_dir,
            event=event_type,
            backend="codex",
            payload={"session_id": session_id, "hook_event_name": "ToolUse"},
            monkeypatch=monkeypatch,
        )

    events = EventLog(state_dir / "events.jsonl").read_all()
    hooks = [event for event in events if event.type.startswith("codex.hook.")]
    orphans = [event for event in events if event.type == "hook.orphan_event"]
    assert len(hooks) == 2
    assert all(event.payload["context_state"] == "unresolved" for event in hooks)
    assert len(orphans) == 1
    assert orphans[0].payload["session_id"] == session_id


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
    assert rejected[0].payload["origin_event"] == "codex.hook.pre_tool_use"
    assert len(rejected[0].payload["tool_input_digest"]) == 64
    assert "*** Begin Patch" not in str(rejected[0].payload)


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


def test_codex_allowed_paths_guard_allows_only_current_result_scratch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    transcript = _seed_scoped_worker_task(state_dir, ["app/server.js"])
    event_log = EventLog(state_dir / "events.jsonl")
    runtime = SimpleNamespace(
        project_root=tmp_path,
        state_dir=state_dir,
        event_log=event_log,
        event_writer=EventWriter(event_log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}}
            )
        ),
    )
    provision_role_submit_credential(state_dir, "dev-1")
    prepared = prepare_call_operation(
        runtime,
        payload={
            "workflow_run_id": "run-scratch",
            "role_instance": "dev-1",
            "fanout_id": "fanout-1",
            "stage_id": "impl",
            "child_id": "child-1",
            "run_id": "attempt-scratch",
            "task_id": "T-RESULT",
            "canonical_success_event": "dev.build.done",
            "canonical_failure_event": "dev.blocked",
        },
        operation_type="fanout_writer_child",
        operation_key="child-1",
        stage_id="impl",
        task_id="T-RESULT",
        dispatch_id="attempt-scratch",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="T-RESULT",
        dispatch_id="attempt-scratch",
    )
    scratch = state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("{}\n", encoding="utf-8")
    sibling = scratch.with_name("other.json")
    sibling.write_text("{}\n", encoding="utf-8")

    def invoke_patch(target: Path, session_id: str) -> int:
        return _invoke(
            state_dir,
            event="codex.hook.pre_tool_use",
            backend="codex",
            payload={
                "session_id": session_id,
                "transcript_path": str(transcript),
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": (
                        "*** Begin Patch\n"
                        f"*** Update File: {target}\n"
                        "@@\n"
                        "-{}\n"
                        "+{\"verdict\": \"passed\"}\n"
                        "*** End Patch"
                    ),
                },
            },
            monkeypatch=monkeypatch,
        )

    assert invoke_patch(scratch, "uuid-result-scratch-ok") == 0

    delete_then_add = _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-result-scratch-delete-add-denied",
            "transcript_path": str(transcript),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": (
                    "*** Begin Patch\n"
                    f"*** Delete File: {scratch}\n"
                    f"*** Add File: {scratch}\n"
                    "+{\"verdict\": \"passed\"}\n"
                    "*** End Patch"
                ),
            },
        },
        monkeypatch=monkeypatch,
    )
    assert delete_then_add == 2
    assert invoke_patch(sibling, "uuid-result-scratch-denied") == 2
    rejected = [
        event
        for event in event_log.read_all()
        if event.type == "worker.scope_write.rejected"
    ]
    assert len(rejected) == 2
    assert rejected[0].payload["offending_paths"] == [str(scratch)]
    assert rejected[1].payload["offending_paths"] == [str(sibling)]


def test_codex_plan_operation_scope_extends_parent_task_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    transcript = _seed_scoped_worker_task(
        state_dir,
        ["src/expense_lite/store.py", "tests/**"],
    )
    workdir = state_dir / "workdirs" / "dev-1" / "project"
    workdir.mkdir(parents=True)
    event_log = EventLog(state_dir / "events.jsonl")
    runtime = SimpleNamespace(
        project_root=tmp_path,
        state_dir=state_dir,
        event_log=event_log,
        event_writer=EventWriter(event_log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}}
            )
        ),
    )
    prepared = prepare_call_operation(
        runtime,
        payload={
            "workflow_run_id": "run-issue",
            "role_instance": "dev-1",
            "fanout_id": "fanout-issue-triage",
            "stage_id": "issue-triage",
            "child_id": "issue-triage",
            "run_id": "attempt-issue-triage",
            "task_id": "T1",
            "canonical_success_event": "issue.triage.child.completed",
            "canonical_failure_event": "issue.triage.child.failed",
        },
        operation_type="fanout_reader_child",
        operation_key="issue-triage",
        stage_id="issue-triage",
        task_id="T1",
        dispatch_id="attempt-issue-triage",
        workdir_write_scopes=["docs/plans/**", "artifacts/plan/**"],
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="T1",
        dispatch_id="attempt-issue-triage",
    )

    def invoke_add(target: Path, session_id: str) -> int:
        return _invoke(
            state_dir,
            event="codex.hook.pre_tool_use",
            backend="codex",
            payload={
                "session_id": session_id,
                "transcript_path": str(transcript),
                "cwd": str(workdir),
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": (
                        "*** Begin Patch\n"
                        f"*** Add File: {target}\n"
                        "+{}\n"
                        "*** End Patch"
                    ),
                },
            },
            monkeypatch=monkeypatch,
        )

    task_map = workdir / "artifacts" / "plan" / "task_map.json"
    unrelated = workdir / "artifacts" / "unrelated" / "escape.json"
    assert invoke_add(task_map, "uuid-plan-artifact-ok") == 0
    assert invoke_add(unrelated, "uuid-plan-artifact-denied") == 2

    rejected = [
        event
        for event in event_log.read_all()
        if event.type == "worker.scope_write.rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].payload["offending_paths"] == [str(unrelated)]
    assert rejected[0].payload["allowed_paths"] == [
        "src/expense_lite/store.py",
        "tests/**",
        "docs/plans/**",
        "artifacts/plan/**",
    ]


def test_codex_refactor_planner_allows_workdir_artifact_and_result_scratch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    role = "refactor-plan-synth"
    workdir = state_dir / "workdirs" / role / "project"
    workdir.mkdir(parents=True)
    transcript = (
        state_dir / "workdirs" / role / "codex-home"
        / "sessions" / "2026" / "07" / "29" / "rollout.jsonl"
    )
    event_log = EventLog(state_dir / "events.jsonl")
    runtime = SimpleNamespace(
        project_root=tmp_path,
        state_dir=state_dir,
        event_log=event_log,
        event_writer=EventWriter(event_log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}}
            )
        ),
    )
    provision_role_submit_credential(state_dir, role)
    prepared = prepare_call_operation(
        runtime,
        payload={
            "workflow_run_id": "run-refactor",
            "role_instance": role,
            "fanout_id": "fanout-refactor-plan",
            "stage_id": "flow-plan",
            "child_id": role,
            "run_id": "attempt-refactor-plan",
            "canonical_success_event": "refactor.plan.child.completed",
            "canonical_failure_event": "refactor.plan.child.failed",
        },
        operation_type="fanout_reader_child",
        operation_key=role,
        stage_id="flow-plan",
        task_id="",
        dispatch_id="attempt-refactor-plan",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="",
        dispatch_id="attempt-refactor-plan",
    )
    scratch = state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("{}\n", encoding="utf-8")

    def invoke_patch(
        target: Path,
        session_id: str,
        *,
        operation: str = "Add",
    ) -> int:
        body = (
            "+{}\n"
            if operation == "Add"
            else "@@\n-{}\n+{\"status\": \"completed\"}\n"
        )
        return _invoke(
            state_dir,
            event="codex.hook.pre_tool_use",
            backend="codex",
            payload={
                "session_id": session_id,
                "transcript_path": str(transcript),
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": (
                        "*** Begin Patch\n"
                        f"*** {operation} File: {target}\n"
                        f"{body}"
                        "*** End Patch"
                    ),
                },
            },
            monkeypatch=monkeypatch,
        )

    artifact = workdir / "artifacts" / "fanout-refactor-plan" / "task_map.json"
    assert invoke_patch(artifact, "uuid-refactor-plan-artifact") == 0
    assert invoke_patch(
        scratch,
        "uuid-refactor-plan-result",
        operation="Update",
    ) == 0

    # A plan producer may write workdir-relative artifacts and then update the
    # exact signed result scratch in one provider shell turn. The legacy root
    # heuristic must allow that compound operation while the scope guard still
    # protects all provider-native path writes.
    compound = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "Path('artifacts/plan/task_map.json').write_text('{}')\n"
        f"Path('{scratch}').write_text('{{}}')\n"
        "PY"
    )
    assert _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-refactor-plan-compound",
            "transcript_path": str(transcript),
            "cwd": str(workdir),
            "tool_name": "Bash",
            "tool_input": {"command": compound},
        },
        monkeypatch=monkeypatch,
    ) == 0
    assert not [
        event
        for event in event_log.read_all()
        if event.type == "worker.scope_write.rejected"
    ]

    # An authorized mention must not hide another root reference or let the
    # path extractor authorize only a prefix of a different target.
    for index, expression in enumerate([
        f"Path('{tmp_path}').joinpath('outside.txt')",
        f"Path(f'{scratch}{{suffix}}')",
        f"Path('{scratch}其他')",
    ]):
        ambiguous = (
            compound.removesuffix("PY")
            + f"{expression}.write_text('bad')\nPY"
        )
        assert _invoke(
            state_dir,
            event="codex.hook.pre_tool_use",
            backend="codex",
            payload={
                "session_id": f"uuid-refactor-plan-ambiguous-root-{index}",
                "transcript_path": str(transcript),
                "cwd": str(workdir),
                "tool_name": "Bash",
                "tool_input": {"command": ambiguous},
            },
            monkeypatch=monkeypatch,
        ) == 2


def _seed_claude_fanout_workdir(state_dir: Path) -> Path:
    workdir = state_dir / "workdirs" / "dev-1" / "project"
    workdir.mkdir(parents=True)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T-FANOUT",
        title="fanout writer",
        status="in_progress",
        assigned_to="dev-1",
        contract=TaskContract(scope=["**/*"]),
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "child-1",
            "run_id": "run-1",
            "task_id": "T-FANOUT",
            "role_instance": "dev-1",
            "workdir": str(workdir),
        },
    ))
    return workdir


def test_codex_controlled_emit_allows_quoted_markdown_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = _seed_claude_fanout_workdir(state_dir)
    payload = json.dumps({
        "report": "Use `Path.write_text`; $(example) is literal documentation.",
    })

    code = _invoke(
        state_dir,
        event="codex.hook.pre_tool_use",
        backend="codex",
        payload={
            "session_id": "uuid-controlled-emit",
            "cwd": str(workdir),
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "zf emit workflow.child.completed --actor dev-1 "
                    f"--state-dir {state_dir} --payload '{payload}'"
                ),
            },
        },
        monkeypatch=monkeypatch,
    )

    assert code == 0
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [
        event
        for event in events
        if event.type == "worker.scope_write.rejected"
    ]


def test_claude_cwd_resolves_fanout_actor_and_blocks_canonical_root_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = _seed_claude_fanout_workdir(state_dir)
    outside = tmp_path / "app" / "escaped.js"

    code = _invoke(
        state_dir,
        event="claude.hook.pre_tool_use",
        backend="claude-code",
        payload={
            "session_id": "",
            "cwd": str(workdir),
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(outside), "content": "bad"},
        },
        monkeypatch=monkeypatch,
    )

    assert code == 2
    events = EventLog(state_dir / "events.jsonl").read_all()
    hook = next(e for e in events if e.type == "claude.hook.pre_tool_use")
    assert hook.actor == "dev-1"
    assert hook.causation_id
    rejected = [e for e in events if e.type == "worker.scope_write.rejected"]
    assert rejected[-1].payload["reason"] == "outside_assigned_workdir"
    assert rejected[-1].payload["origin_event"] == "claude.hook.pre_tool_use"
    assert len(rejected[-1].payload["tool_input_digest"]) == 64


def test_claude_workdir_write_is_allowed(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = _seed_claude_fanout_workdir(state_dir)

    code = _invoke(
        state_dir,
        event="claude.hook.pre_tool_use",
        backend="claude-code",
        payload={
            "session_id": "",
            "cwd": str(workdir),
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(workdir / "app" / "inside.js"),
                "content": "ok",
            },
        },
        monkeypatch=monkeypatch,
    )

    assert code == 0


def test_claude_workdir_guard_allows_only_current_result_scratch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = _seed_claude_fanout_workdir(state_dir)
    event_log = EventLog(state_dir / "events.jsonl")
    runtime = SimpleNamespace(
        project_root=tmp_path,
        state_dir=state_dir,
        event_log=event_log,
        event_writer=EventWriter(event_log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}}
            )
        ),
    )
    provision_role_submit_credential(state_dir, "dev-1")
    prepared = prepare_call_operation(
        runtime,
        payload={
            "workflow_run_id": "run-scratch",
            "role_instance": "dev-1",
            "fanout_id": "fanout-1",
            "stage_id": "impl",
            "child_id": "child-1",
            "run_id": "attempt-scratch",
            "task_id": "T-RESULT",
            "canonical_success_event": "dev.build.done",
            "canonical_failure_event": "dev.blocked",
        },
        operation_type="fanout_writer_child",
        operation_key="child-1",
        stage_id="impl",
        task_id="T-RESULT",
        dispatch_id="attempt-scratch",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="T-RESULT",
        dispatch_id="attempt-scratch",
    )
    scratch = state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("{}\n", encoding="utf-8")

    allowed = _invoke(
        state_dir,
        event="claude.hook.pre_tool_use",
        backend="claude-code",
        payload={
            "session_id": "",
            "cwd": str(workdir),
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(scratch), "content": "{}"},
        },
        monkeypatch=monkeypatch,
    )
    denied = _invoke(
        state_dir,
        event="claude.hook.pre_tool_use",
        backend="claude-code",
        payload={
            "session_id": "",
            "cwd": str(workdir),
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(scratch.with_name("other.json")),
                "content": "{}",
            },
        },
        monkeypatch=monkeypatch,
    )

    assert allowed == 0
    assert denied == 2


def test_claude_workdir_guard_allows_read_only_root_command_redirection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = _seed_claude_fanout_workdir(state_dir)

    code = _invoke(
        state_dir,
        event="claude.hook.pre_tool_use",
        backend="claude-code",
        payload={
            "session_id": "",
            "cwd": str(workdir),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    f"cd {tmp_path} && find . -name task_map.json 2>/dev/null; "
                    "echo done 2>&1 | head -20"
                ),
            },
        },
        monkeypatch=monkeypatch,
    )

    assert code == 0
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [event for event in events if event.type == "worker.scope_write.rejected"]


def test_claude_workdir_guard_still_blocks_root_output_redirection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = _seed_claude_fanout_workdir(state_dir)
    canonical_root_target = Path.cwd() / "escaped.txt"

    code = _invoke(
        state_dir,
        event="claude.hook.pre_tool_use",
        backend="claude-code",
        payload={
            "session_id": "",
            "cwd": str(workdir),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": f"printf bad > {canonical_root_target}",
            },
        },
        monkeypatch=monkeypatch,
    )

    assert code == 2
    events = EventLog(state_dir / "events.jsonl").read_all()
    rejected = [event for event in events if event.type == "worker.scope_write.rejected"]
    assert rejected[-1].payload["command_class"] == "mutating_shell"


def test_claude_workdir_guard_ignores_arrow_in_emit_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = _seed_claude_fanout_workdir(state_dir)

    command = (
        f"zf emit prd.plan.child.completed --state-dir {state_dir} "
        "--payload '{\"summary\":\"scaffold -> headless core\"}'"
    )
    code = _invoke(
        state_dir,
        event="claude.hook.pre_tool_use",
        backend="claude-code",
        payload={
            "session_id": "",
            "cwd": str(workdir),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
        monkeypatch=monkeypatch,
    )

    assert code == 0
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [event for event in events if event.type == "worker.scope_write.rejected"]
