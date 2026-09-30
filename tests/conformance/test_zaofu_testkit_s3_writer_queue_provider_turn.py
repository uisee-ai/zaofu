"""Real hook CLI and wall-clock queue recovery, with simulated worker semantics.

No Provider CLI is launched. Unlike K542, the runtime's active-turn query and
clock are not replaced: hook input crosses the real subprocess CLI boundary.
"""

import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_writer_batch_resume import _finish_resumed_writer
from tests.test_writer_fanout_runtime import _child, _commit, _fanout_id, _manifest, _seed_tasks, _state
from zf.core.config.render import renderable_config_to_primitive
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_contract import build_run_contract, write_run_contract
from zf.runtime.workflow_resume import build_workflow_resume_projection


def _hook(runtime, event: str, *, backend: str, session: str, turn: str) -> ZfEvent:
    namespace = "codex" if backend == "codex" else "claude"
    event_type = f"{namespace}.hook.{event}"
    result = subprocess.run(
        [sys.executable, "-m", "zf.cli.main", "hook-recv", "--event", event_type,
         "--backend", backend, "--state-dir", str(runtime.state_dir)],
        cwd=runtime.project_root, input=json.dumps({"session_id": session, "turn_id": turn}),
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    event = [e for e in runtime.event_log.read_all() if e.type == event_type][-1]
    assert event.payload["session_id"] == session
    if backend == "codex":
        assert event.payload["turn_id"] == turn
    return event


def _first_task_completed_with_open_turn(project: Path, backend: str, *, timeout: int = 60):
    project.mkdir()
    state, log, transport, runtime = _state(project, affinity_stage_slots=True, affinity_lane_count=1)
    _seed_tasks(state, task_ids=("TASK-1", "TASK-2"))
    role = next(role for role in runtime.config.roles if role.instance_id == "dev-1")
    role.backend = backend
    runtime.config.workflow.stages[0].timeout_seconds = timeout
    config_path = project / "zf.yaml"
    config_path.write_text(yaml.safe_dump(renderable_config_to_primitive(runtime.config)))
    write_run_contract(state, build_run_contract(
        runtime.config, config_path=config_path, project_root=project, state_dir=state,
    ))
    runtime.event_writer.append(ZfEvent(type="task_map.ready", actor="planner-fixture",
                                       correlation_id="trace-1", payload={"pdd_id": "F-11111111"}))
    runtime.run_once()
    [first] = [e for e in log.read_all() if e.type == "fanout.child.dispatched"]
    session = str(uuid4())
    registry = RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(project))
    if backend == "codex":
        registry.bind_codex_session("dev-1", session)
    else:
        session = str(registry.get_or_create("dev-1", backend=backend))
    opened = _hook(runtime, "user_prompt_submit", backend=backend, session=session, turn="turn-1")
    assert opened.actor == "dev-1" and opened.causation_id == first.id
    commit = _commit(Path(first.payload["workdir"]), "a.txt", "TASK-1\n", "test: TASK-1")
    # This is the declared simulated worker semantic result, not a kernel verdict.
    runtime.event_writer.append(ZfEvent(type="dev.build.done", actor="dev-1", origin="worker",
        task_id="TASK-1", correlation_id="trace-1", payload={
            "fanout_id": first.payload["fanout_id"], "child_id": first.payload["child_id"],
            "run_id": first.payload["run_id"], "dispatch_id": first.payload["run_id"],
            "pdd_id": "F-11111111", "source_commit": commit,
            "source_branch": first.payload["source_branch"], "workdir": first.payload["workdir"],
        }))
    runtime.run_once()
    fanout = _fanout_id(log)
    assert _child(_manifest(state, fanout), "TASK-1")["status"] == "completed"
    return runtime, transport, fanout, session, commit


@pytest.mark.parametrize("backend", ["claude-code", "codex"])
def test_completed_writer_turn_uses_provider_lifecycle_not_stub(tmp_path, backend, record_property):
    runtime, transport, fanout, session, _ = _first_task_completed_with_open_turn(tmp_path / "project", backend)
    state = runtime.state_dir
    if backend == "claude-code":
        assert _child(_manifest(state, fanout), "TASK-2")["status"] == "dispatched"
    else:
        assert _child(_manifest(state, fanout), "TASK-2")["status"] == "queued"
        assert runtime._active_provider_turn("dev-1")["turn_id"] == "turn-1"
        # Reopen using only canonical persistent state; no lambda/clock survives.
        runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
        for wrong_session, wrong_turn in ((str(uuid4()), "turn-1"), (session, "turn-other")):
            _hook(runtime, "stop", backend=backend, session=wrong_session, turn=wrong_turn)
            runtime.run_once()
            assert runtime._active_provider_turn("dev-1")["turn_id"] == "turn-1"
            assert _child(_manifest(state, fanout), "TASK-2")["status"] == "queued"
        _hook(runtime, "stop", backend=backend, session=session, turn="turn-1")
        runtime.run_once()
        assert runtime._active_provider_turn("dev-1") is None
        assert _child(_manifest(state, fanout), "TASK-2")["status"] == "dispatched"
    events = runtime.event_log.read_all()
    assert sum(e.type == "fanout.child.dispatched" for e in events) == 2
    assert not any(e.type in {"fanout.cancelled", "fanout.timed_out"} for e in events)
    record_property("provider_turn_boundary", json.dumps({"backend": backend, "real_provider": False,
        "hook_cli": True, "active_turn_replaced": False, "clock_replaced": False}))


def test_hook_held_queue_naturally_times_out_then_cli_recovers_only_gap(tmp_path, record_property):
    runtime, transport, fanout, session, commit = _first_task_completed_with_open_turn(
        tmp_path / "project", "codex", timeout=10,
    )
    state, project = runtime.state_dir, runtime.project_root
    runtime = Orchestrator(state, runtime.config, transport, project_root=project)
    assert _child(_manifest(state, fanout), "TASK-2")["status"] == "queued"
    [started] = [e for e in runtime.event_log.read_all() if e.type == "fanout.started"]
    deadline = time.monotonic() + 20
    while True:
        runtime.run_once()
        cancelled = [e for e in runtime.event_log.read_all() if e.type == "fanout.cancelled"
                     and e.payload.get("reason") == "queued_wait_timeout"]
        if cancelled:
            break
        assert time.monotonic() < deadline, "natural queue timeout was not produced"
        time.sleep(0.2)
    assert len(cancelled) == 1
    elapsed = (datetime.fromisoformat(cancelled[0].ts) - datetime.fromisoformat(started.ts)).total_seconds()
    assert elapsed >= 10
    assert cancelled[0].payload["semantic_attempt_consumed"] is False
    assert cancelled[0].payload["completed_task_ids"] == ["TASK-1"]
    [checkpoint] = build_workflow_resume_projection(state, runtime.config)["batch_checkpoints"]
    assert checkpoint["safe_resume_action"] == "resume_queued_children"
    assert checkpoint["completed_task_ids"] == ["TASK-1"]
    assert runtime._active_provider_turn("dev-1")["turn_id"] == "turn-1"
    assert sum(e.type == "fanout.child.dispatched" for e in runtime.event_log.read_all()) == 1
    args = ["recover", "workflow", "--resume-pending", "--json", "--checkpoint-id", checkpoint["checkpoint_id"]]
    for extra, applied in ((["--dry-run"], 0), ([], 1), ([], 0)):
        before = (state / "events.jsonl").read_bytes()
        result = subprocess.run([sys.executable, "-m", "zf.cli.main", *args, *extra], cwd=project,
                                text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["applied"] == applied
        if not applied:
            assert (state / "events.jsonl").read_bytes() == before
    _hook(runtime, "stop", backend="codex", session=session, turn="turn-1")
    runtime = Orchestrator(state, runtime.config, transport, project_root=project)
    runtime.run_once()
    _finish_resumed_writer(runtime, transport, checkpoint, commit, record_property)
    record_property("queue_timeout_boundary", json.dumps({"real_provider": False, "hook_cli": True,
        "clock_replaced": False, "elapsed_seconds": elapsed,
        "fanout_id": fanout, "checkpoint_id": checkpoint["checkpoint_id"]}))
