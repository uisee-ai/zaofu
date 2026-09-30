"""Offline fixture/oracle contracts; never launch a native Provider."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.conformance import test_zaofu_testkit_s5_native_codex_legacy_queue as native
from tests.conformance.test_zaofu_testkit_s3_writer_queue_provider_turn import _hook
from tests.test_writer_fanout_runtime import _child, _commit, _git, _manifest, _RecordingTransport
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.workflow.inspection import build_workflow_inspection_report, inspection_failed
from zf.runtime.backend import CodexAdapter
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_contract import build_run_contract, write_run_contract
from zf.runtime.workflow_resume import build_workflow_resume_projection


def _rows(workdir: Path, session: str, turn: str) -> list[dict]:
    return [
        {"type": "session_meta", "payload": {"id": session, "cwd": str(workdir)}},
        {"type": "turn_context", "payload": {"turn_id": turn, "cwd": str(workdir)}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                              "call_id": "tool-1", "arguments": '{"cmd":"timeout 240 wait-release"}'}},
    ]


def test_transcript_oracle_binds_turn_before_and_after_real_terminal(tmp_path):
    rows = _rows(tmp_path, "session-1", "turn-1")
    args = dict(session="session-1", turn="turn-1", workdir=str(tmp_path), required_command="wait-release")
    assert native.turn_evidence(rows, **args, closed=False)["tool_call_ids"] == ["tool-1"]
    rows.append({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1"}})
    assert native.turn_evidence(rows, **args, closed=True)["terminal_events"] == ["task_complete"]


@pytest.mark.parametrize("mutation", [
    "session", "cwd", "turn", "context-cwd", "no-tool", "other-tool-turn", "no-call-id", "early-terminal",
])
def test_open_turn_oracle_rejects_wrong_identity_or_closed_turn(tmp_path, mutation):
    rows = _rows(tmp_path, "session-1", "turn-1")
    if mutation == "session":
        rows[0]["payload"]["id"] = "other-session"
    elif mutation == "cwd":
        rows[0]["payload"]["cwd"] = str(tmp_path / "unrelated")
    elif mutation == "turn":
        rows[1]["payload"]["turn_id"] = "other-turn"
    elif mutation == "context-cwd":
        rows[1]["payload"]["cwd"] = str(tmp_path / "unrelated")
    elif mutation == "no-tool":
        rows.pop()
    elif mutation == "no-call-id":
        rows[2]["payload"]["call_id"] = ""
    elif mutation == "other-tool-turn":
        rows.insert(2, {"type": "turn_context", "payload": {"turn_id": "other", "cwd": str(tmp_path)}})
    else:
        rows.append({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1"}})
    with pytest.raises(AssertionError):
        native.turn_evidence(rows, session="session-1", turn="turn-1", workdir=str(tmp_path),
                             required_command="wait-release", closed=False)


@pytest.mark.parametrize("terminal", [None, "turn_aborted", "other-turn"])
def test_closed_turn_requires_its_own_successful_terminal(tmp_path, terminal):
    rows = _rows(tmp_path, "session-1", "turn-1")
    if terminal:
        rows.append({"type": "event_msg", "payload": {
            "type": "task_complete" if terminal == "other-turn" else terminal,
            "turn_id": "other-turn" if terminal == "other-turn" else "turn-1",
        }})
    with pytest.raises(AssertionError):
        native.turn_evidence(rows, session="session-1", turn="turn-1", workdir=str(tmp_path),
                             required_command="wait-release", closed=True)


def _queue_events():
    start = datetime(2026, 9, 29, tzinfo=timezone.utc)
    identity = {"fanout_id": "fanout-1", "child_id": "child-1", "run_id": "run-1", "role_instance": "dev-1"}
    events = [
        ZfEvent(type="fanout.started", payload={"fanout_id": "fanout-1"}),
        ZfEvent(type="fanout.child.dispatched", payload=identity),
        ZfEvent(type="codex.hook.user_prompt_submit", actor="dev-1", payload={"session_id": "session-1", "turn_id": "turn-1"}),
        ZfEvent(type="dev.build.done", actor="dev-1", origin="worker", task_id="TASK-1", payload={**identity, "source_commit": "commit-1"}),
        ZfEvent(type="fanout.cancelled", payload={
            "fanout_id": "fanout-1", "reason": "queued_wait_timeout",
            "semantic_attempt_consumed": False, "completed_task_ids": ["TASK-1"], "queued_children": ["child-2"],
        }),
    ]
    events[2].causation_id = events[1].id
    for index, event in enumerate(events):
        event.ts = (start + timedelta(seconds=index if index < 4 else 180)).isoformat()
    return events


def test_queue_oracle_requires_causal_nonsemantic_timeout():
    events = _queue_events()
    result = native.queue_timeout_evidence(events, events[1], timeout=180)
    assert result["elapsed_seconds"] == 180
    assert result["source_commit"] == "commit-1"


@pytest.mark.parametrize("mutation", ["early", "semantic", "lost-completed", "wrong-result", "wrong-hook", "early-stop", "extra-dispatch"])
def test_queue_oracle_rejects_unproven_occupancy(mutation):
    events = _queue_events()
    first = events[1]
    if mutation == "early":
        events[-1].ts = events[3].ts
    elif mutation == "semantic":
        events[-1].payload["semantic_attempt_consumed"] = True
    elif mutation == "lost-completed":
        events[-1].payload["completed_task_ids"] = []
    elif mutation == "wrong-result":
        events[3].payload["run_id"] = "other"
    elif mutation == "wrong-hook":
        events[2].causation_id = "unrelated-dispatch"
    elif mutation == "early-stop":
        events.insert(4, ZfEvent(type="codex.hook.stop", actor="dev-1", payload={"session_id": "session-1", "turn_id": "turn-1"}))
    else:
        events.append(ZfEvent(type="fanout.child.dispatched", payload=deepcopy(first.payload)))
    with pytest.raises((AssertionError, ValueError)):
        native.queue_timeout_evidence(events, first, timeout=180)


def _offline_fixture(tmp_path, monkeypatch, *, timeout=180):
    state, config, release = native._native_fixture(tmp_path, "TEST_MARKER", queue_timeout=timeout)
    loaded = load_config(tmp_path / "zf.yaml")
    report = build_workflow_inspection_report(loaded, project_root=tmp_path, state_dir=state)
    assert not inspection_failed(report), report["diagnostics"]
    assert loaded.project.state_dir == "runtime-state" and release.parent == tmp_path
    assert len(loaded.roles) == 1 and loaded.roles[0].backend == "codex"
    command = CodexAdapter().build_command(loaded.roles[0])
    assert command[command.index("-s") + 1] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert command[command.index("--add-dir") + 1] == str(tmp_path)
    write_run_contract(state, build_run_contract(loaded, config_path=tmp_path / "zf.yaml", project_root=tmp_path, state_dir=state))
    transport = _RecordingTransport()
    runtime = Orchestrator(state, loaded, transport, project_root=tmp_path)
    monkeypatch.setattr(runtime, "_ensure_role_active", lambda *a, **kw: True)
    runtime.event_writer.append(ZfEvent(type="task_map.ready", actor="planner-fixture", payload={
        "pdd_id": "F-11111111", "workflow_run_id": "TEST_MARKER",
        "task_map_ref": "runtime-state/artifacts/F-11111111/task_map.json",
    }))
    runtime.run_once()
    [first] = [e for e in runtime.event_log.read_all() if e.type == "fanout.child.dispatched"]
    assert first.payload["task_id"] == "TASK-1"
    assert "task_stage_session_binding" not in first.payload, "this must stay legacy, not blocking"
    briefing = next((state / "briefings").glob("dev-1-*.md")).read_text()
    assert "timeout 240" in briefing and release.name in briefing
    assert "dev.build.done" in briefing
    session = str(uuid4())
    RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(tmp_path)).bind_codex_session("dev-1", session)
    opened = _hook(runtime, "user_prompt_submit", backend="codex", session=session, turn="turn-1")
    assert opened.actor == "dev-1" and opened.causation_id == first.id
    return runtime, transport, first, session


def test_native_fixture_reaches_legacy_dispatch_without_provider(tmp_path, monkeypatch):
    _offline_fixture(tmp_path, monkeypatch)


def test_native_fixture_offline_timeout_and_cli_gap_recovery(tmp_path, monkeypatch, record_property):
    """Synthetic worker only; real loaded config, clock, hook, resume and kernel."""
    runtime, transport, first, session = _offline_fixture(tmp_path, monkeypatch, timeout=10)
    state = runtime.state_dir

    def complete(dispatch, name, content):
        commit = _commit(Path(dispatch.payload["workdir"]), name, content + "\n", f"test: {name}")
        runtime.event_writer.append(ZfEvent(
            type="dev.build.done", actor="dev-1", origin="worker", task_id=dispatch.payload["task_id"],
            payload={**dispatch.payload, "dispatch_id": dispatch.payload["run_id"], "source_commit": commit},
        ))
        runtime.run_once()

    complete(first, "a.txt", "TEST_MARKER-1")
    assert _child(_manifest(state, first.payload["fanout_id"]), "TASK-1")["status"] == "completed"
    preserved = _git(tmp_path, "rev-parse", "task/TASK-1")
    deadline = time.monotonic() + 20
    while not any(e.type == "fanout.cancelled" and e.payload.get("reason") == "queued_wait_timeout"
                  for e in runtime.event_log.read_all()):
        assert time.monotonic() < deadline
        time.sleep(0.2)
        runtime.run_once()
    facts = native.queue_timeout_evidence(runtime.event_log.read_all(), first, timeout=10)
    [checkpoint] = build_workflow_resume_projection(state, runtime.config)["batch_checkpoints"]
    args = [sys.executable, "-m", "zf.cli.main", "recover", "workflow", "--resume-pending", "--json",
            "--checkpoint-id", checkpoint["checkpoint_id"]]
    for extra, applied in ((["--dry-run"], 0), ([], 1), ([], 0)):
        before = (state / "events.jsonl").read_bytes()
        result = subprocess.run([*args, *extra], cwd=tmp_path, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["applied"] == applied
        if not applied:
            assert (state / "events.jsonl").read_bytes() == before
    _hook(runtime, "stop", backend="codex", session=session, turn="turn-1")
    runtime.run_once()
    [second] = [e for e in runtime.event_log.read_all() if e.type == "fanout.child.dispatched"
                and e.payload["task_id"] == "TASK-2"]
    complete(second, "b.txt", "TEST_MARKER-2")
    [candidate] = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
    head = candidate.payload["candidate_head_commit"]
    assert _git(tmp_path, "show", f"{head}:a.txt") == "TEST_MARKER-1"
    assert _git(tmp_path, "show", f"{head}:b.txt") == "TEST_MARKER-2"
    assert _git(tmp_path, "rev-parse", "task/TASK-1") == preserved
    for _ in range(2):
        runtime = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
        monkeypatch.setattr(runtime, "_ensure_role_active", lambda *a, **kw: True)
        runtime.run_once()
    events = runtime.event_log.read_all()
    assert [e.payload["task_id"] for e in events if e.type == "fanout.child.dispatched"] == ["TASK-1", "TASK-2"]
    assert sum(e.type == "candidate.ready" for e in events) == 1
    record_property("native_queue_fixture_offline", json.dumps({**facts, "real_provider": False,
        "synthetic_worker": True, "clock_replaced": False, "active_turn_replaced": False}))


def test_native_entry_skips_before_creating_fixture_without_opt_in(monkeypatch):
    monkeypatch.delenv("ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE", raising=False)
    monkeypatch.setattr(native, "_native_fixture", lambda *a: pytest.fail("fixture must not be built"))
    with pytest.raises(pytest.skip.Exception, match="ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE"):
        native.test_native_codex_legacy_queue_timeout_resumes_only_gap(lambda *a: None)


def test_native_preflight_rejects_missing_auth_before_version_state(monkeypatch):
    monkeypatch.setenv("ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE", "1")
    monkeypatch.delenv("ZF_CODEX_WORKER_SANDBOX", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(native.shutil, "which", lambda tool: f"/mock/{tool}")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="Not logged in")
    monkeypatch.setattr(native.subprocess, "run", run)
    with pytest.raises(pytest.skip.Exception, match="authenticated"):
        native._require_native_codex()
    assert calls == [["codex", "login", "status"]]


def test_native_preflight_refuses_global_update_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE", "1")
    monkeypatch.delenv("ZF_CODEX_WORKER_SANDBOX", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(native.shutil, "which", lambda tool: f"/mock/{tool}")
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="Logged in"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    version = tmp_path / ".codex/version.json"
    version.parent.mkdir()
    raw = '{"latest_version":"new","dismissed_version":"old"}\n'
    version.write_text(raw)
    with pytest.raises(pytest.skip.Exception, match="global-state mutation"):
        native._require_native_codex()
    assert version.read_text() == raw


def test_transcript_resolution_rejects_wrong_metadata_and_symlink(tmp_path):
    session = str(uuid4())
    root = tmp_path / "workdirs/dev-1/codex-home/sessions"
    root.mkdir(parents=True)
    path = root / f"rollout-test-{session}.jsonl"
    rows = _rows(tmp_path, session, "turn-1")
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert native._codex_transcript(tmp_path, "dev-1", session, str(tmp_path)) == path
    with pytest.raises(AssertionError):
        native._codex_transcript(tmp_path, "dev-1", session, str(tmp_path / "other"))
    outside = tmp_path / "outside.jsonl"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(AssertionError, match="escapes"):
        native._codex_transcript(tmp_path, "dev-1", session, str(tmp_path))
