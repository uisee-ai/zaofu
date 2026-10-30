"""Web API tests for measure-loop.v1 routes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.measure_loop_routes import build_measure_loop_router
from zf.web.projections.loop_view_source import LOOP_VIEW_SOURCE_FIELD
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "feature_list.json").write_text("[]")
    store = TaskStore(sd / "kanban.json")
    store.add(Task(
        id="T1",
        title="gateway",
        status="backlog",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    log = event_log_from_project(sd, config=None, warn=False)
    log.append(ZfEvent(type="task.dispatched", id="dispatch-1", task_id="T1", payload={"feature_id": "F-1"}))
    log.append(ZfEvent(type="static_gate.failed", id="gate-fail", task_id="T1", payload={"feature_id": "F-1"}))
    return sd


@pytest.fixture
def client(state_dir: Path) -> TestClient:
    return TestClient(create_app(state_dir))


def _loop_only_client(
    state_dir: Path,
    *,
    project_root: Path | None = None,
    config=None,
) -> TestClient:
    context = SimpleNamespace(
        state_dir=state_dir,
        config=config,
        project_root=project_root or state_dir.parent,
    )
    app = FastAPI()
    app.include_router(build_measure_loop_router(resolve_ctx=lambda _project_id: context))
    return TestClient(app)


def test_measure_loop_endpoint(client: TestClient) -> None:
    response = client.get("/api/projects/default/measure/loops?feature_id=F-1&lens=verification")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "measure-loop.v1"
    assert data["active_lens"] == "verification"
    assert data["metrics"][0]["label"] == "Gate Pass"
    assert data["stages"][0]["label"] == "Dev Done"
    assert data["source_projection_refs"]


def test_measure_loop_endpoint_is_read_only(client: TestClient, state_dir: Path) -> None:
    kanban = state_dir / "kanban.json"
    before = (kanban.stat().st_mtime_ns, kanban.read_bytes())

    response = client.get("/api/projects/default/measure/loops")

    after = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    assert response.status_code == 200
    assert before == after


def test_loop_view_api_separates_attempts_and_auxiliary_events(state_dir: Path) -> None:
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="task_map.admitted", origin="kernel", id="map-ok", payload={
        "task_map_ref": "artifacts/task-map.json", "task_map_digest": "a" * 64, "task_ids": ["T1"],
    }))
    log.append(ZfEvent(type="task.attempt.started", origin="kernel", task_id="T1", payload={"attempt_id": "a1"}))
    log.append(ZfEvent(type="web.action.failed", task_id="T1"))
    log.append(ZfEvent(type="task.attempt.failed", origin="kernel", task_id="T1", payload={"attempt_id": "a1"}))
    client = _loop_only_client(state_dir)
    first = client.get("/api/projects/default/loop-view")
    second = client.get("/api/projects/default/loop-view")
    assert first.status_code == second.status_code == 200
    for response in (first, second):
        timeline = response.json()["timeline"]
        assert timeline["attempt_count"] == timeline["failed_attempt_count"] == 1
        assert timeline["signal_count"] == 1
        assert timeline["tasks"][0]["signals"][0]["type"] == "web.action.failed"
        assert timeline["baseline"]["event_id"] == "map-ok"


def test_loop_view_endpoint(client: TestClient) -> None:
    response = client.get("/api/projects/default/loop-view")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "loop-view.v1"
    assert data["run"]["promise"]["source"] == "generic fallback"
    assert "delivery" in data["loops"]
    assert data["tasks"]  # T1 dispatch 产生 attempt 行


def test_loop_view_ignores_cache_from_older_projection_revision(
    client: TestClient,
    state_dir: Path,
) -> None:
    from zf.web.projections.read_model import (
        current_projected_seq,
        rebuild,
        set_cached_projection,
    )

    rebuild(state_dir)
    source_seq = current_projected_seq(state_dir, config=None)
    set_cached_projection(
        state_dir,
        "loop-view:default",
        kind="loop-view",
        source_seq=source_seq,
        payload={"schema_version": "stale-loop-view.v0"},
    )

    response = client.get("/api/projects/default/loop-view")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "loop-view.v1"


def test_loop_view_cache_miss_reads_eventlog_once_and_hit_skips_catch_up(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.web import measure_loop_routes
    from zf.web.projections import loop_view_source, read_model

    scans = 0
    builds = 0
    from zf.web.projections import metric_facts
    real_iter = metric_facts.iter_event_records_from_cursor
    real_build = measure_loop_routes.build_loop_view

    def iter_spy(*args, **kwargs):
        nonlocal scans
        scans += 1
        yield from real_iter(*args, **kwargs)

    def build_spy(*args, **kwargs):
        nonlocal builds
        builds += 1
        return real_build(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Loop cache lookup must not catch up the SQLite read model")

    monkeypatch.setattr(metric_facts, "iter_event_records_from_cursor", iter_spy)
    monkeypatch.setattr(measure_loop_routes, "build_loop_view", build_spy)
    monkeypatch.setattr(read_model, "current_projected_seq", forbidden)
    monkeypatch.setattr(read_model, "rebuild", forbidden)

    client = _loop_only_client(state_dir)
    miss = client.get("/api/projects/default/loop-view")
    hit = client.get("/api/projects/default/loop-view")

    assert miss.status_code == hit.status_code == 200
    assert scans == builds == 1
    assert LOOP_VIEW_SOURCE_FIELD not in miss.json()
    assert LOOP_VIEW_SOURCE_FIELD not in hit.json()
    assert hit.json()["projection_cache"]["source_seq"] == 2


def test_loop_view_cache_invalidates_on_event_append(
    client: TestClient,
    state_dir: Path,
) -> None:
    before = client.get("/api/projects/default/loop-view").json()
    event_log_from_project(state_dir, config=None, warn=False).append(ZfEvent(
        type="task.dispatched",
        id="dispatch-2",
        task_id="T2",
        payload={"feature_id": "F-2"},
    ))

    after = client.get("/api/projects/default/loop-view").json()

    assert after["run"]["event_count"] == before["run"]["event_count"] + 1
    assert {task["id"] for task in after["tasks"]} == {"T1", "T2"}


def test_loop_view_cache_invalidates_on_atomic_eventlog_replacement(
    client: TestClient,
    state_dir: Path,
) -> None:
    first = client.get("/api/projects/default/loop-view").json()
    assert {task["id"] for task in first["tasks"]} == {"T1"}

    active = state_dir / "events.jsonl"
    before = active.stat()
    replacement = state_dir / "events.replacement"
    replacement.write_bytes(active.read_bytes().replace(b'"T1"', b'"T9"'))
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, active)
    assert active.stat().st_size == before.st_size
    assert active.stat().st_mtime_ns == before.st_mtime_ns

    replaced = client.get("/api/projects/default/loop-view").json()

    assert {task["id"] for task in replaced["tasks"]} == {"T9"}


def test_loop_view_cache_invalidates_on_rotation_and_archive_replacement(
    client: TestClient,
    state_dir: Path,
) -> None:
    client.get("/api/projects/default/loop-view")
    active = state_dir / "events.jsonl"
    archive_dir = state_dir / "events"
    archive_dir.mkdir(exist_ok=True)
    archive = archive_dir / "2026-08-19.jsonl"
    active.rename(archive)
    event_log_from_project(state_dir, config=None, warn=False).append(ZfEvent(
        type="task.dispatched",
        id="dispatch-2",
        task_id="T2",
        payload={"feature_id": "F-2"},
    ))

    rotated = client.get("/api/projects/default/loop-view").json()
    assert {task["id"] for task in rotated["tasks"]} == {"T1", "T2"}

    before = archive.stat()
    replacement = archive_dir / "archive.replacement"
    replacement.write_bytes(archive.read_bytes().replace(b'"T1"', b'"T9"'))
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, archive)
    archive_replaced = client.get("/api/projects/default/loop-view").json()

    assert {task["id"] for task in archive_replaced["tasks"]} == {"T9", "T2"}


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        (
            "task_attempts.json",
            {
                "tasks": {
                    "T-SIDECAR": {
                        "attempts": [
                            {"started_ts": "2026-08-20T00:00:00Z", "role": "dev"},
                        ],
                    },
                },
            },
        ),
        ("stage_spine.json", {"stages": {}}),
        ("workflow_health.json", {"counters": {"recoveries": 1}}),
    ],
)
def test_loop_view_cache_invalidates_on_loop_sidecar_change(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    payload: dict,
) -> None:
    from zf.web import measure_loop_routes

    builds = 0
    real_build = measure_loop_routes.build_loop_view

    def build_spy(*args, **kwargs):
        nonlocal builds
        builds += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(measure_loop_routes, "build_loop_view", build_spy)
    # The full application starts a background prewarmer that can write sidecars.
    client = _loop_only_client(state_dir)
    client.get("/api/projects/default/loop-view")
    client.get("/api/projects/default/loop-view")
    projection_path = state_dir / "projections" / name
    projection_path.parent.mkdir(exist_ok=True)
    projection_path.write_text(json.dumps(payload), encoding="utf-8")

    changed = client.get("/api/projects/default/loop-view").json()

    assert builds == 2
    if name == "task_attempts.json":
        assert changed["tasks"][0]["id"] == "T-SIDECAR"
    if name == "workflow_health.json":
        assert changed["health_counters"] == {"recoveries": 1}


def test_loop_view_reads_event_truth_when_read_model_is_stale(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.web.projections import read_model

    read_model.rebuild(state_dir)
    event_log_from_project(state_dir, config=None, warn=False).append(ZfEvent(
        type="task.dispatched",
        id="dispatch-tail",
        task_id="T-TAIL",
        payload={"feature_id": "F-TAIL"},
    ))

    def forbidden(*_args, **_kwargs):
        pytest.fail("stale read model must not gate Loop freshness")

    monkeypatch.setattr(read_model, "rebuild", forbidden)
    monkeypatch.setattr(read_model, "current_projected_seq", forbidden)
    client = _loop_only_client(state_dir)
    response = client.get("/api/projects/default/loop-view")

    assert response.status_code == 200
    assert "T-TAIL" in {task["id"] for task in response.json()["tasks"]}
    assert read_model.projection_status(state_dir)["tail_behind"] is True


def test_loop_view_tolerates_corrupt_read_model(
    client: TestClient,
    state_dir: Path,
) -> None:
    from zf.web.projections import read_model

    path = read_model.db_path(state_dir)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"not-a-sqlite-database")

    response = client.get("/api/projects/default/loop-view")

    assert response.status_code == 200
    assert {task["id"] for task in response.json()["tasks"]} == {"T1"}
    assert LOOP_VIEW_SOURCE_FIELD not in response.json()


def test_loop_view_disables_cache_when_fingerprint_probe_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.web.projections import loop_view_source

    def unavailable(_state_dir):
        raise OSError("segment stat unavailable")

    monkeypatch.setattr(loop_view_source, "list_event_segments", unavailable)

    response = client.get("/api/projects/default/loop-view")

    assert response.status_code == 503
    assert "tasks" not in response.json()
    assert "projection_cache" not in response.json()


def test_loop_view_does_not_cache_a_moving_source_watermark(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.web import measure_loop_routes
    from zf.web.projections import loop_view_source, read_model

    snapshots = [
        [
            SimpleNamespace(
                seq=1,
                event=ZfEvent(type="task.dispatched", id="moving-1", task_id="T-OLD"),
            ),
        ],
        [
            SimpleNamespace(
                seq=1,
                event=ZfEvent(type="task.dispatched", id="moving-2", task_id="T-NEW"),
            ),
        ],
    ]
    fingerprints = iter(("before-1", "after-1", "before-2", "after-2"))

    monkeypatch.setattr(
        measure_loop_routes,
        "loop_view_source_fingerprint",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        loop_view_source,
        "loop_view_source_fingerprint",
        lambda *_args, **_kwargs: next(fingerprints),
    )
    monkeypatch.setattr(
        loop_view_source,
        "_read_metric_events",
        lambda *_args, **_kwargs: [(record.seq, record.event) for record in snapshots.pop(0)],
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("an unstable source snapshot must not be cached")

    monkeypatch.setattr(read_model, "set_cached_projection", forbidden)
    response = _loop_only_client(state_dir).get("/api/projects/default/loop-view")

    assert response.status_code == 503
    assert "tasks" not in response.json()
    assert snapshots == []
    assert "projection_cache" not in response.json()


def test_loop_view_cache_is_project_scoped(tmp_path: Path) -> None:
    contexts = {}
    for project_id in ("alpha", "beta"):
        root = tmp_path / project_id
        state_dir = root / ".zf"
        state_dir.mkdir(parents=True)
        (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
        event_log_from_project(state_dir, config=None, warn=False).append(ZfEvent(
            type="task.dispatched",
            id=f"dispatch-{project_id}",
            task_id=f"T-{project_id.upper()}",
        ))
        contexts[project_id] = SimpleNamespace(
            state_dir=state_dir,
            config=None,
            project_root=root,
        )
    app = FastAPI()
    app.include_router(build_measure_loop_router(resolve_ctx=contexts.__getitem__))
    client = TestClient(app)

    alpha = client.get("/api/projects/alpha/loop-view").json()
    beta = client.get("/api/projects/beta/loop-view").json()

    assert alpha["project_id"] == "alpha"
    assert beta["project_id"] == "beta"
    assert {task["id"] for task in alpha["tasks"]} == {"T-ALPHA"}
    assert {task["id"] for task in beta["tasks"]} == {"T-BETA"}


def test_loop_view_cache_invalidates_on_config_and_signer_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "signed"
    state_dir = root / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
    signing = SimpleNamespace(
        enabled=True,
        secret_env="ZF_TEST_LOOP_EVENT_SECRET",
        allow_unsigned_fallback=False,
    )
    config = SimpleNamespace(security=SimpleNamespace(event_signing=signing))
    monkeypatch.setenv("ZF_TEST_LOOP_EVENT_SECRET", "first-secret")
    event_log_from_project(state_dir, config=config, warn=False).append(ZfEvent(
        type="task.dispatched",
        id="dispatch-signed",
        task_id="T-SIGNED",
    ))
    context = SimpleNamespace(state_dir=state_dir, config=config, project_root=root)
    app = FastAPI()
    app.include_router(build_measure_loop_router(resolve_ctx=lambda _project_id: context))
    client = TestClient(app)

    first = client.get("/api/projects/signed/loop-view").json()
    assert {task["id"] for task in first["tasks"]} == {"T-SIGNED"}
    (root / "zf.yaml").write_text(
        "workflow_completion:\n  required_events:\n    - task.dispatched\n",
        encoding="utf-8",
    )
    config_changed = client.get("/api/projects/signed/loop-view").json()
    assert config_changed["run"]["promise"]["source"] == "workflow_completion contract"

    monkeypatch.setenv("ZF_TEST_LOOP_EVENT_SECRET", "second-secret")
    signer_changed = client.get("/api/projects/signed/loop-view").json()

    assert signer_changed["run"]["event_count"] == 1
    assert signer_changed["tasks"] == []
