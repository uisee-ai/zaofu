from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.config.loader import load_config
from zf.core.config.project_context import ProjectContext
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.workspace.registry import WorkspaceRegistry
from zf.web.projections.trace_pages import trace_detail_page, trace_list_page
from zf.web.projections.trace_spans import trace_span_page
from zf.web.server import create_app
from zf.web.trace_routes import build_trace_router


def _state_dir(root: Path) -> Path:
    state_dir = root / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
    return state_dir


def _client(state_dir: Path) -> tuple[TestClient, str]:
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))
    workspace = client.get("/api/workspace/projects").json()
    return client, str(workspace["server_default_project_id"])


def _append(
    state_dir: Path,
    *,
    event_id: str,
    trace_id: str = "",
    task_id: str = "",
    event_type: str = "worker.progress",
    actor: str = "dev-1",
    payload: dict | None = None,
    ts: str = "",
    causation_id: str = "",
    origin: str = "",
) -> None:
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type=event_type,
        id=event_id,
        ts=ts or datetime.now(timezone.utc).isoformat(),
        actor=actor,
        task_id=task_id or None,
        causation_id=causation_id or None,
        correlation_id=trace_id or None,
        payload=payload or {},
        origin=origin,
    ))


def test_trace_v2_projection_contract_and_router_are_directly_wired(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    _append(state_dir, event_id="evt-direct", trace_id="trace-direct")

    listing = trace_list_page(state_dir)
    detail = trace_detail_page(state_dir, "trace-direct")
    router = build_trace_router(resolve_ctx=lambda _project_id: SimpleNamespace(
        state_dir=state_dir,
        config=None,
    ))

    assert listing["schema_version"] == "trace-list.v2"
    assert detail["schema_version"] == "trace-detail.v2"
    assert {route.path for route in router.routes} == {
        "/api/projects/{project_id}/traces",
        "/api/projects/{project_id}/traces/{trace_id}",
        "/api/projects/{project_id}/traces/{trace_id}/spans",
    }


def test_trace_v2_list_is_single_array_and_v1_shape_is_unchanged(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    _append(
        state_dir,
        event_id="evt-nested",
        task_id="TASK-NESTED",
        payload={
            "backend": "codex-headless",
            "nested": {"trace_id": "trace-nested"},
        },
    )
    _append(state_dir, event_id="evt-fallback", task_id="TASK-FALLBACK")
    client, project_id = _client(state_dir)

    legacy = client.get(f"/api/projects/{project_id}/traces")
    legacy_detail = client.get(
        f"/api/projects/{project_id}/traces/trace-nested"
    )
    page = client.get(
        f"/api/projects/{project_id}/traces",
        params={"contract": "v2", "limit": 50},
    )

    assert legacy.status_code == 200
    assert set(legacy.json()) == {
        "schema_version",
        "is_derived_projection",
        "items",
        "traces",
    }
    assert legacy.json()["items"] == legacy.json()["traces"]
    assert legacy_detail.status_code == 200
    assert set(legacy_detail.json()) == {
        "trace_id",
        "event_count",
        "timeline",
        "tasks",
        "actors",
        "git_refs",
        "diagnostics",
        "execution_route",
        "task_pipeline",
        "empty",
    }
    assert page.status_code == 200
    data = page.json()
    assert data["schema_version"] == "trace-list.v2"
    assert "traces" not in data
    assert data["has_more"] is False
    assert data["next_cursor"] is None
    assert {item["trace_id"] for item in data["items"]} == {
        "trace-nested",
        "task:TASK-FALLBACK",
    }
    nested = next(item for item in data["items"] if item["trace_id"] == "trace-nested")
    legacy_nested = next(
        item for item in legacy.json()["items"]
        if item["trace_id"] == "trace-nested"
    )
    assert nested["event_count"] == legacy_nested["event_count"] == 1
    assert nested["backends"] == ["codex-headless"]


def test_trace_v2_list_and_detail_share_exact_membership(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    _append(state_dir, event_id="evt-correlated", trace_id="trace-membership")
    _append(
        state_dir,
        event_id="evt-payload-trace",
        payload={"nested": {"trace_id": "trace-membership"}},
    )
    _append(
        state_dir,
        event_id="evt-mention",
        trace_id="trace-other",
        payload={"message": "trace-membership"},
    )
    _append(
        state_dir,
        event_id="trace-membership",
        task_id="TASK-NOT-MEMBER",
    )

    listing = trace_list_page(state_dir)
    detail = trace_detail_page(state_dir, "trace-membership")
    summary = next(
        item for item in listing["items"]
        if item["trace_id"] == "trace-membership"
    )

    assert listing["as_of_seq"] == detail["as_of_seq"]
    assert summary["event_count"] == detail["event_count"] == 2
    assert summary["first_seq"] == detail["first_seq"]
    assert summary["last_seq"] == detail["last_seq"]
    assert summary["status"] == detail["status"]
    assert [event["id"] for event in detail["timeline"]] == [
        "evt-correlated",
        "evt-payload-trace",
    ]


def test_trace_spans_pairs_only_allowlisted_lifecycle_events(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    started = datetime(2026, 8, 19, tzinfo=timezone.utc)
    common = {"trace_id": "trace-spans", "task_id": "TASK-SPANS"}
    _append(
        state_dir,
        event_id="agent-start",
        event_type="agent.session.run.started",
        payload={
            "run_id": "run-1",
            "thread_id": "thread-1",
            "source": "kanban",
            "backend": "codex-headless",
        },
        ts=started.isoformat(),
        **common,
    )
    _append(
        state_dir,
        event_id="agent-done",
        event_type="agent.session.run.completed",
        causation_id="agent-start",
        payload={"run_id": "run-1", "thread_id": "thread-1", "source": "kanban"},
        ts=(started + timedelta(seconds=2)).isoformat(),
        **common,
    )
    _append(
        state_dir,
        event_id="action-start",
        event_type="runtime.action.attempt.started",
        payload={"attempt_id": "act-1", "action": "approve", "surface": "web"},
        ts=(started + timedelta(seconds=3)).isoformat(),
        **common,
    )
    _append(
        state_dir,
        event_id="action-failed",
        event_type="runtime.action.attempt.failed",
        causation_id="action-start",
        payload={"attempt_id": "act-1", "action": "approve", "surface": "web"},
        ts=(started + timedelta(seconds=5)).isoformat(),
        **common,
    )
    task_identity = {
        "attempt_id": "attempt-1",
        "workflow_run_id": "workflow-1",
        "operation_id": "operation-1",
        "lease_id": "lease-1",
        "dispatch_id": "dispatch-1",
    }
    _append(
        state_dir,
        event_id="task-start",
        event_type="task.attempt.started",
        payload=task_identity,
        ts=(started + timedelta(seconds=6)).isoformat(),
        **common,
    )
    _append(
        state_dir,
        event_id="task-done",
        event_type="task.attempt.succeeded",
        causation_id="some-result-event",
        payload=task_identity,
        ts=(started + timedelta(seconds=9)).isoformat(),
        **common,
    )
    _append(
        state_dir,
        event_id="fake-span",
        event_type="worker.progress",
        payload={"span_id": "claimed-span", "parent_span_id": "claimed-parent"},
        ts=(started + timedelta(seconds=10)).isoformat(),
        **common,
    )
    client, project_id = _client(state_dir)

    response = client.get(
        f"/api/projects/{project_id}/traces/trace-spans/spans",
        params={"contract": "v1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "trace-spans.v1"
    assert data["span_count"] == 3
    assert data["coverage"] == {
        "status": "available",
        "reason": "Paired allowlisted ZaoFu lifecycle events.",
        "collector": "unobserved",
        "projection": "zaofu.lifecycle-pairs.v1",
        "ledger": "complete",
        "source": "events.jsonl",
        "observed_allowlisted_event_count": 6,
        "eligible_event_count": 6,
        "paired_span_count": 3,
        "degraded_span_count": 0,
        "unpaired_start_count": 0,
        "unpaired_terminal_count": 0,
        "malformed_event_count": 0,
        "untrusted_event_count": 0,
    }
    assert [span["kind"] for span in data["items"]] == [
        "agent.session.run",
        "runtime.action.attempt",
        "task.attempt",
    ]
    assert [span["status"] for span in data["items"]] == [
        "completed",
        "failed",
        "completed",
    ]
    assert [span["duration_seconds"] for span in data["items"]] == [2.0, 2.0, 3.0]
    assert all(span["parent_span_id"] is None for span in data["items"])
    assert all(span["truth_class"] == "paired_lifecycle" for span in data["items"])
    assert all(
        span["provenance"]["origin"] == "legacy_unattributed"
        for span in data["items"]
    )
    assert "claimed-span" not in response.text

    focused_id = data["items"][0]["span_id"]
    focused = client.get(
        f"/api/projects/{project_id}/traces/trace-spans/spans",
        params={"contract": "v1", "limit": 1, "focus_span_id": focused_id},
    ).json()
    assert len(focused["items"]) == 1
    assert focused["items"][0]["span_id"] != focused_id
    assert focused["focused_item"]["span_id"] == focused_id


def test_trace_spans_rejects_worker_or_external_lifecycle_claims(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    for origin in ("worker", "external"):
        start_id = f"{origin}-start"
        _append(
            state_dir,
            event_id=start_id,
            trace_id="trace-origin",
            event_type="runtime.action.attempt.started",
            payload={"attempt_id": f"{origin}-attempt"},
            origin=origin,
        )
        _append(
            state_dir,
            event_id=f"{origin}-done",
            trace_id="trace-origin",
            event_type="runtime.action.attempt.completed",
            causation_id=start_id,
            payload={"attempt_id": f"{origin}-attempt"},
            origin=origin,
        )
    _append(
        state_dir,
        event_id="kernel-start",
        trace_id="trace-origin",
        event_type="runtime.action.attempt.started",
        payload={"attempt_id": "kernel-attempt"},
        origin="kernel",
    )
    _append(
        state_dir,
        event_id="kernel-done",
        trace_id="trace-origin",
        event_type="runtime.action.attempt.completed",
        causation_id="kernel-start",
        payload={"attempt_id": "kernel-attempt"},
        origin="kernel",
    )

    page = trace_span_page(state_dir, "trace-origin")

    assert page["span_count"] == 1
    assert page["items"][0]["source_event_ids"] == ["kernel-start", "kernel-done"]
    assert page["items"][0]["provenance"]["origin"] == "kernel"
    assert page["coverage"]["observed_allowlisted_event_count"] == 6
    assert page["coverage"]["eligible_event_count"] == 2
    assert page["coverage"]["untrusted_event_count"] == 4
    assert page["coverage"]["status"] == "degraded"
    assert {item["code"] for item in page["diagnostics"]} == {
        "untrusted_lifecycle_origin"
    }


def test_trace_spans_degrades_recovered_and_does_not_invent_unpaired_timing(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    started = datetime(2026, 8, 19, tzinfo=timezone.utc)
    identity = {"attempt_id": "attempt-recovered", "workflow_run_id": "run-r"}
    _append(
        state_dir,
        event_id="recovered-start",
        trace_id="trace-degraded",
        event_type="task.attempt.started",
        payload={**identity, "recovered": True},
        ts=started.isoformat(),
    )
    _append(
        state_dir,
        event_id="recovered-done",
        trace_id="trace-degraded",
        event_type="task.attempt.succeeded",
        payload=identity,
        ts=(started + timedelta(seconds=4)).isoformat(),
    )
    _append(
        state_dir,
        event_id="unpaired-start",
        trace_id="trace-degraded",
        event_type="runtime.action.attempt.started",
        payload={"attempt_id": "act-unpaired"},
        ts=(started + timedelta(seconds=5)).isoformat(),
    )
    _append(
        state_dir,
        event_id="wrong-terminal",
        trace_id="trace-degraded",
        event_type="runtime.action.attempt.completed",
        causation_id="not-unpaired-start",
        payload={"attempt_id": "act-unpaired"},
        ts=(started + timedelta(seconds=6)).isoformat(),
    )

    page = trace_span_page(state_dir, "trace-degraded")

    assert page["span_count"] == 1
    span = page["items"][0]
    assert span["degraded"] is True
    assert span["degradation_reason"] == "recovered_start_has_no_original_timestamp"
    assert span["started_at"] is None
    assert span["duration_seconds"] is None
    assert page["coverage"]["status"] == "degraded"
    assert page["coverage"]["unpaired_start_count"] == 1
    assert page["coverage"]["unpaired_terminal_count"] == 1
    assert {item["code"] for item in page["diagnostics"]} == {
        "lifecycle_pair_mismatch",
        "start_without_terminal",
    }


def test_trace_spans_cursor_is_trace_bound_and_stable_across_appends(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    started = datetime(2026, 8, 19, tzinfo=timezone.utc)
    for index in range(3):
        start_id = f"action-start-{index}"
        _append(
            state_dir,
            event_id=start_id,
            trace_id="trace-paged-spans",
            event_type="runtime.action.attempt.started",
            payload={"attempt_id": f"act-{index}"},
            ts=(started + timedelta(seconds=index * 2)).isoformat(),
        )
        _append(
            state_dir,
            event_id=f"action-done-{index}",
            trace_id="trace-paged-spans",
            event_type="runtime.action.attempt.completed",
            causation_id=start_id,
            payload={"attempt_id": f"act-{index}"},
            ts=(started + timedelta(seconds=index * 2 + 1)).isoformat(),
        )
    client, project_id = _client(state_dir)
    path = f"/api/projects/{project_id}/traces/trace-paged-spans/spans"
    first = client.get(path, params={"limit": 1}).json()
    _append(
        state_dir,
        event_id="appended-start",
        trace_id="trace-paged-spans",
        event_type="runtime.action.attempt.started",
        payload={"attempt_id": "act-appended"},
    )
    _append(
        state_dir,
        event_id="appended-done",
        trace_id="trace-paged-spans",
        event_type="runtime.action.attempt.completed",
        causation_id="appended-start",
        payload={"attempt_id": "act-appended"},
    )
    second = client.get(path, params={"limit": 1, "cursor": first["next_cursor"]}).json()
    crossed = client.get(
        f"/api/projects/{project_id}/traces/another-trace/spans",
        params={"cursor": first["next_cursor"]},
    )

    assert first["span_count"] == second["span_count"] == 3
    assert first["as_of_seq"] == second["as_of_seq"]
    assert first["items"][0]["source_event_ids"] == ["action-start-2", "action-done-2"]
    assert second["items"][0]["source_event_ids"] == ["action-start-1", "action-done-1"]
    assert "appended-done" not in str((first, second))
    assert crossed.status_code == 400
    assert crossed.json()["detail"] == "trace cursor trace mismatch"


def test_trace_spans_response_is_hard_bounded_for_large_event_payloads(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    blob = "x" * 10_000
    for index in range(105):
        start_id = f"bounded-start-{index}"
        payload = {
            "attempt_id": f"bounded-attempt-{index}",
            "action": blob,
            "backend": blob,
            "unprojected_result": blob,
        }
        _append(
            state_dir,
            event_id=start_id,
            trace_id="trace-bounded-spans",
            task_id=blob,
            actor=blob,
            event_type="runtime.action.attempt.started",
            payload=payload,
        )
        _append(
            state_dir,
            event_id=f"bounded-done-{index}",
            trace_id="trace-bounded-spans",
            task_id=blob,
            actor=blob,
            event_type="runtime.action.attempt.completed",
            causation_id=start_id,
            payload=payload,
        )
    client, project_id = _client(state_dir)

    response = client.get(
        f"/api/projects/{project_id}/traces/trace-bounded-spans/spans",
        params={"limit": 1000},
    )

    assert response.status_code == 200
    assert response.json()["limit"] == 100
    assert len(response.json()["items"]) == 100
    assert response.json()["has_more"] is True
    assert blob not in response.text
    assert len(response.content) < 150 * 1024


def test_trace_v2_empty_payload_raw_and_mixed_timezone_duration_are_safe(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    _append(
        state_dir,
        event_id="evt-naive",
        trace_id="trace-timezone",
        ts="2026-08-19T08:00:00",
    )
    _append(
        state_dir,
        event_id="evt-aware",
        trace_id="trace-timezone",
        ts="2026-08-19T08:01:00Z",
    )
    client, project_id = _client(state_dir)

    listing = client.get(
        f"/api/projects/{project_id}/traces",
        params={"contract": "v2"},
    )
    detail = client.get(
        f"/api/projects/{project_id}/traces/trace-timezone",
        params={"contract": "v2"},
    )

    assert listing.status_code == detail.status_code == 200
    assert listing.json()["items"][0]["duration_seconds"] is None
    assert detail.json()["duration_seconds"] is None
    assert all(event["has_raw"] is True for event in detail.json()["timeline"])


def test_trace_v2_list_cursor_is_stable_across_appends(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    for index in range(5):
        _append(
            state_dir,
            event_id=f"evt-{index}",
            trace_id=f"trace-{index}",
            task_id=f"TASK-{index}",
        )
    client, project_id = _client(state_dir)
    path = f"/api/projects/{project_id}/traces"

    first = client.get(path, params={"contract": "v2", "limit": 2}).json()
    assert [item["trace_id"] for item in first["items"]] == ["trace-4", "trace-3"]
    assert first["has_more"] is True
    assert first["next_cursor"]

    _append(state_dir, event_id="evt-new", trace_id="trace-new", task_id="TASK-NEW")
    second = client.get(path, params={
        "contract": "v2",
        "limit": 2,
        "cursor": first["next_cursor"],
    }).json()
    third = client.get(path, params={
        "contract": "v2",
        "limit": 2,
        "cursor": second["next_cursor"],
    }).json()

    assert second["as_of_seq"] == first["as_of_seq"]
    assert [item["trace_id"] for item in second["items"]] == ["trace-2", "trace-1"]
    assert [item["trace_id"] for item in third["items"]] == ["trace-0"]
    assert third["has_more"] is False
    assert third["next_cursor"] is None
    seen = {
        item["trace_id"]
        for page in (first, second, third)
        for item in page["items"]
    }
    assert seen == {f"trace-{index}" for index in range(5)}
    assert "trace-new" not in seen


def test_trace_v2_rejects_invalid_cursor_and_contract(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    _append(state_dir, event_id="evt-1", trace_id="trace-1")
    client, project_id = _client(state_dir)
    path = f"/api/projects/{project_id}/traces"

    invalid = client.get(path, params={"contract": "v2", "cursor": "not-a-cursor"})
    unknown = client.get(path, params={"contract": "v3"})

    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "invalid trace cursor"
    assert unknown.status_code == 400


def test_trace_v2_detail_is_bounded_slim_and_cursor_stable(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    started = datetime(2026, 8, 19, tzinfo=timezone.utc)
    for index in range(205):
        _append(
            state_dir,
            event_id=f"evt-{index:03d}",
            trace_id="trace-large",
            task_id="TASK-LARGE",
            event_type="dev.build.done" if index == 204 else "worker.progress",
            payload={
                "message": f"event {index} " + ("x" * 1000),
                "span_id": f"span-{index}" if index % 40 == 0 else "",
            },
            ts=(started + timedelta(seconds=index)).isoformat(),
        )
    client, project_id = _client(state_dir)
    path = f"/api/projects/{project_id}/traces/trace-large"

    first_response = client.get(path, params={"contract": "v2", "limit": 80})
    first = first_response.json()
    assert first_response.status_code == 200
    assert first["schema_version"] == "trace-detail.v2"
    assert first["event_count"] == 205
    assert first["status"] == "completed"
    assert first["duration_seconds"] == 204
    assert len(first["timeline"]) == 80
    assert first["timeline"][0]["id"] == "evt-125"
    assert first["timeline"][-1]["id"] == "evt-204"
    assert first["truncated"] is True
    assert first["has_more"] is True
    assert first["execution_route"]["schema_version"] == (
        "execution-route-summary.v2"
    )
    assert set(first).isdisjoint({"git_refs", "diagnostics", "task_pipeline"})
    assert all("payload" not in event for event in first["timeline"])
    assert all(event["payload_slim"] is True for event in first["timeline"])
    assert len(first_response.content) < 150 * 1024

    _append(
        state_dir,
        event_id="evt-205",
        trace_id="trace-large",
        task_id="TASK-LARGE",
        event_type="worker.progress",
    )
    second = client.get(path, params={
        "contract": "v2",
        "limit": 80,
        "cursor": first["next_cursor"],
    }).json()
    third = client.get(path, params={
        "contract": "v2",
        "limit": 80,
        "cursor": second["next_cursor"],
    }).json()

    assert second["event_count"] == third["event_count"] == 205
    assert second["as_of_seq"] == third["as_of_seq"] == first["as_of_seq"]
    assert [event["id"] for event in second["timeline"]] == [
        f"evt-{index:03d}" for index in range(45, 125)
    ]
    assert [event["id"] for event in third["timeline"]] == [
        f"evt-{index:03d}" for index in range(45)
    ]
    assert third["has_more"] is False
    assert third["next_cursor"] is None
    ids = [
        event["id"]
        for page in (first, second, third)
        for event in page["timeline"]
    ]
    assert len(ids) == len(set(ids)) == 205
    assert "evt-205" not in ids


def test_trace_v2_detail_cursor_is_bound_to_trace(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    for index in range(3):
        _append(state_dir, event_id=f"evt-a-{index}", trace_id="trace-a")
    _append(state_dir, event_id="evt-b", trace_id="trace-b")
    client, project_id = _client(state_dir)
    base = f"/api/projects/{project_id}/traces"

    first = client.get(
        f"{base}/trace-a",
        params={"contract": "v2", "limit": 1},
    ).json()
    crossed = client.get(
        f"{base}/trace-b",
        params={"contract": "v2", "cursor": first["next_cursor"]},
    )

    assert crossed.status_code == 400
    assert crossed.json()["detail"] == "trace cursor trace mismatch"


def test_trace_v2_route_uses_each_actor_final_status(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    _append(
        state_dir,
        event_id="evt-dispatched",
        trace_id="trace-status",
        task_id="TASK-STATUS",
        event_type="task.dispatched",
        actor="dev-1",
    )
    _append(
        state_dir,
        event_id="evt-done",
        trace_id="trace-status",
        task_id="TASK-STATUS",
        event_type="dev.build.done",
        actor="dev-1",
    )
    client, project_id = _client(state_dir)

    response = client.get(
        f"/api/projects/{project_id}/traces/trace-status",
        params={"contract": "v2"},
    )

    assert response.status_code == 200
    route = response.json()["execution_route"]
    assert route["linear"][0]["stage"] == "dev"
    assert route["linear"][0]["status"] == "done"
    assert route["status"] == "observed"


def test_trace_v2_raw_event_is_loaded_by_event_id_and_redacted(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    _append(
        state_dir,
        event_id="evt-secret",
        trace_id="trace-secret",
        task_id="TASK-SECRET",
        event_type="dev.build.done",
        payload={"message": "TOKEN=secret-value", "blob": "y" * 5000},
    )
    client, project_id = _client(state_dir)

    detail = client.get(
        f"/api/projects/{project_id}/traces/trace-secret",
        params={"contract": "v2"},
    )
    raw = client.get(f"/api/projects/{project_id}/events/evt-secret")

    assert detail.status_code == raw.status_code == 200
    assert "secret-value" not in detail.text
    assert detail.json()["timeline"][0]["has_raw"] is True
    assert raw.json()["schema_version"] == "event-detail.v1"
    assert raw.json()["event_id"] == "evt-secret"
    assert raw.json()["event"]["id"] == "evt-secret"
    assert raw.json()["event"]["payload_slim"] is False
    assert "secret-value" not in raw.text
    assert "[REDACTED_SECRET]" in raw.text


def test_trace_v2_timeline_scalars_are_bounded_and_declared(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    blob = "x" * 200_000
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type=f"dev.build.done.{blob}",
        id=f"evt-wide-{blob}",
        ts=blob,
        actor=blob,
        task_id=f"TASK-{blob}",
        causation_id=f"cause-{blob}",
        correlation_id="trace-wide-scalars",
        payload={"span_id": blob, "parent_span_id": blob},
    ))
    client, project_id = _client(state_dir)

    response = client.get(
        f"/api/projects/{project_id}/traces/trace-wide-scalars",
        params={"contract": "v2", "limit": 1},
    )

    assert response.status_code == 200
    event = response.json()["timeline"][0]
    assert event["metadata_truncated"] is True
    assert set(event["truncated_fields"]) == {
            "id",
            "ts",
            "type",
        "actor",
        "task_id",
        "causation_id",
        "span_id",
        "parent_span_id",
    }
    assert all(
        len(event[field]) <= 120
        for field in event["truncated_fields"]
    )
    assert event["has_raw"] is False
    assert len(response.json()["first_ts"]) <= 120
    assert len(response.json()["last_ts"]) <= 120
    route_stage = response.json()["execution_route"]["linear"][0]
    assert len(route_stage["first_ts"]) <= 120
    assert len(route_stage["last_ts"]) <= 120
    assert len(response.content) < 150 * 1024


def test_trace_v2_oversized_trace_ids_use_resolvable_opaque_refs(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    blob = "t" * 500_000
    _append(
        state_dir,
        event_id="evt-wide-correlation",
        trace_id=blob,
        task_id="TASK-CORRELATION",
    )
    _append(
        state_dir,
        event_id="evt-wide-fallback",
        task_id=blob,
    )
    client, project_id = _client(state_dir)

    listing = client.get(
        f"/api/projects/{project_id}/traces",
        params={"contract": "v2", "limit": 50},
    )

    assert listing.status_code == 200
    assert len(listing.content) < 80 * 1024
    items = listing.json()["items"]
    assert len(items) == 2
    assert all(item["trace_id_opaque"] is True for item in items)
    assert all(
        item["trace_id"].startswith("trace-ref:sha256:")
        and len(item["trace_id"]) == len("trace-ref:sha256:") + 64
        for item in items
    )
    assert blob not in listing.text

    for item in items:
        detail = client.get(
            f"/api/projects/{project_id}/traces/{item['trace_id']}",
            params={"contract": "v2", "limit": 1},
        )
        assert detail.status_code == 200
        assert detail.json()["trace_id"] == item["trace_id"]
        assert detail.json()["trace_id_opaque"] is True
        assert detail.json()["event_count"] == 1
        assert len(detail.content) < 150 * 1024


def test_trace_v2_list_payload_stays_under_budget(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    for index in range(60):
        _append(
            state_dir,
            event_id=f"evt-budget-{index}",
            trace_id=f"trace-budget-{index:03d}-" + ("x" * 80),
            task_id=f"TASK-BUDGET-{index:03d}-" + ("y" * 80),
            actor=f"dev-{index:03d}-" + ("z" * 40),
        )
    client, project_id = _client(state_dir)

    response = client.get(
        f"/api/projects/{project_id}/traces",
        params={"contract": "v2", "limit": 50},
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 50
    assert response.json()["has_more"] is True
    assert len(response.content) < 80 * 1024


def test_trace_v2_adversarial_route_stays_bounded(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    started = datetime(2026, 8, 19, tzinfo=timezone.utc)
    for index in range(600):
        event_type = "arch.proposal.done" if index < 300 else "dev.build.done"
        if index == 333:
            event_type = "judge.passed"
        _append(
            state_dir,
            event_id=f"evt-route-{index:03d}",
            trace_id="trace-wide-route",
            task_id=f"TASK-{index:03d}",
            actor=f"actor-{index:03d}",
            event_type=event_type,
            payload={"backend": f"backend-{index:03d}"},
            ts=(started + timedelta(seconds=index)).isoformat(),
        )
    client, project_id = _client(state_dir)

    response = client.get(
        f"/api/projects/{project_id}/traces/trace-wide-route",
        params={"contract": "v2", "limit": 80},
    )

    assert response.status_code == 200
    detail = response.json()
    route = detail["execution_route"]
    assert route["metadata_truncated"] is True
    assert route["trace_event_count"] == route["source_event_count"] == 600
    assert [stage["stage"] for stage in route["linear"]] == [
        "plan",
        "dev",
        "gate",
    ]
    assert sum(stage["event_count"] for stage in route["linear"]) == 600
    assert all(len(stage["actors"]) <= 8 for stage in route["linear"])
    assert "dag" not in route
    assert detail["tasks_truncated"] is True
    assert detail["actors_truncated"] is True
    assert len(detail["tasks"]) == len(detail["actors"]) == 20
    assert len(response.content) < 150 * 1024

    listing = client.get(
        f"/api/projects/{project_id}/traces",
        params={"contract": "v2", "limit": 50},
    )
    item = listing.json()["items"][0]
    assert item["task_ids_truncated"] is True
    assert item["actors_truncated"] is True
    assert item["backends_truncated"] is True
    assert len(item["task_ids"]) == len(item["actors"]) == 4
    assert len(item["backends"]) == 4
    assert len(listing.content) < 80 * 1024


def _project(root: Path, name: str, event_id: str) -> ProjectContext:
    root.mkdir()
    state_dir = _state_dir(root)
    (root / "zf.yaml").write_text(
        f'version: "1.0"\nproject:\n  name: {name}\n  state_dir: .zf\n',
        encoding="utf-8",
    )
    _append(
        state_dir,
        event_id=event_id,
        trace_id="trace-shared",
        task_id=f"TASK-{name.upper()}",
    )
    return ProjectContext(
        project_root=root,
        config_path=root / "zf.yaml",
        config=load_config(root / "zf.yaml"),
        state_dir=state_dir,
    )


def test_trace_v2_project_isolation_and_cursor_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    project_a = _project(tmp_path / "repo-a", "repo-a", "evt-a")
    project_b = _project(tmp_path / "repo-b", "repo-b", "evt-b")
    _append(
        project_a.state_dir,
        event_id="evt-a-extra",
        trace_id="trace-a-extra",
        task_id="TASK-A-EXTRA",
    )
    registry_project_b = WorkspaceRegistry().upsert_context(project_b)
    client = TestClient(create_app(
        project_a.state_dir,
        config=project_a.config,
        project_root=project_a.project_root,
    ))
    project_a_id = client.get("/api/workspace/projects").json()[
        "server_default_project_id"
    ]

    detail_a = client.get(
        f"/api/projects/{project_a_id}/traces/trace-shared",
        params={"contract": "v2"},
    ).json()
    detail_b = client.get(
        f"/api/projects/{registry_project_b.project_id}/traces/trace-shared",
        params={"contract": "v2"},
    ).json()
    page_a = client.get(
        f"/api/projects/{project_a_id}/traces",
        params={"contract": "v2", "limit": 1},
    ).json()
    crossed = client.get(
        f"/api/projects/{registry_project_b.project_id}/traces",
        params={"contract": "v2", "cursor": page_a["next_cursor"]},
    )

    assert [event["id"] for event in detail_a["timeline"]] == ["evt-a"]
    assert [event["id"] for event in detail_b["timeline"]] == ["evt-b"]
    assert crossed.status_code == 400
    assert crossed.json()["detail"] == "trace cursor project mismatch"

    for index in range(2):
        _append(
            project_a.state_dir,
            event_id=f"project-a-span-start-{index}",
            trace_id="trace-span-shared",
            event_type="runtime.action.attempt.started",
            payload={"attempt_id": f"project-a-attempt-{index}"},
        )
        _append(
            project_a.state_dir,
            event_id=f"project-a-span-done-{index}",
            trace_id="trace-span-shared",
            event_type="runtime.action.attempt.completed",
            causation_id=f"project-a-span-start-{index}",
            payload={"attempt_id": f"project-a-attempt-{index}"},
        )
    span_page_a = client.get(
        f"/api/projects/{project_a_id}/traces/trace-span-shared/spans",
        params={"limit": 1},
    ).json()
    crossed_span_cursor = client.get(
        f"/api/projects/{registry_project_b.project_id}"
        "/traces/trace-span-shared/spans",
        params={"cursor": span_page_a["next_cursor"]},
    )

    assert crossed_span_cursor.status_code == 400
    assert crossed_span_cursor.json()["detail"] == (
        "trace cursor project mismatch"
    )


def test_trace_source_failure_does_not_render_a_false_zero(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import zf.web.trace_routes as routes
    from zf.web.projections.projection_failures import record_projection_failure
    def failed(*args, **kwargs):
        record_projection_failure("canonical_events")
        return {"items": [], "event_count": 0}
    monkeypatch.setattr(routes, "trace_list_page", failed)
    app = FastAPI()
    app.include_router(routes.build_trace_router(resolve_ctx=lambda _: SimpleNamespace(state_dir=tmp_path, config=None)))
    response = TestClient(app).get("/api/projects/p/traces?contract=v2")
    assert response.status_code == 503
    assert "items" not in response.json()
