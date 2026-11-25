from datetime import datetime, timedelta, timezone
import json
from zf.core.events import ZfEvent
from zf.runtime.board_liveness import board_silence_attention
from tests.test_run_manager_diagnosis_consumer import _setup, _tick
from zf.runtime.supervisor_inspection import build_supervisor_snapshot
from zf.core.state.atomic_io import atomic_write_text


def test_silent_board_single_diagnosis_and_late_progress(tmp_path, monkeypatch):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    board = {
        "enabled": True,
        "members": ["dev", "verify"],
        "generation": "1",
        "silence_seconds": 120,
    }
    cfg.workflow.flow_metadata["run_boards"] = {"r": board}
    now = datetime.now(timezone.utc)
    for role in board["members"]:
        writer.append(
            ZfEvent(
                type="workflow.operation.requested",
                origin="kernel",
                actor="zf-cli",
                ts=(now - timedelta(minutes=10)).isoformat(),
                payload={
                    "workflow_run_id": "r",
                    "operation_id": "op-" + role,
                    "request_hash": "digest-" + role,
                    "role_instance": role,
                },
            )
        )

    def query():
        return board_silence_attention(state, cfg, log.read_all(), now)

    quiet = query()
    assert len(quiet) == 1 and quiet[0]["suggested_route"] == "run_manager_recovery"
    full = build_supervisor_snapshot(state, config=cfg, project_root=tmp_path)
    assert quiet[0]["fingerprint"] in {
        row["fingerprint"] for row in full["attention_items"]
    }
    # The persisted Supervisor input is consumed by the actual Run Manager tick.
    atomic_write_text(
        state / "projections/supervisor/snapshot.json",
        json.dumps({**full, "attention_items": quiet}),
    )
    _tick(state, log, writer, cfg, tmp_path)
    _tick(state, log, writer, cfg, tmp_path)
    assert (
        len(
            [
                e
                for e in log.read_all()
                if e.type == "run.manager.resident.prompted"
                and e.payload.get("reprompt")
            ]
        )
        == 1
    )
    writer.append(
        ZfEvent(
            type="worker.heartbeat",
            actor="dev",
            ts=now.isoformat(),
            payload={"workflow_run_id": "r", "operation_id": "op-dev"},
        )
    )
    assert not query()
    assert not board_silence_attention(
        state, cfg, log.read_all(), now + timedelta(hours=1), paused=True
    )


def test_unassigned_execution_frontier_can_wake_rm_without_a_worker(tmp_path):
    from zf.core.task.schema import Task, TaskExecutionBinding
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    config = SimpleNamespace(
        workflow=SimpleNamespace(
            flow_metadata={
                "run_boards": {
                    "unattended": {
                        "enabled": True,
                        "generation": "1",
                        "members": ["dev"],
                        "silence_seconds": 120,
                    }
                }
            }
        )
    )
    task = Task(
        id="TASK-1",
        title="unclaimed",
        status="in_progress",
        execution_binding=TaskExecutionBinding(workflow_run_id="unattended"),
        created_at=(now - timedelta(minutes=5)).isoformat(),
    )
    (report,) = board_silence_attention(tmp_path, config, [], now, tasks=[task])
    assert report["suggested_action"]["task_ids"] == ["TASK-1"]
    assert not report["suggested_action"]["authorizes_retry"]
    task.status = "done"
    assert not board_silence_attention(tmp_path, config, [], now, tasks=[task])


def test_matching_wait_and_live_lease_suppress_only_until_expiry(tmp_path, monkeypatch):
    from zf.core.state.task_attempts import TaskAttemptStore

    state, log, writer, cfg, _ = _setup(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    then = (now - timedelta(minutes=10)).isoformat()
    deadline = (now + timedelta(seconds=20)).isoformat()
    cfg.workflow.flow_metadata["run_boards"] = {
        "r": {
            "enabled": True,
            "members": ["dev"],
            "generation": "v1",
            "silence_seconds": 60,
        }
    }
    writer.append(
        ZfEvent(
            type="workflow.operation.requested",
            origin="kernel",
            actor="kernel",
            ts=then,
            payload={
                "workflow_run_id": "r",
                "operation_id": "op",
                "request_hash": "req",
                "role_instance": "dev",
                "active_attempt_id": "a",
            },
        )
    )
    wait = {
        "enabled": True,
        "action": "notify",
        "workflow_run_id": "r",
        "operation_id": "op",
        "request_hash": "req",
        "actor": "dev",
        "attempt_id": "a",
        "effective_at": then,
        "deadline": deadline,
    }
    cfg.workflow.flow_metadata["orchestration_schedules"] = [wait]

    def check(at):
        return board_silence_attention(state, cfg, log.read_all(), at)

    assert not check(now)
    assert check(now + timedelta(seconds=21))
    wait["attempt_id"] = "previous"
    assert check(now)
    store = TaskAttemptStore(state / "task_attempts.json")
    store.ensure_for_dispatch(
        run_id="r",
        task_id="t",
        dispatch_id="dispatch",
        role="dev",
        instance_id="dev",
        operation_id="op",
        briefing_ref="brief",
        created_at=then,
        lease_expires_at=deadline,
        max_attempts=2,
    )
    assert not check(now)
    assert check(now + timedelta(seconds=21))
