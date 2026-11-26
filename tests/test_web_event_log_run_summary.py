import json
from collections import Counter

import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.projections.events import _event_log_run_summary
from zf.web.projections.projection_failures import collect_projection_failures
from zf.web.projections.summaries import _archive_tasks
from zf.web.projections.tasks import _task_runs


def test_task_run_counts_store_rows_without_projecting_every_card(tmp_path, monkeypatch):
    archive = tmp_path / "kanban"
    archive.mkdir()
    private_id = "TOKEN=fixture-private"
    (tmp_path / "kanban.json").write_text(json.dumps([
        {"id": "A", "title": "A", "status": "done"},
        {"id": "B", "title": "B", "status": "in_progress"},
        {"id": private_id, "title": "private", "status": "done"},
    ]))
    (archive / "2026-01-01.json").write_text(json.dumps([
        {"id": "A", "title": "prior A", "status": "done"},
        {"id": "B", "title": "prior B", "status": "cancelled"},
        {"id": "C", "title": "without events", "status": "done"},
    ]))
    log = EventLog(tmp_path / "events.jsonl")
    for task_id in ("A", "B", private_id):
        log.append(ZfEvent(type="task.started", task_id=task_id))

    old = Counter(row["status"] for row in _archive_tasks(tmp_path, include_active=True)
                  if row["id"] in {"A", "B", private_id})
    assert (old["done"], old["cancelled"]) == (2, 1)
    monkeypatch.setattr("zf.web.projections.tasks.task_card_projection",
                        lambda *args, **kwargs: pytest.fail("summary projected a task card"))
    fallback, = _task_runs(tmp_path, project_root=tmp_path, task_id="A")["runs"]
    summary = fallback["summary"]
    assert summary["done_tasks"] == old["done"]
    assert summary["cancelled_tasks"] == old["cancelled"]
    assert summary["task_count"] == 3


def test_run_summary_preserves_missing_and_invalid_store_semantics(tmp_path):
    archive = tmp_path / "kanban"
    archive.mkdir()
    (archive / "2026-01-01.json").write_text('[{"id":"A","title":"A","status":"done"}]')
    assert _event_log_run_summary(tmp_path) is None
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="sample"))
    with collect_projection_failures() as failures:
        assert _event_log_run_summary(tmp_path) is None
        assert failures == []
    log.append(ZfEvent(type="task.started", task_id="A"))
    with collect_projection_failures() as failures:
        assert _event_log_run_summary(tmp_path)["summary"]["done_tasks"] == 0
        assert failures == []
    (tmp_path / "kanban.json").write_text("not a valid task store")
    with collect_projection_failures() as failures:
        assert _event_log_run_summary(tmp_path)["summary"]["done_tasks"] == 0
        assert failures == ["archive_tasks"]


def test_run_summary_filters_task_ids_by_verified_events(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_TEST_SUMMARY_SIGNER", "fixture-only-key")
    config = ZfConfig()
    config.security.event_signing.enabled = True
    config.security.event_signing.secret_env = "ZF_TEST_SUMMARY_SIGNER"
    log = event_log_from_project(tmp_path, config=config)
    log.append(ZfEvent(type="task.done", task_id="SIGNED"))
    with log.path.open("a") as stream:
        stream.write(ZfEvent(type="task.done", task_id="UNSIGNED").to_json() + "\n")
    (tmp_path / "kanban.json").write_text(json.dumps([
        {"id": name, "title": name, "status": "done"} for name in ("SIGNED", "UNSIGNED")
    ]))
    summary = _event_log_run_summary(tmp_path, config=config)["summary"]
    assert summary["task_ids"] == ["SIGNED"]
    assert summary["done_tasks"] == 1
