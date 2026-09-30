"""Real proposal execution across process crashes, without an idempotent spy.

The observer counts successful local TaskStore.update calls, not remote
effects. os._exit skips Python cleanup; it does not simulate power loss.
Only the child process is instrumented. Production code never reads the
observer receipts or the fault witness.
"""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

from tests.testkit.command_runner import run_command
from tests.testkit.evidence import write_json_atomic
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService


MODULE = "tests.conformance.test_zaofu_testkit_s4_action_crash"
TASK_ID = "ACTION-CRASH-TASK"
PROPOSAL_ID = "action-crash-proposal"
RESOLVED = "kanban.agent.proposal.resolved"
CRASH_EXIT = 86


class DuplicateActionEffect(AssertionError):
    """A confirmed repeated business effect, not a harness/timeout error."""


DUPLICATE_WINDOW = pytest.mark.xfail(
    strict=True, raises=DuplicateActionEffect,
    reason="ZF-TEST-032: proposal replay repeats a committed update before resolution is recorded",
)
POINTS = (
    "before_store_update",
    pytest.param("after_store_update_before_task_updated", marks=DUPLICATE_WINDOW),
    pytest.param("after_action_result_before_resolution", marks=DUPLICATE_WINDOW),
    "after_resolution",
)


def _seed(root: Path) -> None:
    state = root / "state"
    state.mkdir(parents=True)
    TaskStore(state / "kanban.json").add(Task(
        id=TASK_ID, title="crash target", priority=3,
    ))
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
        id=PROPOSAL_ID, type="kanban.agent.action.proposed",
        actor="web", task_id=TASK_ID, correlation_id="action-crash-run",
        payload={"proposal": {
            "proposal_id": "action-crash-lineage", "revision": 1,
            "action": "update-task", "valid": True,
            "payload": {"task_id": TASK_ID, "priority": 1},
        }},
    ))


def _child(root: Path, name: str, fail_at: str) -> None:
    state = root / "state"
    evidence = root / "evidence"
    evidence.mkdir(exist_ok=True)

    def hit(point: str) -> None:
        if fail_at == point:
            write_json_atomic(evidence / "fault-witness.json", {
                "point": point, "pid": os.getpid(), "exit_code": CRASH_EXIT,
            })
            os._exit(CRASH_EXIT)

    original_update = TaskStore.update

    def observe_update(store: TaskStore, task_id: str, **kwargs: Any) -> Task | None:
        target = store.path == state / "kanban.json" and task_id == TASK_ID
        if target:
            hit("before_store_update")
        result = original_update(store, task_id, **kwargs)
        if target and result is not None:
            # Always append AFTER the real successful write. No cache, key
            # suppression or read of prior observer rows can hide a replay.
            with (evidence / "observed-updates.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "task_id": task_id, "updates": kwargs, "pid": os.getpid(),
                }) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return result

    class FaultWriter(EventWriter):
        def emit(self, event_type: str, **kwargs: Any) -> ZfEvent:
            if event_type == "task.updated":
                hit("after_store_update_before_task_updated")
            if event_type == RESOLVED:
                hit("after_action_result_before_resolution")
            event = super().emit(event_type, **kwargs)
            if event_type == RESOLVED:
                hit("after_resolution")
            return event

    writer = FaultWriter(EventLog(state / "events.jsonl"))
    payload = {"task_id": TASK_ID, "priority": 1, "proposal_event_id": PROPOSAL_ID}
    requested = writer.emit(
        "web.action.requested", actor="operator", task_id=TASK_ID,
        correlation_id="action-crash-run", payload={"request": payload},
    )
    service = ControlledActionService(
        state, writer, project_root=root,
        actor="operator", source="web", surface="web",
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(TaskStore, "update", observe_update)
        result = service.execute(
            action="update-task", requested_action="update-task",
            payload=payload, requested=requested,
        )
    write_json_atomic(evidence / f"{name}.result.json", result)


def _invoke(root: Path, name: str, fail_at: str = "") -> dict[str, Any]:
    receipt = run_command(
        [sys.executable, "-m", MODULE, "--child", str(root), name, fail_at],
        command_id=name, cwd=Path(__file__).resolve().parents[2],
        output_dir=root / "evidence", timeout_seconds=30,
    )
    assert not receipt.timed_out and not receipt.environment_error, receipt
    stderr = Path(receipt.stderr_ref).read_text(encoding="utf-8")
    assert receipt.exit_code == (CRASH_EXIT if fail_at else 0), (receipt, stderr)
    if fail_at:
        witness = json.loads((root / "evidence/fault-witness.json").read_text())
        assert witness["point"] == fail_at and witness["exit_code"] == CRASH_EXIT
        assert witness["pid"] != os.getpid()
        return witness
    result = json.loads((root / "evidence" / f"{name}.result.json").read_text())
    assert result["ok"] is True, result
    return result


def _snapshot(root: Path) -> dict[str, Any]:
    task = TaskStore(root / "state/kanban.json").get(TASK_ID)
    assert task is not None
    events = EventLog(root / "state/events.jsonl").read_all()
    assert len({event.id for event in events}) == len(events)
    counts = Counter(event.type for event in events)
    assert not any(counts[kind] for kind in ("task.created", "task.done", "run.goal.completed"))
    requests = {event.id for event in events if event.type == "web.action.requested"}
    for event in events:
        if event.type in {"task.updated", RESOLVED}:
            assert event.task_id == TASK_ID and event.causation_id in requests
            assert event.correlation_id == "action-crash-run"
        if event.type == RESOLVED:
            assert event.payload["proposal_event_id"] == PROPOSAL_ID
    observer = root / "evidence/observed-updates.jsonl"
    effects = [json.loads(line) for line in observer.read_text().splitlines()] if observer.exists() else []
    assert all(row["task_id"] == TASK_ID and row["updates"] == {"priority": 1} for row in effects)
    return {
        "task": {"id": task.id, "priority": task.priority, "status": task.status},
        "effects": effects, "event_counts": dict(counts),
    }


def _assert_same_business_result(baseline: dict, recovered: dict) -> None:
    assert recovered["task"] == baseline["task"]
    assert recovered["event_counts"].get(RESOLVED, 0) == 1
    effects = len(recovered["effects"])
    updates = recovered["event_counts"].get("task.updated", 0)
    if effects > len(baseline["effects"]) or updates > baseline["event_counts"]["task.updated"]:
        raise DuplicateActionEffect(
            f"same proposal repeated: update calls={effects}, task.updated={updates}; "
            f"baseline={baseline!r}; recovered={recovered!r}"
        )
    assert effects == 1 and updates == 1, recovered


def _baseline(root: Path) -> dict[str, Any]:
    _seed(root)
    assert _invoke(root, "apply")["status"] == "completed"
    assert _invoke(root, "replay")["status"] == "already_resolved"
    state = _snapshot(root)
    assert len(state["effects"]) == 1
    assert state["event_counts"]["task.updated"] == state["event_counts"][RESOLVED] == 1
    return state


def test_controlled_action_completed_replay_is_deduplicated_in_new_process(tmp_path: Path) -> None:
    _baseline(tmp_path / "baseline")


@pytest.mark.parametrize("fail_at", POINTS)
def test_controlled_action_commit_window_recovers_without_duplicate_mutation(
    tmp_path: Path, fail_at: str,
) -> None:
    baseline = _baseline(tmp_path / "baseline")
    root = tmp_path / "fault"
    _seed(root)
    _invoke(root, "crash", fail_at)
    at_crash = _snapshot(root)
    counts = at_crash["event_counts"]
    before_write = fail_at == "before_store_update"
    assert len(at_crash["effects"]) == (0 if before_write else 1)
    assert at_crash["task"]["priority"] == (3 if before_write else 1)
    after_result = fail_at in {"after_action_result_before_resolution", "after_resolution"}
    assert counts.get("task.updated", 0) == int(after_result)
    assert counts.get("runtime.action.attempt.completed", 0) == int(after_result)
    assert counts.get(RESOLVED, 0) == int(fail_at == "after_resolution")
    write_json_atomic(root / "evidence/crash-state.json", at_crash)

    _invoke(root, "recover")
    recovered = _snapshot(root)
    # After resolution, another new process must not add any business effect.
    assert _invoke(root, "replay")["status"] == "already_resolved"
    replayed = _snapshot(root)
    assert replayed["effects"] == recovered["effects"]
    assert replayed["event_counts"].get("task.updated", 0) == recovered["event_counts"].get("task.updated", 0)
    write_json_atomic(root / "evidence/recovery-comparison.json", {
        "fail_at": fail_at, "baseline": baseline, "at_crash": at_crash,
        "recovered": recovered, "replayed": replayed,
    })
    _assert_same_business_result(baseline, replayed)


if __name__ == "__main__":
    assert len(sys.argv) == 5 and sys.argv[1] == "--child"
    _child(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
