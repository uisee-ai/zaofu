"""S6 conformance for bounded Web read-model freshness."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from tests.testkit.scenarios import isolated_project
from tests.test_workspace_projects import _make_project
from zf.core.config.loader import load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.workspace.registry import WorkspaceRegistry
from zf.web.projections import read_model
from zf.web.projections.events import _events_page
from zf.web.server import create_app


def test_events_api_exposes_lag_and_read_model_can_require_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary reads are bounded-stale; explicit reads converge synchronously."""
    project = isolated_project(tmp_path, run_id="RUN-S6-FRESHNESS")
    log = EventLog(project.events_path)
    log.append(ZfEvent(type="session.started", actor="testkit"))
    read_model.rebuild(project.state_dir)
    log.append(
        ZfEvent(
            type="channel.created",
            actor="testkit",
            payload={"channel_id": "ch-freshness", "name": "Freshness"},
        ),
    )

    # Prevent a daemon catch-up from racing this deterministic contract check.
    monkeypatch.setattr(read_model, "request_catch_up", lambda *args, **kwargs: None)
    client = TestClient(create_app(project.state_dir, project_root=project.root))

    bounded = client.get("/api/events?prefix=channel.")
    assert bounded.status_code == 200
    bounded_payload = bounded.json()
    assert bounded_payload["items"] == []
    assert bounded_payload["tail_behind"] is True
    # The implementation may omit a source-count scan for a bounded read;
    # tail_behind plus the selected watermark is the stable freshness signal.
    assert bounded_payload["projection_lag"] in {None, 1}
    assert bounded_payload["current_seq"] == 1

    fresh_payload = read_model.events_page(
        project.state_dir,
        limit=100,
        event_prefix="channel.",
        require_fresh=True,
    )
    assert fresh_payload is not None
    assert [item["type"] for item in fresh_payload["items"]] == ["channel.created"]
    assert fresh_payload["current_seq"] == 2
    assert fresh_payload["tail_behind"] is False
    assert fresh_payload["projection_lag"] == 0


def test_legacy_events_route_marks_bounded_stale_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy route may lag, but must expose that fact and schedule catch-up."""
    project = isolated_project(tmp_path, run_id="RUN-S6-BOUNDED-STALE")
    log = EventLog(project.events_path)
    log.append(ZfEvent(type="session.started", actor="testkit"))
    read_model.rebuild(project.state_dir)
    log.append(
        ZfEvent(
            type="channel.created",
            actor="testkit",
            payload={"channel_id": "ch-bounded", "name": "Bounded"},
        ),
    )
    scheduled: list[Path] = []
    monkeypatch.setattr(
        read_model,
        "request_catch_up",
        lambda state_dir, *, config=None: scheduled.append(Path(state_dir)),
    )
    client = TestClient(create_app(project.state_dir, project_root=project.root))

    response = client.get("/api/events?prefix=channel.")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["projection_state"] == "ready"
    assert payload["tail_behind"] is True
    assert payload["source"] == "read_model.sqlite"
    assert scheduled
    assert set(scheduled) == {project.state_dir}


def test_registered_project_events_route_honors_freshness_parameter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The project route binds its query flag to the same isolated state root."""
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    state_dir = _make_project(root, name="fresh-route", state_dir_name="runtime-state")
    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    client = TestClient(
        create_app(state_dir, config=load_config(root / "zf.yaml"), project_root=root),
    )
    read_model.rebuild(state_dir)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="channel.created", actor="testkit", payload={
        "channel_id": "ch-route", "name": "Route",
    }))
    ledger_before = (state_dir / "events.jsonl").read_bytes()
    scheduled = []
    monkeypatch.setattr(
        read_model,
        "request_catch_up",
        lambda state_dir, *, config=None: scheduled.append(state_dir),
    )
    route = f"/api/projects/{project.project_id}/events"

    bounded = client.get(route, params={"prefix": "channel."})
    assert bounded.status_code == 200
    assert bounded.json()["items"] == []
    assert bounded.json()["current_seq"] == 1
    assert bounded.json()["tail_behind"] is True
    assert scheduled and set(scheduled) == {state_dir}

    fresh = client.get(route, params={"prefix": "channel.", "require_fresh": "true"})
    assert fresh.status_code == 200
    data = fresh.json()
    assert [item["type"] for item in data["items"]] == ["channel.created"]
    assert data["current_seq"] == 2
    assert data["projection_state"] == "ready"
    assert data["tail_behind"] is False
    assert data["projection_lag"] == 0
    assert (state_dir / "events.jsonl").read_bytes() == ledger_before
    assert not (root / ".zf").exists()


def test_registered_project_fresh_route_preserves_malformed_cursor_pagination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit fresh route must retain malformed rows and cursor order."""
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    state_dir = _make_project(root, name="malformed-fresh", state_dir_name="runtime-state")
    (state_dir / "events.jsonl").write_text(
        '{"type":"task.created","id":"e1","ts":"2026-04-27T00:00:00",'
        '"actor":"x","task_id":"T1","payload":{},'
        '"causation_id":null,"correlation_id":null}\n'
        'garbage line not json\n'
        '{"type":"task.dispatched","id":"e2","ts":"2026-04-27T00:00:01",'
        '"actor":"x","task_id":"T1","payload":{},'
        '"causation_id":null,"correlation_id":null}\n',
        encoding="utf-8",
    )
    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    read_model.rebuild(state_dir)
    client = TestClient(
        create_app(state_dir, config=load_config(root / "zf.yaml"), project_root=root),
    )

    route = f"/api/projects/{project.project_id}/events"
    first = client.get(route, params={"limit": 2, "cursor": 0, "require_fresh": "true"})
    assert first.status_code == 200
    first_payload = first.json()
    second = client.get(
        route,
        params={
            "limit": 2,
            "cursor": first_payload["next_cursor"],
            "require_fresh": "true",
        },
    )
    assert second.status_code == 200
    second_payload = second.json()

    assert [item["seq"] for item in first_payload["items"]] == [1, 2]
    assert first_payload["items"][1]["type"] == "event.malformed"
    assert [item["seq"] for item in second_payload["items"]] == [3]
    assert first_payload["tail_behind"] is False
    assert second_payload["tail_behind"] is False


def test_registered_project_fresh_route_binds_response_to_barrier_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events appended after rebuild must wait for the next fresh barrier."""
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    state_dir = _make_project(root, name="moving-watermark", state_dir_name="runtime-state")
    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="watermark.before", actor="testkit"))
    read_model.rebuild(state_dir)
    log.append(ZfEvent(type="watermark.barrier", actor="testkit"))

    real_rebuild = read_model.rebuild
    appended = False

    def rebuild_then_append(state_dir: Path, *, config=None) -> None:
        nonlocal appended
        real_rebuild(state_dir, config=config)
        if not appended:
            appended = True
            log.append(ZfEvent(type="watermark.after_barrier", actor="testkit"))

    monkeypatch.setattr(read_model, "rebuild", rebuild_then_append)
    client = TestClient(
        create_app(state_dir, config=load_config(root / "zf.yaml"), project_root=root),
    )
    route = f"/api/projects/{project.project_id}/events"

    first = client.get(route, params={"prefix": "watermark.", "require_fresh": "true"})
    assert first.status_code == 200
    first_payload = first.json()
    assert [item["type"] for item in first_payload["items"]] == [
        "watermark.before",
        "watermark.barrier",
    ]
    assert first_payload["current_seq"] == first_payload["items"][-1]["seq"]
    assert first_payload["tail_behind"] is True

    second = client.get(route, params={"prefix": "watermark.", "require_fresh": "true"})
    assert second.status_code == 200
    second_payload = second.json()
    assert [item["type"] for item in second_payload["items"]] == [
        "watermark.before",
        "watermark.barrier",
        "watermark.after_barrier",
    ]
    assert second_payload["current_seq"] == second_payload["items"][-1]["seq"]
    assert second_payload["tail_behind"] is False


def test_concurrent_fresh_readers_join_one_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A joining fresh reader observes the owner's completed watermark."""
    project = isolated_project(tmp_path)
    project.event_log().append(ZfEvent(id="baseline", type="session.started"))
    read_model.rebuild(project.state_dir)
    project.event_log().append(ZfEvent(id="tail", type="session.started"))
    ledger_before = project.events_path.read_bytes()
    started = threading.Event()
    release = threading.Event()
    joined = threading.Event()
    calls = []
    real_rebuild = read_model.rebuild

    def held_rebuild(state_dir, *, config=None):
        calls.append(state_dir)
        started.set()
        assert release.wait(timeout=5), "test did not release rebuild"
        return real_rebuild(state_dir, config=config)

    monkeypatch.setattr(read_model, "rebuild", held_rebuild)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(
            read_model.events_page, project.state_dir, limit=100, require_fresh=True,
        )
        try:
            assert started.wait(timeout=5), "owner did not enter rebuild"
            key = str(read_model.db_path(project.state_dir).resolve())
            with read_model._INFLIGHT_REBUILDS_LOCK:
                completion = read_model._INFLIGHT_REBUILDS[key]
            real_wait = completion.wait

            def observed_wait(timeout=None):
                joined.set()
                return real_wait(timeout)

            monkeypatch.setattr(completion, "wait", observed_wait)
            follower = pool.submit(
                read_model.events_page, project.state_dir, limit=100, require_fresh=True,
            )
            assert joined.wait(timeout=5), "second reader did not join owner"
            assert not owner.done() and not follower.done()
        finally:
            release.set()
        pages = [owner.result(timeout=5), follower.result(timeout=5)]

    assert calls == [project.state_dir]
    for page in pages:
        assert page is not None
        assert [item["id"] for item in page["items"]] == ["baseline", "tail"]
        assert page["current_seq"] == 2
        assert page["tail_behind"] is False
    assert project.events_path.read_bytes() == ledger_before
    assert key not in read_model._INFLIGHT_REBUILDS


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "ZF-TEST-022: single-flight rebuild failure is not propagated to joiners"
    ),
)
def test_joining_fresh_reader_fails_closed_when_rebuild_owner_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A require_fresh joiner must not serve stale data after owner failure."""
    project = isolated_project(tmp_path, run_id="RUN-S6-FAILURE")
    project.event_log().append(ZfEvent(id="baseline", type="session.started"))
    read_model.rebuild(project.state_dir)
    project.event_log().append(ZfEvent(id="tail", type="session.started"))

    started = threading.Event()
    release = threading.Event()

    def failed_rebuild(state_dir, *, config=None):
        started.set()
        assert release.wait(timeout=5), "test did not release rebuild"
        raise RuntimeError("synthetic rebuild failure")

    monkeypatch.setattr(read_model, "rebuild", failed_rebuild)
    outcomes: list[tuple[str, object]] = []

    def read() -> None:
        try:
            page = read_model.events_page(
                project.state_dir, limit=100, require_fresh=True,
            )
        except Exception as exc:  # noqa: BLE001 - capture propagation contract
            outcomes.append(("error", exc))
        else:
            outcomes.append(("page", page))

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(read)
        assert started.wait(timeout=5), "owner did not enter rebuild"
        key = str(read_model.db_path(project.state_dir).resolve())
        with read_model._INFLIGHT_REBUILDS_LOCK:
            completion = read_model._INFLIGHT_REBUILDS[key]
        joined = threading.Event()
        real_wait = completion.wait

        def observed_wait(timeout=None):
            joined.set()
            return real_wait(timeout)

        monkeypatch.setattr(completion, "wait", observed_wait)
        follower = pool.submit(read)
        assert joined.wait(timeout=5), "follower did not join owner"
        release.set()
        owner.result(timeout=5)
        follower.result(timeout=5)

    errors = [value for kind, value in outcomes if kind == "error"]
    pages = [value for kind, value in outcomes if kind == "page"]
    assert len(errors) == 2, f"both readers must fail closed, outcomes={outcomes!r}"
    assert not pages, f"stale page returned after rebuild failure: {pages!r}"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ZF-TEST-022: single-flight joiner timeout is silently ignored",
)
def test_single_flight_joiner_timeout_is_not_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh reader timeout must produce an explicit fail-closed outcome."""
    project = isolated_project(tmp_path, run_id="RUN-S6-TIMEOUT")
    started = threading.Event()
    release = threading.Event()

    def held_rebuild(state_dir, *, config=None):
        started.set()
        assert release.wait(timeout=5), "test did not release rebuild"

    monkeypatch.setattr(read_model, "rebuild", held_rebuild)
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(
            read_model._rebuild_single_flight,
            project.state_dir,
        )
        assert started.wait(timeout=5), "owner did not enter rebuild"
        timed_out = False
        try:
            read_model._rebuild_single_flight(project.state_dir, wait_timeout_s=0.01)
        except TimeoutError:
            timed_out = True
        finally:
            release.set()
        owner.result(timeout=5)

    assert timed_out, "joiner timeout must be explicit, not a silent success"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "ZF-TEST-012: EventLog fallback omits projection freshness/source metadata"
    ),
)
def test_event_fallback_preserves_freshness_and_source_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-model failure must remain distinguishable from an indexed read."""
    project = isolated_project(tmp_path, run_id="RUN-S6-FALLBACK")
    project.event_log().append(
        ZfEvent(
            id="fallback-event",
            type="channel.created",
            actor="testkit",
            payload={"channel_id": "ch-fallback", "name": "Fallback"},
        ),
    )

    def fail_projection(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic read-model failure")

    monkeypatch.setattr(read_model, "events_page", fail_projection)
    payload = _events_page(
        project.state_dir,
        limit=100,
        event_prefix="channel.",
    )

    assert payload["items"]
    assert payload.get("projection_state") == "fallback"
    assert payload.get("projection_source") == "event_log"
    assert payload.get("tail_behind") is None
    assert payload.get("projection_lag") is None
