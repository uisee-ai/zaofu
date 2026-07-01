from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.fanout import (
    FanoutChild,
    FanoutContext,
    FanoutManifestProjector,
    reconcile_fanout_manifest_terminal_state,
)
from zf.web.server import create_app


def test_fanout_context_creates_stable_ids():
    first = FanoutContext.create(
        stage_id="review-candidate",
        topology="fanout_reader",
        trace_id="trace-1",
        trigger_event_id="evt-abcdef123456",
        target_ref="candidate/F-1",
        role_instances=["review", "review"],
    )
    second = FanoutContext.create(
        stage_id="review-candidate",
        topology="fanout_reader",
        trace_id="trace-1",
        trigger_event_id="evt-abcdef123456",
        target_ref="candidate/F-1",
        role_instances=["review", "review"],
    )

    assert first.fanout_id == "fanout-review-candidate-evt-abcdef12"
    assert [child.child_id for child in first.expected_children] == [
        "review",
        "review-2",
    ]
    assert first == second


def test_event_writer_rebuilds_fanout_manifest(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    context = FanoutContext.create(
        stage_id="review-candidate",
        topology="fanout_reader",
        trace_id="trace-1",
        trigger_event_id="evt-1",
        target_ref="candidate/F-1",
        role_instances=["review-security", "review-arch"],
    )

    started = context.started_event()
    started.payload["pdd_id"] = "F-1"
    started.payload["feature_id"] = "F-1"
    started.payload["task_map_ref"] = ".zf/artifacts/F-1/task-map.json"
    writer.append(started)
    writer.append(context.child_dispatched_event(
        context.expected_children[0],
        run_id="run-review-security-1",
    ))
    writer.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": context.fanout_id,
            "trace_id": "trace-1",
            "stage_id": "review-candidate",
            "child_id": "review-security",
            "run_id": "run-review-security-1",
            "status": "completed",
            "result_event_id": "evt-result",
        },
    ))
    writer.append(context.aggregate_started_event(mode="wait_for_all"))
    writer.append(ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": context.fanout_id,
            "trace_id": "trace-1",
            "stage_id": "review-candidate",
            "status": "completed",
            "success_event": "review.complete",
        },
    ))

    manifest = json.loads(
        (
            state_dir
            / "fanouts"
            / context.fanout_id
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["trace_id"] == "trace-1"
    assert manifest["stage_id"] == "review-candidate"
    assert manifest["pdd_id"] == "F-1"
    assert manifest["feature_id"] == "F-1"
    assert manifest["task_map_ref"] == ".zf/artifacts/F-1/task-map.json"
    assert manifest["children"][0]["child_id"] == "review-arch"
    assert manifest["children"][0]["status"] == "pending"
    assert manifest["children"][1]["child_id"] == "review-security"
    assert manifest["children"][1]["status"] == "completed"
    assert manifest["aggregate"]["status"] == "completed"
    assert manifest["barrier"]["status"] == "completed"
    assert manifest["barrier"]["required_children"] == [
        "review-arch",
        "review-security",
    ]
    assert manifest["barrier"]["completed_children"] == ["review-security"]
    assert {event.correlation_id for event in log.read_all()} == {"trace-1"}


def test_fanout_manifest_rebuild_ignores_unknown_payload_fields(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    events = [
        ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-1",
                "stage_id": "review",
                "topology": "fanout_reader",
                "target_ref": "candidate/F-1",
                "expected_children": [{
                    "child_id": "review",
                    "role_instance": "review",
                    "expected_output": "security report",
                    "owner_claim": "security",
                }],
                "unexpected": {"nested": ["ok"]},
            },
        ),
        ZfEvent(
            type="fanout.child.failed",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-1",
                "child_id": "review",
                "reason": "failed",
                "unknown": object(),
            },
        ),
    ]

    manifest = FanoutManifestProjector(state_dir).write_manifest("fanout-1", events)

    assert manifest["children"][0]["status"] == "failed"
    assert manifest["children"][0]["reason"] == "failed"
    assert manifest["children"][0]["expected_output"] == "security report"
    assert manifest["children"][0]["owner_claim"] == "security"
    assert manifest["barrier"]["failed_children"] == ["review"]


def test_fanout_manifest_marks_corrected_failure_non_blocking() -> None:
    manifest = {
        "fanout_id": "fanout-final-judge",
        "stage_id": "final-judge",
        "status": "failed",
        "aggregate": {
            "status": "failed",
            "failure_event": "evt-failed",
        },
    }
    events = [
        ZfEvent(
            id="evt-failed",
            type="judge.failed",
            payload={"reason": "stale manifest"},
        ),
        ZfEvent(
            id="evt-pass",
            type="judge.passed",
            payload={"correction_of": "evt-failed"},
        ),
    ]

    reconciled = reconcile_fanout_manifest_terminal_state(manifest, events)

    assert reconciled["status"] == "corrected_passed"
    assert reconciled["non_blocking"] is True
    assert reconciled["reconciled_by"] == "corrected_terminal"
    assert reconciled["aggregate"]["corrected_by_event_id"] == "evt-pass"


def test_fanout_manifest_closes_pending_after_terminal_run_completed() -> None:
    manifest = {
        "fanout_id": "fanout-reader",
        "stage_id": "reader",
        "status": "started",
        "aggregate": {"status": "pending"},
    }
    events = [
        ZfEvent(
            id="evt-run-completed",
            type="run.completed",
            payload={"status": "passed"},
        ),
    ]

    reconciled = reconcile_fanout_manifest_terminal_state(manifest, events)

    assert reconciled["status"] == "closed"
    assert reconciled["aggregate"]["status"] == "closed"
    assert reconciled["non_blocking"] is True
    assert reconciled["reconciled_by"] == "run.completed"


def test_fanout_manifest_projects_affinity_stage_slot_queue(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    context = FanoutContext(
        fanout_id="fanout-dev-evt-1",
        stage_id="dev-fanout",
        topology="fanout_writer_scoped",
        trace_id="trace-1",
        trigger_event_id="evt-1",
        target_ref="main",
        expected_children=[
            FanoutChild(
                child_id="queued-TASK-3",
                role_instance="",
                target_ref="main",
                payload={
                    "task_id": "TASK-3",
                    "assignment_strategy": "affinity_stage_slots",
                    "lane_profile": "refactor-2",
                    "stage_slot": "impl",
                    "affinity_tag": "web-tui",
                },
            ),
        ],
    )

    writer.append(context.started_event())
    writer.append(ZfEvent(
        type="fanout.child.queued",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": context.fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": "queued-TASK-3",
            "task_id": "TASK-3",
            "assignment_strategy": "affinity_stage_slots",
            "lane_profile": "refactor-2",
            "stage_slot": "impl",
            "affinity_tag": "web-tui",
            "queue_order": 0,
        },
    ))
    writer.append(ZfEvent(
        type="fanout.slot.released",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": context.fanout_id,
            "child_id": "dev-2-TASK-2",
            "role_instance": "dev-2",
            "lane_id": "lane1",
            "stage_slot": "impl",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.slot.assigned",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": context.fanout_id,
            "child_id": "queued-TASK-3",
            "role_instance": "dev-2",
            "lane_id": "lane1",
            "stage_slot": "impl",
        },
    ))

    manifest = json.loads(
        (
            state_dir
            / "fanouts"
            / context.fanout_id
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )

    child = manifest["children"][0]
    assert child["status"] == "queued"
    assert child["assignment_strategy"] == "affinity_stage_slots"
    assert child["affinity_tag"] == "web-tui"
    assert child["queue_order"] == "0"
    assert manifest["planned_children"] == ["queued-TASK-3"]
    assert manifest["queued_children"] == ["queued-TASK-3"]
    assert manifest["dispatched_children"] == []
    assert manifest["terminal_children"] == []
    assert manifest["slot_state"] == [{
        "status": "assigned",
        "lane_id": "lane1",
        "stage_slot": "impl",
        "child_id": "queued-TASK-3",
        "role_instance": "dev-2",
        "event_id": manifest["slot_events"][1]["event_id"],
    }]
    assert [event["status"] for event in manifest["slot_events"]] == [
        "released",
        "assigned",
    ]


def test_web_fanout_detail_prefers_manifest(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="linked task",
        status="in_progress",
        assigned_to="review",
        contract=TaskContract(feature_id="F-1"),
    ))
    EventWriter(EventLog(state_dir / "events.jsonl")).append(ZfEvent(
        type="fanout.child.dispatched",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "review",
            "run_id": "run-fanout-1-review",
            "workdir": "/tmp/workdir",
            "source_branch": "worker/review",
        },
    ))
    manifest_dir = state_dir / "fanouts" / "fanout-1"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({
        "fanout_id": "fanout-1",
        "stage_id": "review",
        "topology": "fanout_reader",
        "target_ref": "candidate/F-1",
        "status": "completed",
        "children": [
            {
                "child_id": "review",
                "role_instance": "review",
                "status": "completed",
                "task_id": "TASK-1",
                "run_id": "run-fanout-1-review",
                "workdir": "/tmp/workdir",
                "source_branch": "worker/review",
                "report": {
                    "child_id": "review",
                    "status": "passed",
                    "summary": "No blockers.",
                    "findings": [],
                    "recommendation": "approve",
                },
            },
        ],
        "aggregate": {"status": "completed"},
        "synth": {"status": "completed", "recommendation": "approve"},
    }), encoding="utf-8")
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(
        create_app(state_dir)
    )

    detail = client.get("/api/fanouts/fanout-1").json()
    snapshot = client.get("/api/snapshot").json()

    assert detail["topology"] == "fanout_reader"
    assert detail["children"][0]["child_id"] == "review"
    assert detail["children"][0]["linked_task"]["task_status"] == "in_progress"
    assert detail["children"][0]["workdir"] == "/tmp/workdir"
    assert detail["children"][0]["report"]["recommendation"] == "approve"
    assert detail["aggregate"]["status"] == "completed"
    assert detail["synth"]["recommendation"] == "approve"
    assert snapshot["fanouts"][0]["fanout_id"] == "fanout-1"
    task = next(item for item in snapshot["tasks"] if item["id"] == "TASK-1")
    assert task["links"]["fanout"] == "fanout-1"
    assert task["links"]["fanout_child"] == "review"
    assert task["fanout"]["workdir"] == "/tmp/workdir"
