"""/health/summary 口径测试(doc116 §11.1 header 权威源).

r10 现场 bug:Board 把 fanout 排队(blocked_reason=fanout_queue:*)刻意投影到
Todo 列(7c0ec4c7),header 却按 raw status 报 "5 blocked" —— 列里找不到
Blocked、header 报 5,操作员看到口径打架。修 = header 与 Board 用同一判据,
排队单列为 queued,blocked 只剩真阻塞。
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.web.server import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "events.jsonl").write_text("")
    tasks = [
        {"id": "T-RUN-1", "title": "a", "status": "in_progress"},
        {"id": "T-RUN-2", "title": "b", "status": "in_progress"},
        {"id": "T-QUEUE-1", "title": "c", "status": "blocked",
         "blocked_reason": "fanout_queue:fo-1:queued-T-QUEUE-1-6"},
        {"id": "T-QUEUE-2", "title": "d", "status": "blocked",
         "blocked_reason": "fanout_queue:fo-1:queued-T-QUEUE-2-7"},
        {"id": "T-STUCK-1", "title": "e", "status": "blocked",
         "blocked_reason": "missing dependency: upstream contract"},
        {"id": "T-TODO-1", "title": "f", "status": "backlog"},
        {"id": "T-DONE-1", "title": "g", "status": "done"},
    ]
    (sd / "kanban.json").write_text(json.dumps(tasks))
    return TestClient(create_app(sd))


def test_health_summary_splits_queued_from_blocked(client: TestClient) -> None:
    body = client.get("/api/projects/default/health/summary").json()
    assert body["active"] == 2
    assert body["queued"] == 2, "fanout_queue 排队不算真阻塞"
    assert body["blocked"] == 1, "只剩真阻塞(非 fanout_queue reason)"
    # raw truth 表保持完整(kanban.json 的 status 原样计数,不被口径拆分污染)
    assert body["task_counts"]["blocked"] == 3
    assert body["task_counts"]["in_progress"] == 2


def test_health_summary_consistent_with_board_column(client: TestClient) -> None:
    """header blocked == Board Blocked 列数(同一判据),queued+blocked == raw。"""
    from zf.core.task.kanban_projection import kanban_column_projection
    from zf.core.task.schema import Task

    body = client.get("/api/projects/default/health/summary").json()
    board_blocked = 0
    for tid, reason in [
        ("T-QUEUE-1", "fanout_queue:fo-1:queued-T-QUEUE-1-6"),
        ("T-QUEUE-2", "fanout_queue:fo-1:queued-T-QUEUE-2-7"),
        ("T-STUCK-1", "missing dependency: upstream contract"),
    ]:
        task = Task(id=tid, title="x", status="blocked", blocked_reason=reason)
        if kanban_column_projection(task).column == "blocked":
            board_blocked += 1
    assert body["blocked"] == board_blocked == 1
    assert body["queued"] + body["blocked"] == body["task_counts"]["blocked"]
