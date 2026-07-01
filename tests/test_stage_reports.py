from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.stage_reports import build_stage_report


def _state(tmp_path: Path) -> tuple[Path, TaskStore, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return (
        state_dir,
        TaskStore(state_dir / "kanban.json"),
        EventWriter(EventLog(state_dir / "events.jsonl")),
    )


def _read_report(state_dir: Path, stage: str) -> dict:
    path = state_dir / "artifacts" / "stage-reports" / f"{stage}-report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_stage_reports_project_scan_plan_and_task_map_artifacts(
    tmp_path: Path,
) -> None:
    state_dir, store, writer = _state(tmp_path)
    store.add(Task(id="CJMIN-SCAN-001", title="scan", status="in_progress"))

    writer.append(ZfEvent(
        type="zaofu.refactor.review.ready",
        actor="scan",
        task_id="CJMIN-SCAN-001",
        payload={
            "prompt_ref": "artifacts/scan/prompt.md",
            "artifact_refs": ["artifacts/scan/source-index.json"],
            "skills": ["repo-scan"],
            "constraints": ["read-only"],
            "scope": ["src/**"],
            "modules": ["gateway", "provider"],
        },
    ))
    writer.append(ZfEvent(
        type="zaofu.refactor.plan.ready",
        actor="plan",
        task_id="CJMIN-SCAN-001",
        payload={"artifact_ref": "artifacts/plan/plan-report.md"},
    ))
    writer.append(ZfEvent(
        type="product_delivery.task_map.accepted",
        actor="plan",
        task_id="CJMIN-SCAN-001",
        payload={"task_map_ref": "artifacts/plan/task-map.json"},
    ))

    scan = _read_report(state_dir, "scan")
    plan = _read_report(state_dir, "plan")
    task_map = _read_report(state_dir, "task-map")
    assert scan["stage"] == "scan"
    assert scan["stage_inputs"]["prompt_refs"] == ["artifacts/scan/prompt.md"]
    assert scan["stage_inputs"]["modules"] == ["gateway", "provider"]
    assert scan["stage_inputs"]["skills"] == ["repo-scan"]
    assert "artifacts/scan/source-index.json" in scan["artifact_refs"]
    assert (
        state_dir / "artifacts" / "stage-reports" / "scan-report.md"
    ).exists()
    assert plan["summary"]["next_action"] == "proceed"
    assert "artifacts/plan/plan-report.md" in plan["artifact_refs"]
    assert "artifacts/plan/task-map.json" in task_map["artifact_refs"]


def test_impl_report_lists_failed_fanout_child_and_next_action(
    tmp_path: Path,
) -> None:
    state_dir, store, writer = _state(tmp_path)
    store.add(Task(id="CJMIN-GATEWAY-001", title="gateway", status="in_progress"))

    writer.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-1",
            "child_id": "dev-0",
            "task_id": "CJMIN-GATEWAY-001",
            "artifact_ref": "artifacts/impl/gateway.md",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-1",
            "child_id": "dev-1",
            "task_id": "CJMIN-GATEWAY-001",
            "attempt": 2,
            "log_ref": "logs/dev-1.txt",
            "reason": "timeout",
        },
    ))

    report = _read_report(state_dir, "impl")
    assert report["summary"]["next_action"] == "needs_operator"
    assert report["fanout"]["completed"] == 1
    assert report["fanout"]["failed"] == 1
    assert report["fanout"]["without_output"][0]["child_id"] == "dev-1"
    assert report["fanout"]["without_output"][0]["attempt"] == "2"
    assert report["fanout"]["without_output"][0]["log_ref"] == "logs/dev-1.txt"
    assert "artifacts/impl/gateway.md" in report["artifact_refs"]


def test_review_and_verify_reports_include_rework_target(tmp_path: Path) -> None:
    state_dir, store, writer = _state(tmp_path)
    store.add(Task(id="CJMIN-PROVIDER-001", title="provider", status="review"))

    writer.append(ZfEvent(
        type="review.rejected",
        actor="review-0",
        task_id="CJMIN-PROVIDER-001",
        payload={"reason": "missing llm function-call regression"},
    ))
    writer.append(ZfEvent(
        type="verify.failed",
        actor="verify-0",
        task_id="CJMIN-PROVIDER-001",
        payload={"reason": "pnpm test failed"},
    ))

    review = _read_report(state_dir, "review")
    verify = _read_report(state_dir, "verify")
    assert review["summary"]["next_action"] == "rework"
    assert review["rework"]["items"][0]["rework_target"] == "impl"
    assert "missing llm function-call regression" in (
        review["rework"]["items"][0]["reason"]
    )
    assert verify["summary"]["next_action"] == "rework"
    assert verify["rework"]["items"][0]["rework_target"] == "impl"
    assert verify["gaps"][0]["reason"] == "pnpm test failed"


def test_stage_report_rebuild_is_stable_for_same_event_stream(
    tmp_path: Path,
) -> None:
    state_dir, store, writer = _state(tmp_path)
    store.add(Task(id="CJMIN-STABLE-001", title="stable", status="in_progress"))
    event = writer.append(ZfEvent(
        type="dev.build.done",
        actor="dev-0",
        task_id="CJMIN-STABLE-001",
        payload={"artifact_refs": ["artifacts/impl/stable.md"]},
    ))
    events = EventLog(state_dir / "events.jsonl").read_all()
    tasks = store.list_all()

    first = build_stage_report(
        state_dir,
        "impl",
        trigger_event=event,
        events=events,
        tasks=tasks,
    )
    second = build_stage_report(
        state_dir,
        "impl",
        trigger_event=event,
        events=events,
        tasks=tasks,
    )

    assert first == second


def test_web_api_reads_latest_stage_report(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    state_dir, store, writer = _state(tmp_path)
    store.add(Task(id="CJMIN-WEB-001", title="web", status="testing"))
    writer.append(ZfEvent(
        type="verify.failed",
        actor="verify-0",
        task_id="CJMIN-WEB-001",
        payload={"reason": "quality gate failed"},
    ))

    client = TestClient(create_app(state_dir))
    response = client.get("/api/stage-reports/latest")
    data = response.json()

    assert response.status_code == 200
    assert data["latest"]["stage"] == "verify"
    assert data["report"]["stage"] == "verify"
    assert data["report"]["summary"]["next_action"] == "rework"
