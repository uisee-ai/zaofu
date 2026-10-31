from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.safety import PathGuardError
from zf.runtime.session_tailer import ClaudeSessionTailer
from zf.runtime.workflow_delivery import build_flow_submit_preview
from zf.web.server import create_app
from zf.core.config.loader import load_config


def _write_config(path: Path) -> None:
    path.write_text(
        """apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: review}
spec:
  version: '1.0'
  project: {name: review, state_dir: .zf}
""",
        encoding="utf-8",
    )


def _web_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "review-token")
    config_path = tmp_path / "zf.yaml"
    _write_config(config_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    return TestClient(
        create_app(
            state_dir,
            config=load_config(config_path),
            project_root=tmp_path,
        )
    )


def test_web_workflow_rejects_cross_project_paths(tmp_path: Path, monkeypatch) -> None:
    client = _web_client(tmp_path, monkeypatch)
    outside = tmp_path.parent / "review-escape.md"
    response = client.post(
        "/api/projects/default/workflow-intake",
        headers={"x-zf-web-token": "review-token"},
        json={
            "request_id": "REQ-ESCAPE",
            "kind": "issue",
            "objective": "escape",
            "output": str(outside),
        },
    )
    assert response.status_code == 422
    assert not outside.exists()


def test_submit_preview_rejects_absolute_output_escape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "preview.json"
    with pytest.raises(PathGuardError):
        build_flow_submit_preview(
            config_path=project / "zf.yaml",
            intake_path=project / "intake.md",
            output=outside,
            project_root=project,
        )


def test_store_ids_are_unique_across_active_and_archive(tmp_path: Path) -> None:
    tasks = TaskStore(tmp_path / "kanban.json")
    tasks.add(Task(id="TASK-DUP"))
    with pytest.raises(ValueError, match="already exists"):
        tasks.add(Task(id="TASK-DUP"))
    tasks.update("TASK-DUP", status="done")
    with pytest.raises(ValueError, match="already exists"):
        tasks.add(Task(id="TASK-DUP"))

    features = FeatureStore(tmp_path / "feature_list.json")
    features.add(Feature(id="FEATURE-DUP"))
    with pytest.raises(ValueError, match="already exists"):
        features.add(Feature(id="FEATURE-DUP"))
    features.update("FEATURE-DUP", status="done")
    with pytest.raises(ValueError, match="already exists"):
        features.add(Feature(id="FEATURE-DUP"))


def test_task_add_is_atomic_for_concurrent_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "kanban.json"

    def add_one() -> str:
        try:
            TaskStore(path).add(Task(id="TASK-RACE"))
        except ValueError:
            return "conflict"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: add_one(), range(2)))
    assert sorted(results) == ["conflict", "created"]
    assert len(TaskStore(path).list_all()) == 1


def test_product_delivery_feature_upsert_handles_race(tmp_path: Path) -> None:
    from zf.runtime.product_delivery import _upsert_delivery_feature

    def upsert() -> dict:
        return _upsert_delivery_feature(
            state_dir=tmp_path,
            task_map={"feature_id": "FEATURE-RACE", "title": "race"},
            refs={},
            writer=None,
            actor="test",
            causation_id="",
            correlation_id="",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upsert(), range(2)))
    assert sorted(result["status"] for result in results) == ["created", "reused"]
    assert len(FeatureStore(tmp_path / "feature_list.json").list_all()) == 1


def test_terminal_index_is_rebuilt_for_ready_and_corruption(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="TASK-DONE"))
    store.update("TASK-DONE", status="done")
    store.add(Task(id="TASK-NEXT", blocked_by=["TASK-DONE"]))
    store._index_path.unlink()
    assert [task.id for task in store.ready()] == ["TASK-NEXT"]
    store._index_path.write_text("{broken", encoding="utf-8")
    assert [task.id for task in store.ready()] == ["TASK-NEXT"]


def test_session_tailer_keeps_byte_offset_for_partial_utf8(tmp_path: Path) -> None:
    class CaptureTailer(ClaudeSessionTailer):
        def __init__(self, event_path: Path) -> None:
            super().__init__(EventLog(event_path))
            self.lines: list[str] = []

        def _emit_from_line(self, instance_id: str, raw: str) -> None:
            self.lines.append(raw)

    session = tmp_path / "session.jsonl"
    tailer = CaptureTailer(tmp_path / "events.jsonl")
    payload = (json.dumps({"message": "你好"}, ensure_ascii=False) + "\n").encode()
    cut = payload.index("你".encode()) + 1
    partial = payload[:cut]
    session.write_bytes(partial)
    assert tailer._drain("dev", session, 0) == 0
    session.write_bytes(partial)
    assert tailer._drain("dev", session, 0) == 0
    session.write_bytes(payload)
    assert tailer._drain("dev", session, 0) == len(payload)
    assert tailer.lines == [payload[:-1].decode()]


def test_start_event_adapter_enriches_events(tmp_path: Path) -> None:
    from zf.cli.start import _append_event

    log = EventLog(tmp_path / "events.jsonl")
    parent = ZfEvent(type="task.created", task_id="TASK-1")
    log.append(parent)
    _append_event(log, ZfEvent(type="task.updated", task_id="TASK-1"))
    updated = log.read_all()[-1]
    assert updated.causation_id == parent.id
