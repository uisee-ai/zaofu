"""131-P0 E2: shadow spine 三投影 — 游标增量、attempt/stage/health 解释。"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_spine_projection import refresh_spine_projections


def _log(tmp_path: Path) -> EventLog:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    return EventLog(state_dir / "events.jsonl")


def _read(tmp_path: Path, name: str) -> dict:
    return json.loads(
        (tmp_path / ".zf" / "projections" / name).read_text(encoding="utf-8"),
    )


def test_attempt_lifecycle_and_incremental_cursor(tmp_path: Path) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    log.append(ZfEvent(type="task.dispatched", task_id="T-1",
                       payload={"role": "dev-1", "fanout_id": "f1"}))
    stats1 = refresh_spine_projections(state_dir, log)
    assert stats1["events_folded"] == 1

    log.append(ZfEvent(type="dev.build.done", task_id="T-1",
                       payload={"status": "completed"}))
    log.append(ZfEvent(type="task.dispatched", task_id="T-1",
                       payload={"role": "dev-2", "fanout_id": "f2"}))
    stats2 = refresh_spine_projections(state_dir, log)
    # 增量:第二轮只折叠新 2 条,不重放第 1 条
    assert stats2["events_folded"] == 2

    attempts = _read(tmp_path, "task_attempts.json")
    entry = attempts["tasks"]["T-1"]
    assert entry["attempt_count"] == 2
    assert entry["attempts"][0]["terminal"]["type"] == "dev.build.done"
    assert entry["attempts"][1]["terminal"] is None
    assert entry["current_owner"] == "dev-2"

    # 无新事件 → 零折叠(游标生效)
    stats3 = refresh_spine_projections(state_dir, log)
    assert stats3["events_folded"] == 0


def test_standard_task_attempt_events_project_full_attempt_context(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    log.append(ZfEvent(
        type="task.attempt.started",
        task_id="T-STD",
        payload={
            "attempt_key": "attempt-1",
            "role_instance": "dev-lane-1",
            "fanout_id": "fanout-1",
            "lease_token": "lease-1",
        },
    ))
    log.append(ZfEvent(
        type="task.attempt.heartbeat",
        task_id="T-STD",
        payload={"attempt_key": "attempt-1"},
    ))
    log.append(ZfEvent(
        type="task.attempt.failed",
        task_id="T-STD",
        payload={"reason": "missing acceptance evidence"},
    ))
    log.append(ZfEvent(
        type="task.attempt.started",
        task_id="T-DEAD",
        payload={"attempt_key": "attempt-dead", "role": "dev-lane-2"},
    ))
    log.append(ZfEvent(
        type="task.attempt.deadlettered",
        task_id="T-DEAD",
        payload={"reason": "environment poison", "retryable": False},
    ))

    refresh_spine_projections(state_dir, log)

    attempts = _read(tmp_path, "task_attempts.json")
    std = attempts["tasks"]["T-STD"]
    assert std["attempt_count"] == 1
    assert std["latest_attempt_key"] == "attempt-1"
    assert std["latest_state"] == "failed"
    assert std["lease_state"] == "released"
    assert std["open_attempts"] == 0
    assert std["counted_failures"] == 1
    assert std["attempts"][0]["last_heartbeat_ts"]
    assert std["attempts"][0]["failure_signature"] == "task_attempt_failed"
    assert std["attempts"][0]["retryable"] is True

    dead = attempts["tasks"]["T-DEAD"]
    assert dead["latest_state"] == "deadlettered"
    assert dead["attempts"][0]["retryable"] is False
    assert dead["counted_failures"] == 1


def test_generic_child_completed_closes_attempt(tmp_path: Path) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    log.append(ZfEvent(
        type="task.dispatched",
        task_id="T-IMPL",
        payload={"role": "dev-lane-0", "dispatch_id": "disp-1"},
    ))
    log.append(ZfEvent(
        type="impl.child.completed",
        actor="dev-lane-0",
        task_id="T-IMPL",
        payload={"dispatch_id": "disp-1", "status": "completed"},
    ))

    refresh_spine_projections(state_dir, log)

    attempts = _read(tmp_path, "task_attempts.json")
    task = attempts["tasks"]["T-IMPL"]
    assert task["latest_state"] == "succeeded"
    assert task["lease_state"] == "released"
    assert task["open_attempts"] == 0
    assert task["last_terminal"] == "impl.child.completed"


def test_provider_activity_advances_matching_open_attempt_watermark(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="T-LONG",
        ts="2026-07-18T12:00:00+00:00",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "child-1",
            "run_id": "run-1",
            "role_instance": "dev-lane-0",
        },
    ))
    log.append(ZfEvent(
        type="agent.usage",
        actor="dev-lane-0",
        ts="2026-07-18T12:12:00+00:00",
        payload={"output_tokens": 19775},
    ))

    refresh_spine_projections(state_dir, log)

    attempt = _read(tmp_path, "task_attempts.json")["tasks"]["T-LONG"]["attempts"][0]
    assert attempt["attempt_key"] == "run-1"
    assert attempt["last_activity_ts"] == "2026-07-18T12:12:00+00:00"
    assert attempt["last_activity_event_type"] == "agent.usage"
    assert attempt["terminal"] is None


def test_new_dispatch_supersedes_prior_open_attempt_and_late_terminal_is_matched(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    for fanout_id, child_id, run_id, role in (
        ("fanout-1", "child-1", "run-1", "dev-lane-0"),
        ("fanout-2", "child-2", "run-2", "dev-lane-1"),
    ):
        log.append(ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            task_id="T-FENCE",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role,
            },
        ))
    log.append(ZfEvent(
        type="impl.child.completed",
        actor="dev-lane-0",
        task_id="T-FENCE",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "child-1",
            "run_id": "run-1",
            "status": "completed",
        },
    ))

    refresh_spine_projections(state_dir, log)

    entry = _read(tmp_path, "task_attempts.json")["tasks"]["T-FENCE"]
    assert entry["open_attempts"] == 1
    assert entry["attempts"][0]["state"] == "superseded"
    assert entry["attempts"][1]["attempt_key"] == "run-2"
    assert entry["attempts"][1]["terminal"] is None


def test_stage_rounds_and_status(tmp_path: Path) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    for fanout in ("fa", "fb"):
        log.append(ZfEvent(type="fanout.started",
                           payload={"stage_id": "avbs-impl", "fanout_id": fanout}))
    log.append(ZfEvent(type="review.rejected",
                       payload={"stage_id": "avbs-review", "status": "failed"}))
    refresh_spine_projections(state_dir, log)

    stages = _read(tmp_path, "stage_spine.json")
    assert stages["stages"]["avbs-impl"]["rounds"] == 2
    assert stages["stages"]["avbs-impl"]["last_fanout_id"] == "fb"
    assert stages["stages"]["avbs-review"]["last_status"] == "review.rejected"


def test_health_counters(tmp_path: Path) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    for _ in range(3):
        log.append(ZfEvent(type="human.escalate", payload={"reason": "cap"}))
    log.append(ZfEvent(type="runtime.watcher.lag_warning",
                       payload={"trigger_lag_s": 500}))
    refresh_spine_projections(state_dir, log)

    health = _read(tmp_path, "workflow_health.json")
    assert health["counters"]["human.escalate"] == 3
    assert health["counters"]["runtime.watcher.lag_warning"] == 1
    assert health["last_event_ts"]


def test_run_level_milestones(tmp_path: Path) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    log.append(ZfEvent(type="refactor.scan.ready", payload={"pdd_id": "PDD-9"}))
    log.append(ZfEvent(type="verify.passed", payload={"pdd_id": "PDD-9"}))
    log.append(ZfEvent(type="review.rejected", payload={"pdd_id": "PDD-9"}))
    refresh_spine_projections(state_dir, log)

    runs = _read(tmp_path, "workflow_spine.json")
    run = runs["runs"]["PDD-9"]
    assert run["milestones"] == 3
    assert run["last_milestone"] == "review.rejected"
    assert run["attention"] is True

    # attention 事件之后出现正向里程碑 → attention 回落
    log.append(ZfEvent(type="verify.passed", payload={"pdd_id": "PDD-9"}))
    refresh_spine_projections(state_dir, log)
    runs = _read(tmp_path, "workflow_spine.json")
    assert runs["runs"]["PDD-9"]["attention"] is False


def test_corrupt_projection_rebuilds_from_zero(tmp_path: Path) -> None:
    log = _log(tmp_path)
    state_dir = tmp_path / ".zf"
    log.append(ZfEvent(type="task.dispatched", task_id="T-1",
                       payload={"role": "dev-1"}))
    refresh_spine_projections(state_dir, log)
    # 损坏游标文件 → 下一轮全量重建一次,不崩
    (state_dir / "projections" / "task_attempts.json").write_text("{broken", encoding="utf-8")
    stats = refresh_spine_projections(state_dir, log)
    assert stats["events_folded"] == 1  # 从 0 重放
    attempts = _read(tmp_path, "task_attempts.json")
    assert attempts["tasks"]["T-1"]["attempt_count"] == 1
