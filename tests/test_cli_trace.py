"""Tests for zf trace show."""

from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def test_trace_show_outputs_correlation_trace(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    user = writer.append(ZfEvent(type="user.message", actor="human"))
    writer.append(ZfEvent(type="task.created", task_id="T1", causation_id=user.id))

    result = main(["trace", "show", user.correlation_id or ""])

    assert result == 0
    out = capsys.readouterr().out
    assert "user.message" in out
    assert "task.created" in out
    assert user.correlation_id in out


def test_trace_show_json_can_use_event_id(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    user = writer.append(ZfEvent(type="user.message", actor="human"))
    created = writer.append(ZfEvent(
        type="task.created",
        task_id="T1",
        causation_id=user.id,
    ))

    result = main(["trace", "show", created.id, "--format", "json"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "correlation"
    assert [event["type"] for event in data["events"]] == [
        "user.message",
        "task.created",
    ]


def test_trace_show_unknown_exits_nonzero(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()

    result = main(["trace", "show", "trace-missing"])

    assert result != 0


def test_trace_export_otlp_json_stdout_and_file(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="build api",
        status="in_progress",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.started",
        id="evt-fanout",
        payload={
            "feature_id": "F-1",
            "fanout_id": "FX",
            "stage_id": "impl",
            "expected_children": [{"child_id": "dev", "task_id": "T1"}],
        },
    ))
    log.append(ZfEvent(
        type="fanout.child.completed",
        id="evt-child",
        task_id="T1",
        payload={
            "feature_id": "F-1",
            "fanout_id": "FX",
            "child_id": "dev",
            "task_id": "T1",
        },
    ))
    canonical_before = {
        path.name: path.read_bytes()
        for path in (state_dir / "events.jsonl", state_dir / "kanban.json")
    }

    result = main(["trace", "export", "F-1", "--format", "otlp-json", "--state-dir", str(state_dir)])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    spans = data["resource_spans"][0]["scope_spans"][0]["spans"]
    assert spans
    assert spans[0]["attributes"]["zaofu.target_id"] == "F-1"

    output = tmp_path / "otlp.json"
    result = main([
        "trace", "export", "--target", "F-1", "--format", "otlp-json",
        "--output", str(output), "--state-dir", str(state_dir),
    ])

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["resource_spans"]
    assert {
        path.name: path.read_bytes()
        for path in (state_dir / "events.jsonl", state_dir / "kanban.json")
    } == canonical_before


def test_trace_export_completion_json_is_fail_closed_and_read_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    run_id = "run-completion-export"
    target = "a" * 40
    events = [
        ZfEvent(
            id="evt-run-start",
            type="run.goal.started",
            correlation_id=run_id,
            payload={"run_id": run_id, "goal_id": "GOAL-1"},
        ),
        ZfEvent(
            id="evt-closure",
            type="goal.closure.synthesized",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "goal_id": "GOAL-1",
                "admitted_call_result_ref": {
                    "ref": "artifacts/closure.json",
                    "sha256": "d" * 64,
                },
            },
        ),
        ZfEvent(
            id="evt-claim",
            type="run.goal.completion.claimed",
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "goal_id": "GOAL-1",
                "claim_id": "claim-1",
                "claim_type": "admitted_goal_closure_result",
                "task_map_generation": "generation-1",
                "target_commit": target,
            },
        ),
        ZfEvent(
            id="evt-candidate",
            type="candidate.ready",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "candidate_ref": "candidate/GOAL-1",
                "candidate_head_commit": target,
            },
        ),
        ZfEvent(
            id="evt-verify",
            type="fanout.child.completed",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "admitted_call_result_ref": {
                    "ref": "artifacts/verify.json",
                    "sha256": "b" * 64,
                },
            },
        ),
        ZfEvent(
            id="evt-completed",
            type="run.goal.completed",
            causation_id="evt-claim",
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "workflow_run_id": run_id,
                "goal_id": "GOAL-1",
                "claim_id": "claim-1",
                "source_event_id": "evt-closure",
                "task_map_generation": "generation-1",
                "target_commit": target,
                "verified_target_commit": target,
                "verification_event_id": "evt-verify",
                "verification_admitted_call_result_ref": {
                    "ref": "artifacts/verify.json",
                    "sha256": "b" * 64,
                },
                "candidate_event_id": "evt-candidate",
                "candidate_ref": "candidate/GOAL-1",
                "goal_claim_set_ref": "artifacts/claims.json",
                "goal_claim_set_digest": "c" * 64,
                "admitted_call_result_ref": {
                    "ref": "artifacts/closure.json",
                    "sha256": "d" * 64,
                },
                "delivery_policy": "report_only",
                "delivery_status": "not_required",
                "delivery_event_id": "",
            },
        ),
    ]
    for event in events:
        log.append(event)
    canonical_before = (state_dir / "events.jsonl").read_bytes()

    missing_run = main([
        "trace",
        "export",
        "--format",
        "completion-json",
        "--state-dir",
        str(state_dir),
    ])
    assert missing_run == 2
    assert "requires an explicit --run-id" in capsys.readouterr().err

    result = main([
        "trace",
        "export",
        "--format",
        "completion-json",
        "--run-id",
        run_id,
        "--state-dir",
        str(state_dir),
    ])

    assert result == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["schema_version"] == "goal-completion-receipt.v1"
    assert receipt["terminal"]["event_id"] == "evt-completed"
    assert receipt["completion_gate"]["verified_target_commit"] == target

    output = tmp_path / "completion.json"
    result = main([
        "trace",
        "export",
        "--format",
        "completion-json",
        "--run-id",
        run_id,
        "--output",
        str(output),
        "--state-dir",
        str(state_dir),
    ])
    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["degraded"] is False
    assert (state_dir / "events.jsonl").read_bytes() == canonical_before


def test_trace_export_completion_json_rejects_non_terminal_run(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="run.goal.started",
        correlation_id="run-active",
        payload={"run_id": "run-active", "goal_id": "GOAL-ACTIVE"},
    ))

    result = main([
        "trace",
        "export",
        "--format",
        "completion-json",
        "--run-id",
        "run-active",
        "--state-dir",
        str(state_dir),
    ])

    assert result == 3
    assert "completion_not_admitted" in capsys.readouterr().err
def test_trace_delivery_summary_is_bounded_and_full_is_explicit(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="build api",
        status="in_progress",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="worker.progress",
        task_id="T1",
        payload={"large": "x" * 10000},
    ))

    assert main([
        "trace", "delivery", "F-1", "--summary",
        "--state-dir", str(state_dir),
    ]) == 0
    summary_text = capsys.readouterr().out
    summary = json.loads(summary_text)
    assert summary["schema_version"] == "delivery-trace-summary.v1"
    assert summary["tasks"]["task_count"] == 1
    assert "events" not in summary
    assert len(summary_text) < 3000

    assert main([
        "trace", "delivery", "F-1", "--full",
        "--state-dir", str(state_dir),
    ]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full["schema_version"] == "delivery-trace.v1"
