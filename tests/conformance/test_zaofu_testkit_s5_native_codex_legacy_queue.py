"""Opt-in native Codex legacy-writer queue timeout and gap recovery.

The first writer turn emits its real completion event and then remains inside
the same Codex turn while a bounded release sentinel is absent.  This keeps
the Provider slot occupied without replacing ``_active_provider_turn`` or the
clock.  The test is host/real-provider only and is never a deterministic
MAINLINE green substitute.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s5_native_cli_lifecycle import NativeCLI
from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import _assert_gone
from tests.test_writer_fanout_runtime import (
    _child,
    _config,
    _git,
    _init_repo,
    _manifest,
    _seed_tasks,
)
from tests.testkit.native_pipeline_recovery import transcript_rows
from tests.testkit.runner import _terminate
from tests.testkit.source_observation import capture_source
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import RoleLifecycleConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.backend import CodexAdapter
from zf.runtime.provider_turn_liveness import active_codex_turn
from zf.runtime.workflow_resume import build_workflow_resume_projection

QUEUE_TIMEOUT = 180
HOLD_TIMEOUT = 240


def _require_native_codex() -> None:
    if os.environ.get("ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE", "").lower() not in {
        "1", "true", "yes",
    }:
        pytest.skip("set ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE=1 for native Codex queue")
    if shutil.which("codex") is None or shutil.which("tmux") is None:
        pytest.skip("native Codex queue requires codex and tmux")
    if os.environ.get("ZF_CODEX_WORKER_SANDBOX"):
        pytest.fail("native Codex queue refuses inherited sandbox overrides")
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home and Path(codex_home).resolve() != (Path.home() / ".codex").resolve():
        pytest.skip("login probe and production credential source must use the same Codex home")
    login = subprocess.run(
        ["codex", "login", "status"], capture_output=True, text=True, timeout=15,
    )
    if login.returncode != 0 or "not logged in" in login.stdout.lower() + login.stderr.lower():
        pytest.skip("native Codex queue requires an authenticated Codex CLI")
    # SpawnCoordinator has a compatibility dismissal for this global file.  Do
    # not let this opt-in test mutate operator state when the precondition is
    # not already safe; a role-local CODEX_HOME is created by production code.
    version = Path.home() / ".codex" / "version.json"
    try:
        body = json.loads(version.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        body = {}
    if body.get("latest_version") and body.get("latest_version") != body.get("dismissed_version"):
        pytest.skip("Codex update prompt is not dismissed; refusing global-state mutation")


def _native_fixture(project: Path, marker: str, *, queue_timeout: int = QUEUE_TIMEOUT):
    _init_repo(project)
    state = project / "runtime-state"
    state.mkdir()
    task_map = state / "artifacts" / "F-11111111" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    release = project / f"{marker}.release"
    hold = shlex.join([
        "timeout", str(HOLD_TIMEOUT), "sh", "-c",
        f"while ! test -f {shlex.quote(str(release))}; do sleep 0.2; done",
    ])
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1", "scope": "a.txt", "affinity_tag": "core",
                "allowed_paths": ["a.txt"],
                "acceptance": [f"a.txt contains exactly {marker}-1\\n"],
                "verification": "git cat-file -e HEAD:a.txt",
                "payload": {"instruction": (
                    f"Create a.txt with exactly {marker}-1 followed by a newline. "
                    "Commit it on the assigned branch. Run the exact completion "
                    "command printed in this briefing. After the command succeeds, "
                    f"immediately execute `{hold}` in this SAME turn. "
                    "If the tool yields a session, poll that session until it exits; "
                    "do not finalize the turn early. Do not create the release file. "
                    "This bounded test wait reads a driver signal only; it must not "
                    "emit hooks, cancel events or other runtime facts. If timeout "
                    "exits nonzero, report that failure without retrying."
                )},
            },
            {
                "task_id": "TASK-2", "scope": "b.txt", "affinity_tag": "second",
                "allowed_paths": ["b.txt"],
                "acceptance": [f"b.txt contains exactly {marker}-2\\n"],
                "verification": "git cat-file -e HEAD:b.txt",
                "payload": {"instruction": (
                    f"Create b.txt with exactly {marker}-2 followed by a newline. "
                    "Commit it on the assigned branch, then run the exact completion "
                    "command printed in this briefing. Do not touch a.txt."
                )},
            },
        ],
    }, ensure_ascii=False), encoding="utf-8")
    config = _config(
        state,
        trigger="task_map.ready",
        task_map="runtime-state/artifacts/${pdd_id}/task_map.json",
        affinity_stage_slots=True,
        affinity_lane_count=1,
    )
    config.project.state_dir = "runtime-state"
    config.session.tmux_session = f"zf-k594-{uuid4().hex}"
    config.workflow.stages[0].timeout_seconds = queue_timeout
    config.workflow.rework_routing = {"dev.blocked": "dev"}
    config.roles = [role for role in config.roles if role.instance_id == "dev-1"]
    role = config.roles[0]
    role.backend = "codex"
    role.transport = "tmux"
    role.permission_mode = "restricted"
    role.lifecycle = RoleLifecycleConfig(mode="eager")
    role.constraints.allowed_paths = [str(project)]
    role.spawn_ready_timeout_seconds = 45
    role.guardrails = [
        f"Only write the assigned paths under {project}; never edit the ZaoFu checkout.",
        "Do not delegate, use remote services, push, or modify canonical runtime files.",
        "Use the exact zf completion command printed in the briefing.",
        "Do not inspect credentials or print environment/config secrets. Stop on permission denial.",
    ]
    config.workflow.dag.external_triggers = ["task_map.ready"]
    (project / "zf.yaml").write_text(
        yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
    )
    _seed_tasks(
        state,
        task_map_ref="runtime-state/artifacts/F-11111111/task_map.json",
        task_ids=("TASK-1", "TASK-2"),
    )
    return state, config, release


def _codex_transcript(state: Path, instance: str, session_id: str, workdir: str) -> Path:
    assert session_id and str(UUID(session_id)) == session_id
    root = state / "workdirs" / instance / "codex-home" / "sessions"
    assert root.resolve().is_relative_to(state.resolve()), "session root escapes private runtime state"
    matches = sorted(root.rglob(f"rollout-*-{session_id}.jsonl"))
    assert matches, f"missing role-local Codex transcript for {session_id}"
    candidates = []
    for path in matches:
        assert path.resolve().is_relative_to(root.resolve()), "transcript escapes role-local home"
        rows = transcript_rows(path)
        meta = next((row for row in rows if row.get("type") == "session_meta"), {})
        cwd = str((meta.get("payload") or {}).get("cwd") or "")
        if ((meta.get("payload") or {}).get("id") == session_id
                and cwd == str(Path(workdir).resolve())):
            candidates.append(path)
    assert len(candidates) == 1, [(str(p), len(transcript_rows(p))) for p in matches]
    return candidates[0]


def turn_evidence(rows: list[dict], *, session: str, turn: str, workdir: str,
                  required_command: str, closed: bool) -> dict:
    """Bind tools to an actual transcript turn, not another session's tail."""
    [meta] = [row["payload"] for row in rows if row.get("type") == "session_meta"]
    assert meta["id"] == session and Path(meta["cwd"]).resolve() == Path(workdir).resolve()
    assert turn
    active = None
    contexts, calls, terminals = [], [], []
    for row in rows:
        body = row.get("payload") or {}
        if row.get("type") == "turn_context":
            active = body.get("turn_id")
            if active == turn:
                assert Path(body["cwd"]).resolve() == Path(workdir).resolve()
                contexts.append(body)
        if row.get("type") == "response_item" and active == turn:
            if body.get("type") == "function_call" and required_command in str(body.get("arguments", "")):
                assert body.get("call_id") and body.get("name")
                calls.append(body["call_id"])
        if row.get("type") == "event_msg" and body.get("turn_id") == turn:
            if body.get("type") in {"task_complete", "turn_complete", "turn_aborted"}:
                terminals.append(body["type"])
    assert contexts and calls, "missing matching turn context or native tool call"
    if closed:
        assert terminals and "turn_aborted" not in terminals, "turn did not complete normally"
    else:
        assert not terminals, "provider turn closed before queued timeout"
    return {"session_id": session, "turn_id": turn, "tool_call_ids": calls,
            "terminal_events": terminals}


def queue_timeout_evidence(events: list[ZfEvent], first: ZfEvent, *, timeout: int) -> dict:
    """Require real ledger ordering and a same-turn occupancy at cancellation."""
    [started] = [e for e in events if e.type == "fanout.started"
                 and e.payload.get("fanout_id") == first.payload["fanout_id"]]
    [opened] = [e for e in events if e.type == "codex.hook.user_prompt_submit"
                and e.actor == first.payload["role_instance"] and e.causation_id == first.id]
    assert opened.payload.get("session_id") and opened.payload.get("turn_id")
    [done] = [e for e in events if e.type == "dev.build.done" and e.task_id == "TASK-1"]
    assert done.actor == first.payload["role_instance"]
    for key in ("fanout_id", "child_id", "run_id"):
        assert done.payload[key] == first.payload[key]
    [cancelled] = [e for e in events if e.type == "fanout.cancelled"
                  and e.payload.get("fanout_id") == first.payload["fanout_id"]]
    assert cancelled.payload["reason"] == "queued_wait_timeout"
    assert cancelled.payload["semantic_attempt_consumed"] is False
    assert cancelled.payload["completed_task_ids"] == ["TASK-1"]
    assert len(cancelled.payload["queued_children"]) == 1
    elapsed = (datetime.fromisoformat(cancelled.ts) - datetime.fromisoformat(started.ts)).total_seconds()
    assert elapsed >= timeout
    positions = {e.id: index for index, e in enumerate(events)}
    assert positions[started.id] < positions[first.id] < positions[opened.id] < positions[done.id] < positions[cancelled.id]
    assert [e.id for e in events if e.type == "fanout.child.dispatched"] == [first.id]
    for event in events[positions[opened.id] + 1:positions[cancelled.id] + 1]:
        if event.actor == opened.actor and event.type in {"codex.hook.stop", "provider.turn.closed"}:
            assert event.payload.get("turn_id") != opened.payload["turn_id"], "slot closed before timeout"
    return {"session": opened.payload["session_id"], "turn": opened.payload["turn_id"],
            "opened_id": opened.id, "done_id": done.id, "cancelled_id": cancelled.id,
            "elapsed_seconds": elapsed, "source_commit": done.payload["source_commit"]}


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_codex_legacy_queue_timeout_resumes_only_gap(record_property):
    _require_native_codex()
    repo = Path(__file__).resolve().parents[2]
    source = capture_source(repo)
    marker = f"ZF_K594_{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix="zf-k594-native-legacy-queue-") as directory:
        project = Path(directory)
        state, config, release = _native_fixture(project, marker)
        cli = NativeCLI(SimpleNamespace(config=config, state_dir=state, project_root=project), project)
        observed: dict[str, object] = {"marker": marker, "release": str(release), "complete": False}
        transcripts = []
        try:
            command = CodexAdapter().build_command(config.roles[0])
            argv_probe = subprocess.run([*command, "--help"], capture_output=True, text=True, timeout=15)
            observed["argv_probe"] = {"args": command, "exit_code": argv_probe.returncode,
                                      "stderr": argv_probe.stderr[-1000:]}
            assert argv_probe.returncode == 0, (
                "restricted Codex argv rejected (ZF-TEST-048); do not bypass the sandbox",
                observed["argv_probe"],
            )
            deadline = time.monotonic() + 900
            cli.start()
            cli.wait(lambda events: any(e.type == "loop.started" for e in events), deadline)
            cli.command(
                "emit", "task_map.ready", "--actor", "planner-fixture",
                "--payload", json.dumps({"pdd_id": "F-11111111", "workflow_run_id": marker,
                                         "task_map_ref": "runtime-state/artifacts/F-11111111/task_map.json"}),
            )

            def queued(events):
                assert not any(e.type in {"fanout.timed_out", "human.escalate"} for e in events)
                assert not any(e.type == "fanout.cancelled" and e.payload.get("reason") != "queued_wait_timeout"
                               for e in events), "fanout failed before the intended queue boundary"
                dispatches = [e for e in events if e.type == "fanout.child.dispatched"]
                assert len(dispatches) <= 1, "second task dispatched before a queue timeout"
                if len(dispatches) != 1:
                    return None
                done = [e for e in events if e.type == "dev.build.done" and e.task_id == "TASK-1"]
                if not done:
                    return None
                if _child(_manifest(state, dispatches[0].payload["fanout_id"]), "TASK-1")["status"] != "completed":
                    return None
                return dispatches[0]

            first = cli.wait(queued, deadline)
            fanout_id = first.payload["fanout_id"]
            cli.wait(
                lambda events: next((e for e in events if e.type == "fanout.cancelled"
                                     and e.payload.get("fanout_id") == fanout_id), None),
                min(deadline, time.monotonic() + QUEUE_TIMEOUT + 30),
            )
            facts = queue_timeout_evidence(cli.events(), first, timeout=QUEUE_TIMEOUT)
            active = active_codex_turn(EventLog(state / "events.jsonl"), state, "dev-1")
            assert active and active["turn_id"] == facts["turn"] and active["session_id"] == facts["session"]
            registry = RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(project))
            assert str(registry.get("dev-1")) == facts["session"]
            transcript = _codex_transcript(state, "dev-1", facts["session"], first.payload["workdir"])
            assert registry.get_path("dev-1") == transcript
            first_turn = turn_evidence(transcript_rows(transcript), session=facts["session"], turn=facts["turn"],
                                       workdir=first.payload["workdir"], required_command=release.name, closed=False)
            preserved_ref = _git(project, "rev-parse", "task/TASK-1")
            assert _git(project, "show", "task/TASK-1:a.txt") == f"{marker}-1"
            observed.update(queue=facts, open_turn=first_turn, first_task_ref=preserved_ref)

            [checkpoint] = build_workflow_resume_projection(state, config)["batch_checkpoints"]
            assert checkpoint["safe_resume_action"] == "resume_queued_children"
            assert checkpoint["completed_task_ids"] == ["TASK-1"]
            args = ("recover", "workflow", "--resume-pending", "--json",
                    "--checkpoint-id", checkpoint["checkpoint_id"])
            for extra, applied in ((("--dry-run",), 0), ((), 1), ((), 0)):
                assert json.loads(cli.command(*args, *extra))["applied"] == applied
            # A live watcher may append telemetry during dry-run/replay, so the
            # invariant is no extra resume request, not byte-identical ledger.
            [resumed] = [e for e in cli.events() if e.type == "task_map.ready"
                         and e.payload.get("source") == "workflow_resume_batch"]
            assert resumed.payload["task_ids"] == ["TASK-2"]
            assert resumed.payload["completed_task_ids"] == ["TASK-1"]
            assert resumed.payload["resume_scope"] == "gap_tasks_only"
            assert [e for e in cli.events() if e.type == "fanout.child.dispatched"] == [first]

            release.touch()
            second = cli.wait(
                lambda events: next((e for e in events if e.type == "fanout.child.dispatched"
                                     and e.payload.get("task_id") == "TASK-2"), None),
                deadline,
            )
            assert second.payload["task_id"] == "TASK-2"
            stops = [e for e in cli.events() if e.type == "codex.hook.stop" and e.actor == "dev-1"
                     and e.payload.get("session_id") == facts["session"]
                     and e.payload.get("turn_id") == facts["turn"]]
            assert stops, "missing matching native Stop hook"
            ids = [e.id for e in cli.events()]
            assert ids.index(facts["cancelled_id"]) < ids.index(stops[0].id) < ids.index(second.id)
            observed["closed_first_turn"] = turn_evidence(
                transcript_rows(transcript), session=facts["session"], turn=facts["turn"],
                workdir=first.payload["workdir"], required_command=release.name, closed=True,
            )
            candidate = cli.wait(
                lambda events: next((e for e in events if e.type == "candidate.ready"), None),
                deadline,
            )
            head = candidate.payload.get("candidate_head_commit")
            assert head
            for index, name in enumerate(("a.txt", "b.txt"), 1):
                assert subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=project, timeout=10) == f"{marker}-{index}\n".encode()
            assert _git(project, "rev-parse", "task/TASK-1") == preserved_ref
            assert _git(project, "show", "task/TASK-1:a.txt") == f"{marker}-1"
            events = cli.events()
            [second_open] = [e for e in events if e.type == "codex.hook.user_prompt_submit"
                             and e.causation_id == second.id and e.actor == "dev-1"]
            session2, turn2 = second_open.payload["session_id"], second_open.payload["turn_id"]
            assert turn2 != facts["turn"]
            transcript2 = _codex_transcript(state, "dev-1", session2, second.payload["workdir"])
            cli.wait(lambda rows: any(e.type == "codex.hook.stop" and e.actor == "dev-1"
                                     and e.payload.get("session_id") == session2
                                     and e.payload.get("turn_id") == turn2 for e in rows), deadline)
            observed["second_turn"] = turn_evidence(
                transcript_rows(transcript2), session=session2, turn=turn2,
                workdir=second.payload["workdir"], required_command="dev.build.done", closed=True,
            )
            assert [e.payload["task_id"] for e in events if e.type == "fanout.child.dispatched"] == ["TASK-1", "TASK-2"]
            assert sum(e.type == "candidate.ready" for e in events) == 1
            canonical_types = {"fanout.child.dispatched", "candidate.ready", "dev.build.done", "fanout.cancelled"}
            canonical = [e.id for e in events if e.type in canonical_types]
            cli.stop()
            starts = sum(e.type == "loop.started" for e in cli.events())
            cli.start()
            cli.wait(lambda rows: sum(e.type == "loop.started" for e in rows) > starts, deadline)
            cli.stop()
            assert [e.id for e in cli.events() if e.type in canonical_types] == canonical
            assert _git(project, "rev-parse", "task/TASK-1") == preserved_ref
            observed.update({
                "fanout_id": fanout_id, "checkpoint_id": checkpoint["checkpoint_id"],
                "candidate_id": candidate.id, "candidate_head_commit": head,
                "dispatches": len([e for e in cli.events() if e.type == "fanout.child.dispatched"]),
            })
            observed["complete"] = True
        finally:
            release.touch()  # Unblock only this test's bounded tool before shutdown.
            EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
                type="simulation.done", actor="testkit", payload={"reason": "k594-native-finished"},
            ))
            if cli.process is not None:
                try:
                    cli.stop()
                except Exception as exc:
                    observed["cleanup_error"] = str(exc)
                    if cli.process is not None:
                        _terminate(cli.process)
            if cli.tmux.has_session():
                cli.tmux.kill_session()
            for path in (state / "workdirs/dev-1/codex-home/sessions").rglob("rollout-*.jsonl"):
                assert path.resolve().is_relative_to(state.resolve())
                transcripts.append({"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
                                    "rows": redact_obj(transcript_rows(path))})
            final_source = capture_source(repo)
            observed.update(starts=cli.starts, commands=cli.commands, pids=sorted(cli.pids),
                            transcripts=transcripts, events=[asdict(e) for e in cli.events()],
                            logs={p.name: p.read_text()[-16000:] for p in project.glob("start-*.log")},
                            source_before=source["files_sha256"], source_after=final_source["files_sha256"])
            record_property("native_codex_legacy_queue", json.dumps(redact_obj(observed), ensure_ascii=False))
            _assert_gone(cli.pids)
            assert not cli.tmux.has_session()
            assert "cleanup_error" not in observed, observed.get("cleanup_error")
            assert source["git_head"] == final_source["git_head"]
            assert source["files_sha256"] == final_source["files_sha256"]
