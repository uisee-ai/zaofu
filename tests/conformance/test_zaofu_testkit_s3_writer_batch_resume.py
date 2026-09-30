"""Real writer queue timeout -> approved gap-only batch resume.

Uses the legacy writer fixture (canonical tasks are Given; default compatibility
result protocol, not the blocking Task Pipeline). Worker transport/provider-turn
and time are inert inputs. Queue/cancel/child/candidate kernel facts are real.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from tests.test_writer_fanout_runtime import (
    _child,
    _commit,
    _fanout_id,
    _git,
    _manifest,
    _RecordingTransport,
    _seed_tasks,
    _state,
)
from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.events import ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_resume import build_workflow_resume_projection


class ResumedCandidateDropsCompletedTask(AssertionError):
    """A published gap-only candidate omits an already accepted upstream TaskRef."""


def _queued_writer_checkpoint(tmp_path, monkeypatch):
    state, log, transport, runtime = _state(
        tmp_path,
        affinity_stage_slots=True,
        affinity_lane_count=1,
    )
    task_map = state / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {"task_id": "TASK-1", "affinity_tag": "core", "allowed_paths": ["a.txt"]},
            {"task_id": "TASK-2", "affinity_tag": "assembly", "allowed_paths": ["b.txt"]},
        ],
    }), encoding="utf-8")
    _seed_tasks(state, task_ids=("TASK-1", "TASK-2"))
    runtime.config.workflow.stages[0].timeout_seconds = 3600

    provider_turn_active = False
    runtime._active_provider_turn = lambda instance_id: (  # type: ignore[method-assign]
        {"turn_id": "turn-old"}
        if provider_turn_active and instance_id == "dev-1"
        else None
    )
    trigger = runtime.event_writer.append(ZfEvent(
        type="task_map.ready", actor="planner-fixture", correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    ))
    runtime.run_once(events=[trigger])
    fanout_id = _fanout_id(log)
    first = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-1"
    )
    assert _child(_manifest(state, fanout_id), "TASK-2")["status"] == "queued"
    assert [sent[0] for sent in transport.sent] == ["dev-1"]
    provider_turn_active = True
    commit = _commit(Path(first.payload["workdir"]), "a.txt", "TASK-1\n", "test: TASK-1")

    completed = runtime.event_writer.append(ZfEvent(
        type="dev.build.done",
        actor="dev-1", origin="worker",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": first.payload["child_id"],
            "run_id": first.payload["run_id"],
            "dispatch_id": first.payload["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit,
            "source_branch": first.payload["source_branch"],
            "workdir": first.payload["workdir"],
        },
    ))
    runtime.run_once(events=[completed])
    assert _child(_manifest(state, fanout_id), "TASK-1")["status"] == "completed"
    assert _child(_manifest(state, fanout_id), "TASK-2")["status"] == "queued"

    with monkeypatch.context() as clock:
        clock.setattr(runtime, "_now", lambda: time.time() + 7200)
        runtime.run_once(events=[])

    cancelled = [
        event for event in log.read_all()
        if event.type == "fanout.cancelled"
        and event.payload.get("reason") == "queued_wait_timeout"
    ]
    assert len(cancelled) == 1
    assert cancelled[0].payload["queued_children"] == [_child(
        _manifest(state, fanout_id), "TASK-2"
    )["child_id"]]
    assert cancelled[0].payload["semantic_attempt_consumed"] is False
    assert cancelled[0].payload["completed_task_ids"] == ["TASK-1"]
    [checkpoint] = build_workflow_resume_projection(
        state, runtime.config,
    )["batch_checkpoints"]
    assert checkpoint["safe_resume_action"] == "resume_queued_children"
    assert checkpoint["pending_children"] == cancelled[0].payload["queued_children"]
    assert checkpoint["completed_task_ids"] == ["TASK-1"]
    return runtime, transport, checkpoint, commit


def test_writer_queue_timeout_resume_preserves_completed_task_in_candidate(
    tmp_path, monkeypatch, record_property,
):
    runtime, transport, checkpoint, commit = _queued_writer_checkpoint(tmp_path, monkeypatch)
    state, log = runtime.state_dir, runtime.event_log

    requested = runtime.event_writer.append(ZfEvent(
        type="control.action.requested",
        actor="operator",
        correlation_id="trace-1",
        payload={
            "action": "workflow-batch-resume",
            "checkpoint_id": checkpoint["checkpoint_id"],
            "safe_resume_action": checkpoint["safe_resume_action"],
        },
    ))
    result = ControlledActionService(
        state, runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, actor="operator", source="test", surface="test",
    ).execute(
        action="workflow-batch-resume",
        requested_action="workflow-batch-resume",
        payload={
            "checkpoint_id": checkpoint["checkpoint_id"],
            "safe_resume_action": checkpoint["safe_resume_action"],
        },
        requested=requested,
    )
    assert result["status"] == "applied", result

    resumed = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert len(resumed) == 1
    assert resumed[0].payload["task_ids"] == ["TASK-2"]
    assert resumed[0].payload["resume_scope"] == "gap_tasks_only"
    assert resumed[0].payload["completed_task_ids"] == ["TASK-1"]

    runtime = Orchestrator(state, runtime.config, transport)
    runtime.run_once()
    _finish_resumed_writer(runtime, transport, checkpoint, commit, record_property)


def _finish_resumed_writer(runtime, transport, checkpoint, commit, record_property):
    state, log, tmp_path = runtime.state_dir, runtime.event_log, runtime.project_root
    [resumed] = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    dispatches = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-2"
    ]
    assert len(dispatches) == 1
    assert dispatches[0].payload["task_id"] == "TASK-2"
    assert len([
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-1"
    ]) == 1
    assert not any(event.type == "kernel.housekeeping.failed" for event in log.read_all())

    second = dispatches[0]
    second_commit = _commit(Path(second.payload["workdir"]), "b.txt", "TASK-2\n", "test: TASK-2")
    second_result = runtime.event_writer.append(ZfEvent(
        type="dev.build.done", actor=second.payload["role_instance"], origin="worker",
        task_id="TASK-2", correlation_id="trace-1",
        payload={
            "fanout_id": second.payload["fanout_id"],
            "child_id": second.payload["child_id"],
            "run_id": second.payload["run_id"],
            "dispatch_id": second.payload["run_id"],
            "pdd_id": "F-11111111", "source_commit": second_commit,
            "source_branch": second.payload["source_branch"], "workdir": second.payload["workdir"],
        },
    ))
    runtime.run_once(events=[second_result])
    [ready] = [event for event in log.read_all() if event.type == "candidate.ready"]
    head = ready.payload["candidate_head_commit"]
    assert _git(tmp_path, "show", f"{head}:b.txt") == "TASK-2"
    assert _git(tmp_path, "show", "task/TASK-1:a.txt") == "TASK-1"
    candidate_files = _git(tmp_path, "ls-tree", "--name-only", head).splitlines()
    record_property("writer_resume_candidate_observation", json.dumps({
        "state_dir": str(state), "candidate_head_commit": head,
        "candidate_files": candidate_files, "original_task_commit": commit,
        "checkpoint_completed_task_ids": checkpoint["completed_task_ids"],
        "resumed_completed_task_ids": resumed.payload["completed_task_ids"],
        "resume_task_ids": resumed.payload["task_ids"],
    }))
    if "a.txt" not in candidate_files:
        raise ResumedCandidateDropsCompletedTask(
            f"candidate {head} contains TASK-2 but drops accepted task/TASK-1:a.txt; "
            f"checkpoint.completed_task_ids={checkpoint['completed_task_ids']!r}; "
            f"resume.completed_task_ids={resumed.payload['completed_task_ids']!r}"
        )
    assert _git(tmp_path, "show", f"{head}:a.txt") == "TASK-1"
    for _ in range(2):
        runtime = Orchestrator(state, runtime.config, transport)
        runtime.run_once()
    events = log.read_all()
    assert sum(event.type == "candidate.ready" for event in events) == 1
    assert sum(event.type == "fanout.child.dispatched" for event in events) == 2


def _writer_process_phase(project: Path, phase: str) -> dict:
    """Independent interpreter entry; only zf.yaml, ledger and Git cross phases."""
    if phase == "checkpoint":
        with pytest.MonkeyPatch.context() as clock:
            runtime, transport, checkpoint, _commit_id = _queued_writer_checkpoint(project, clock)
        runtime.config.project.state_dir = runtime.state_dir.relative_to(project).as_posix()
        (project / "zf.yaml").write_text(
            yaml.safe_dump(renderable_config_to_primitive(runtime.config)), encoding="utf-8",
        )
    else:
        config = load_config(project / "zf.yaml")
        state = project / config.project.state_dir
        transport = _RecordingTransport()
        runtime = Orchestrator(state, config, transport, project_root=project)
        if phase == "dispatch":
            runtime.run_once()
        elif phase == "complete":
            events = runtime.event_log.read_all()
            [first_result] = [e for e in events
                              if e.type == "dev.build.done" and e.task_id == "TASK-1"]
            [resumed] = [e for e in events if e.type == "task_map.ready"
                         and e.payload.get("source") == "workflow_resume_batch"]
            _finish_resumed_writer(
                runtime, transport,
                {"completed_task_ids": resumed.payload["completed_task_ids"]},
                first_result.payload["source_commit"], lambda *_args: None,
            )
        elif phase == "replay":
            runtime.run_once()
        else:
            raise AssertionError(f"unknown phase: {phase}")
    events = runtime.event_log.read_all()
    dispatched = [e for e in events if e.type == "fanout.child.dispatched"]
    candidates = [e for e in events if e.type == "candidate.ready"]
    assert not any(e.type in {"kernel.housekeeping.failed", "orchestrator.tick.failed"}
                   for e in events)
    receipt = {
        "phase": phase, "pid": os.getpid(), "state_dir": str(runtime.state_dir),
        "dispatch_ids": [e.id for e in dispatched],
        "dispatch_tasks": [e.payload["task_id"] for e in dispatched],
        "sent": len(transport.sent), "candidate_ids": [e.id for e in candidates],
        "resume_ids": [e.id for e in events if e.type == "task_map.ready"
                       and e.payload.get("source") == "workflow_resume_batch"],
    }
    if phase == "checkpoint":
        receipt["checkpoint_id"] = checkpoint["checkpoint_id"]
    if candidates:
        [candidate] = candidates
        head = candidate.payload["candidate_head_commit"]
        receipt["candidate_head"] = head
        receipt["contents"] = {
            name: _git(project, "show", f"{head}:{name}") for name in ("a.txt", "b.txt")
        }
    return receipt


@pytest.mark.parametrize("abrupt_exit", [False, True], ids=["clean-exit", "abrupt-exit"])
def test_writer_batch_resume_across_independent_processes(record_property, abrupt_exit):
    """Restart after checkpoint, authorization and dispatch; no inherited objects."""
    repo = Path(__file__).resolve().parents[2]
    entry = (
        "import json, os, sys; from pathlib import Path; "
        "from tests.conformance.test_zaofu_testkit_s3_writer_batch_resume "
        "import _writer_process_phase; "
        "print('K542_RECEIPT=' + json.dumps(_writer_process_phase(Path(sys.argv[1]), sys.argv[2])), "
        "flush=True);\nif int(sys.argv[3]): os._exit(int(sys.argv[3]))"
    )
    receipts = []
    with TemporaryDirectory(prefix="zf-k542-writer-process-20260924-") as directory:
        project = Path(directory)

        def phase(name):
            # Abrupt checkpoints bypass interpreter teardown only after the real
            # durable checkpoint/dispatch. This is not an in-write or OS crash.
            exit_code = 73 if abrupt_exit and name in {"checkpoint", "dispatch"} else 0
            result = subprocess.run(
                [sys.executable, "-c", entry, str(project), name, str(exit_code)], cwd=repo,
                text=True, capture_output=True, timeout=60,
            )
            assert result.returncode == exit_code, result.stdout + result.stderr
            [body] = [line.removeprefix("K542_RECEIPT=") for line in result.stdout.splitlines()
                      if line.startswith("K542_RECEIPT=")]
            receipt = json.loads(body)
            receipt["exit_code"] = result.returncode
            assert receipt["pid"] != os.getpid()
            receipts.append(receipt)
            return receipt

        initial = phase("checkpoint")
        assert initial["dispatch_tasks"] == ["TASK-1"]
        assert initial["candidate_ids"] == initial["resume_ids"] == []
        args = ["recover", "workflow", "--resume-pending", "--json",
                "--checkpoint-id", initial["checkpoint_id"]]
        state = Path(initial["state_dir"])

        def recover(*extra):
            result = subprocess.run(
                [sys.executable, "-m", "zf.cli.main", *args, *extra], cwd=project,
                text=True, capture_output=True, timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            return json.loads(result.stdout)

        before_dry = (state / "events.jsonl").read_bytes()
        assert recover("--dry-run")["applied"] == 0
        assert (state / "events.jsonl").read_bytes() == before_dry
        assert recover()["applied"] == 1
        after_apply = (state / "events.jsonl").read_bytes()
        assert recover()["applied"] == 0
        assert (state / "events.jsonl").read_bytes() == after_apply
        dispatched = phase("dispatch")
        assert dispatched["dispatch_tasks"] == ["TASK-1", "TASK-2"]
        assert dispatched["sent"] == 1
        assert len(dispatched["resume_ids"]) == 1
        assert dispatched["candidate_ids"] == []
        completed = phase("complete")
        assert completed["dispatch_ids"] == dispatched["dispatch_ids"]
        assert completed["sent"] == 0
        assert len(completed["candidate_ids"]) == 1
        assert completed["contents"] == {"a.txt": "TASK-1", "b.txt": "TASK-2"}
        for _ in range(2):
            assert recover()["applied"] == 0
            replay = phase("replay")
            for key in ("dispatch_ids", "resume_ids", "candidate_ids", "candidate_head", "contents"):
                assert replay[key] == completed[key], (key, replay)
            assert replay["sent"] == 0
        assert len({r["pid"] for r in receipts}) == len(receipts)
        record_property("writer_resume_process_receipts", json.dumps(receipts))
