from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.full_stack_validation import (
    build_preflight_report,
    build_scorecard,
    main,
)
from zf.core.events.log import EventLog


def _write_events(state_dir: Path, events: list[dict]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, event in enumerate(events):
        row = {
            "id": event.get("id", f"evt-{index}"),
            "type": event["type"],
            "actor": event.get("actor", "zf-cli"),
            "payload": event.get("payload", {}),
        }
        if "task_id" in event:
            row["task_id"] = event["task_id"]
        if "causation_id" in event:
            row["causation_id"] = event["causation_id"]
        rows.append(json.dumps(row))
    (state_dir / "events.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_split_events(state_dir: Path, archive_events: list[dict], active_events: list[dict]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    events_dir = state_dir / "events"
    events_dir.mkdir()
    rows: list[str] = []
    for index, event in enumerate(archive_events):
        rows.append(json.dumps(_event_row(event, index)))
    (events_dir / "2026-06-21-0001.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    rows = []
    for index, event in enumerate(active_events, start=len(archive_events)):
        rows.append(json.dumps(_event_row(event, index)))
    (state_dir / "events.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _event_row(event: dict, index: int) -> dict:
    row = {
        "id": event.get("id", f"evt-{index}"),
        "type": event["type"],
        "actor": event.get("actor", "zf-cli"),
        "payload": event.get("payload", {}),
    }
    if "task_id" in event:
        row["task_id"] = event["task_id"]
    if "causation_id" in event:
        row["causation_id"] = event["causation_id"]
    return row


def _manifest(state_dir: Path, fanout_id: str, *, status: str = "completed") -> None:
    path = state_dir / "fanouts" / fanout_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fanout_id": fanout_id,
        "children": [{"child_id": "child-1", "status": "completed"}],
        "aggregate": {"status": status},
    }), encoding="utf-8")


def _passing_events(*, fanout_actor: str = "orchestrator") -> list[dict]:
    fanout = "fo-1"
    return [
        {"type": "task.created", "id": "issue-task", "payload": {"source_kind": "issue"}},
        {"type": "product.plan.ready"},
        {"type": "task_map.ready"},
        {"type": "product_delivery.task_map.accepted"},
        {"type": "product_delivery.wave.ready"},
        {"type": "zaofu.refactor.review.ready"},
        {"type": "zaofu.refactor.plan.ready"},
        {"type": "web.action.requested"},
        {"type": "kanban.agent.turn.started"},
        {"type": "kanban.agent.turn.completed"},
        {"type": "channel.message.posted"},
        {"type": "channel.agent.reply.completed"},
        {
            "type": "workflow.invoke.requested",
            "id": "wf-kanban",
            "payload": {"entrypoint": "kanban-agent"},
        },
        {
            "type": "workflow.invoke.requested",
            "id": "wf-channel",
            "payload": {"entrypoint": "channel"},
        },
        {
            "type": "fanout.started",
            "id": "fanout-start",
            "actor": fanout_actor,
            "payload": {"fanout_id": fanout},
            "causation_id": "wf-kanban",
        },
        {
            "type": "fanout.child.dispatched",
            "id": "fanout-dispatch",
            "actor": fanout_actor,
            "payload": {"fanout_id": fanout, "child_id": "child-1"},
            "causation_id": "fanout-start",
        },
        {
            "type": "fanout.child.completed",
            "id": "fanout-child-done",
            "actor": "review-a",
            "payload": {"fanout_id": fanout, "child_id": "child-1"},
        },
        {
            "type": "fanout.aggregate.completed",
            "id": "fanout-aggregate",
            "actor": fanout_actor,
            "payload": {"fanout_id": fanout},
            "causation_id": "fanout-child-done",
        },
        {"type": "codex.hook.session_start", "actor": "dev-1"},
        {"type": "agent.usage", "actor": "dev-1", "payload": {"backend": "codex", "usage": {"input_tokens": 10}}},
    ]


def test_scorecard_blocks_empty_state(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    card = build_scorecard(state_dir, require_real_codex=True)
    assert card["status"] == "blocked"
    assert "issue:task.created from zf issue ingest" in card["missing_required_events"]
    assert "codex:codex.hook.*" in card["missing_required_events"]


def test_scorecard_rejects_synthetic_fanout_runtime_events(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_events(state_dir, _passing_events(fanout_actor="e2e"))
    _manifest(state_dir, "fo-1")
    card = build_scorecard(state_dir, require_real_codex=True)
    gate = card["hard_gates"]["fanout_trace_chain"]
    assert card["status"] == "blocked"
    assert gate["passed"] is False
    assert any("synthetic_runtime_events" in item for item in gate["blocked_reasons"])


def test_scorecard_passes_realish_matrix_with_codex_and_fanout_manifest(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_events(state_dir, _passing_events())
    _manifest(state_dir, "fo-1")
    card = build_scorecard(state_dir, require_real_codex=True)
    assert card["status"] == "passed"
    assert card["passed"] is True
    assert card["hard_gates"]["real_codex_observability"]["hook_count"] == 1
    assert card["hard_gates"]["fanout_trace_chain"]["accepted_fanout_count"] == 1


def test_scorecard_reads_archived_and_active_event_truth(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    events = _passing_events()
    _write_split_events(state_dir, events[:8], events[8:])
    _manifest(state_dir, "fo-1")

    card = build_scorecard(state_dir, require_real_codex=True)

    assert card["status"] == "passed"
    assert card["event_count"] == len(EventLog(state_dir / "events.jsonl").read_all())
    assert card["event_sources"]["archive_count"] == 8
    assert card["event_sources"]["active_count"] == len(events) - 8
    assert card["event_sources"]["total_count"] == len(events)


def test_preflight_report_is_machine_readable_without_external_requirements(tmp_path: Path) -> None:
    (tmp_path / "zf.yaml").write_text("version: '1.0'\n", encoding="utf-8")
    report = build_preflight_report(
        repo_root=tmp_path,
        require_real_codex=False,
        require_docker=False,
    )
    assert report["schema_version"] == "zaofu.full_stack_preflight.v1"
    assert report["checks"]["zf_yaml"]["ok"] is True
    assert report["checks"]["docker"]["required"] is False


def test_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    (tmp_path / "zf.yaml").write_text("version: '1.0'\n", encoding="utf-8")
    _write_events(state_dir, _passing_events())
    _manifest(state_dir, "fo-1")
    score = tmp_path / "scorecard.json"
    markdown = tmp_path / "report.md"
    rc = main([
        "--state-dir", str(state_dir),
        "--repo-root", str(tmp_path),
        "--output", str(score),
        "--markdown", str(markdown),
    ])
    assert rc == 0
    assert json.loads(score.read_text(encoding="utf-8"))["status"] == "passed"
    assert "ZaoFu Full-stack Validation" in markdown.read_text(encoding="utf-8")
