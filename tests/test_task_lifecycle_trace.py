"""task-lifecycle.v1 — state trajectory + tries (S-A)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.task_lifecycle_trace import build_task_lifecycle

T0 = datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _timeline() -> list[ZfEvent]:
    return [
        ZfEvent(type="task.created", actor="zf-cli", task_id="T1", ts=_ts(0)),
        ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(9)),
        ZfEvent(type="worker.heartbeat", actor="dev-lane-0", task_id="T1", ts=_ts(9.5)),
        ZfEvent(type="dev.build.done", actor="dev-lane-0", task_id="T1", ts=_ts(40)),
        ZfEvent(type="verify.failed", actor="zf-cli", task_id="T1", ts=_ts(45)),
        ZfEvent(
            type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(50),
            payload={"rework_kind": "workflow_stage_backedge"},
        ),
        ZfEvent(type="dev.build.done", actor="dev-lane-0", task_id="T1", ts=_ts(70)),
        ZfEvent(type="verify.passed", actor="zf-cli", task_id="T1", ts=_ts(75)),
        ZfEvent(type="task.done", actor="zf-cli", task_id="T1", ts=_ts(76)),
    ]


def test_state_history_order_and_dwell():
    history = build_task_lifecycle(_timeline())["tasks"]["T1"]["state_history"]
    states = [row["state"] for row in history]
    assert states == [
        "backlog", "queued", "running", "verify", "failed",
        "queued", "running", "verify", "done",
    ]
    assert history[0]["dwell_seconds"] == 9 * 60  # backlog -> first dispatch
    assert history[-1]["dwell_seconds"] is None  # terminal state open-ended


def test_tries_split_with_first_response_and_outcomes():
    tries = build_task_lifecycle(_timeline())["tasks"]["T1"]["tries"]
    assert len(tries) == 2
    assert tries[0]["first_response_seconds"] == 30
    assert tries[0]["outcome"] == "failed"
    assert tries[0]["rework_kind"] is None
    assert tries[1]["rework_kind"] == "workflow_stage_backedge"
    assert tries[1]["outcome"] == "done"
    gate_types = [g["type"] for g in tries[1]["gate_results"]]
    assert "verify.passed" in gate_types


def test_blocked_and_requeue_states():
    events = [
        ZfEvent(type="task.created", actor="zf-cli", task_id="T2", ts=_ts(0)),
        ZfEvent(type="task.blocked", actor="zf-cli", task_id="T2", ts=_ts(2)),
        ZfEvent(type="task.requeued", actor="zf-cli", task_id="T2", ts=_ts(8)),
    ]
    history = build_task_lifecycle(events)["tasks"]["T2"]["state_history"]
    assert [row["state"] for row in history] == ["backlog", "blocked", "ready"]
    assert history[1]["dwell_seconds"] == 6 * 60


def test_trace_anchors_on_tries():
    events = list(enumerate([
        ZfEvent(type="task.created", actor="zf-cli", task_id="T1", ts=_ts(0)),
        ZfEvent(
            type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(9),
            payload={"dispatch_id": "d-abc123", "briefing": ".zf/briefings/dev-T1.md",
                     "snapshot_ref": ".zf/snapshots/s1.json"},
        ),
        ZfEvent(type="codex.hook.post_tool_use", actor="dev-lane-0", task_id="T1", ts=_ts(10)),
        ZfEvent(type="codex.hook.post_tool_use", actor="dev-lane-0", task_id="T1", ts=_ts(11)),
        ZfEvent(
            type="agent.usage", actor="dev-lane-0", task_id="T1", ts=_ts(12),
            payload={"usage": {"input_tokens": 1000, "output_tokens": 50}},
        ),
        ZfEvent(
            type="verify.failed", actor="zf-cli", task_id="T1", ts=_ts(45),
            payload={"reason": "FunctionalD: 3 tests red", "tier": "functional"},
        ),
    ]))
    t = build_task_lifecycle(events)["tasks"]["T1"]["tries"][0]
    assert t["dispatch_id"] == "d-abc123"
    assert t["briefing_ref"] == ".zf/briefings/dev-T1.md"
    assert t["snapshot_ref"] == ".zf/snapshots/s1.json"
    assert t["tool_calls"] == 2
    assert t["tokens_in"] == 1000 and t["tokens_out"] == 50
    assert t["seq_first"] == 1 and t["seq_last"] == 5
    gate = t["gate_results"][0]
    assert gate["detail"]["reason"].startswith("FunctionalD")
