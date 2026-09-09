from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.segments import (
    build_event_manifest,
    current_event_cursor,
    cursor_is_stale,
    iter_event_records,
    iter_event_records_from_cursor,
    write_event_manifest,
)
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.web.projections import read_model


def _write_line(path: Path, event: ZfEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.to_json() + "\n")


def test_segment_manifest_covers_archive_and_active(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    archived = ZfEvent(type="task.created", id="evt-arch", task_id="TASK-1")
    active = ZfEvent(type="dev.build.done", id="evt-active", task_id="TASK-1")
    _write_line(state_dir / "events" / "2026-06-21.jsonl", archived)
    _write_line(state_dir / "events.jsonl", active)

    manifest = build_event_manifest(state_dir)
    assert [segment.rel_path for segment in manifest.segments] == [
        "events/2026-06-21.jsonl",
        "events.jsonl",
    ]
    assert [record.event.id for record in iter_event_records(state_dir)] == [
        "evt-arch",
        "evt-active",
    ]

    manifest_path = write_event_manifest(state_dir)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["digest"] == manifest.digest
    assert payload["segments"][0]["kind"] == "archive"


def test_segment_cursor_detects_manifest_drift(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))

    cursor = current_event_cursor(state_dir)
    assert cursor.last_event_id == "evt-a"
    assert not cursor_is_stale(state_dir, cursor)

    _write_line(state_dir / "events.jsonl", ZfEvent(type="b", id="evt-b"))
    assert cursor_is_stale(state_dir, cursor)


def test_segment_cursor_reader_only_yields_new_complete_records(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))
    baseline = list(iter_event_records(state_dir))
    last = baseline[-1]
    _write_line(state_dir / "events.jsonl", ZfEvent(type="b", id="evt-b"))
    with (state_dir / "events.jsonl").open("ab") as handle:
        handle.write(b'{"id":"partial"')

    records = list(iter_event_records_from_cursor(
        state_dir,
        segment=last.raw_segment,
        byte_offset=last.raw_offset + last.raw_length,
        start_seq=last.seq,
    ))

    assert [(record.seq, record.event.id) for record in records] == [
        (2, "evt-b"),
    ]


def test_read_model_rebuild_indexes_timeline_and_raw_event(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events" / "2026-06-21.jsonl", ZfEvent(
        type="task.created",
        id="evt-1",
        actor="orchestrator",
        task_id="TASK-1",
        payload={"summary": "created", "feature_id": "FEAT"},
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="dev.build.done",
        id="evt-2",
        actor="dev",
        task_id="TASK-1",
        payload={"summary": "built", "trace_id": "trace-1"},
        correlation_id="trace-1",
    ))

    result = read_model.rebuild(state_dir)
    assert result["source_seq"] == 2
    assert (state_dir / "events" / "manifest.json").exists()

    page = read_model.events_page(state_dir, limit=10, task_id="TASK-1")
    assert page is not None
    assert [item["id"] for item in page["items"]] == ["evt-1", "evt-2"]
    assert page["items"][0]["event_ref"]["raw_segment"] == "events/2026-06-21.jsonl"

    timeline = read_model.task_timeline(state_dir, "TASK-1", limit=10)
    assert timeline is not None
    assert [item["type"] for item in timeline["timeline"]] == [
        "task.created",
        "dev.build.done",
    ]

    hydrated = read_model.hydrate_event_by_seq(state_dir, 2)
    assert hydrated is not None
    assert hydrated.id == "evt-2"
    assert hydrated.payload["summary"] == "built"


def test_event_ref_is_compact_and_hydrates_controlled_multi_refs(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="workflow.operation.started",
        id="evt-operation",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="run-1",
        causation_id="evt-parent",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": "op-1",
            "parent_operation_id": "op-parent",
            "attempt_id": "attempt-1",
            "dispatch_id": "dispatch-1",
            "active_dispatch_id": "dispatch-1",
            "plan_artifact_package_id": "planpkg-1",
            "package_id": "planpkg-1",
            "child_task_ids": ["TASK-2", "TASK-2", "TASK-3"],
            "trace_ids": ["trace-child"],
        },
    ))

    read_model.rebuild(state_dir)

    with sqlite3.connect(read_model.db_path(state_dir)) as conn:
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(event_ref)").fetchall()
        ]
        table_sql = str(conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'event_ref'"
        ).fetchone()[0])
        index_names = {
            row[1] for row in conn.execute("PRAGMA index_list(event_ref)").fetchall()
        }
        refs = set(conn.execute(
            "SELECT ref_kind, ref_id FROM event_ref ORDER BY ref_kind, ref_id"
        ).fetchall())

    assert columns == ["ref_kind", "ref_id", "seq"]
    assert "WITHOUT ROWID" in table_sql.upper()
    assert "idx_event_ref_seq" not in index_names
    assert ("operation_id", "op-1") in refs
    assert ("parent_operation_id", "op-parent") in refs
    assert ("workflow_run_id", "run-1") in refs
    assert ("attempt_id", "attempt-1") in refs
    assert ("dispatch_id", "dispatch-1") in refs
    assert ("plan_artifact_package_id", "planpkg-1") in refs
    assert ("task", "TASK-2") in refs
    assert ("task", "TASK-3") in refs
    assert ("trace", "trace-child") in refs
    assert ("event", "evt-operation") not in refs
    assert ("actor", "zf-cli") not in refs

    for ref_kind, ref_id in (
        ("event", "evt-operation"),
        ("task", "TASK-1"),
        ("task", "TASK-2"),
        ("operation_id", "op-1"),
        ("dispatch_id", "dispatch-1"),
        ("causation", "evt-parent"),
        ("correlation", "run-1"),
    ):
        events = read_model.hydrate_events_by_ref(
            state_dir,
            ref_kind=ref_kind,
            ref_id=ref_id,
        )
        assert events is not None
        assert [event.id for event in events] == ["evt-operation"]


def test_read_model_v4_schema_rebuilds_to_v5(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(
        state_dir / "events.jsonl",
        ZfEvent(
            type="workflow.operation.requested",
            id="evt-v5",
            payload={"operation_id": "op-v5"},
        ),
    )
    path = read_model.db_path(state_dir)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projection_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO projection_meta(key, value)
            VALUES ('schema_version', 'event-read-model.v4');
            CREATE TABLE event_ref (
              ref_kind TEXT NOT NULL,
              ref_id TEXT NOT NULL,
              seq INTEGER NOT NULL,
              event_id TEXT,
              PRIMARY KEY (ref_kind, ref_id, seq)
            );
            INSERT INTO event_ref VALUES ('event', 'legacy', 1, 'legacy');
            """
        )

    rebuilt = read_model.rebuild(state_dir)

    assert rebuilt["schema_version"] == "event-read-model.v6"
    events = read_model.hydrate_events_by_ref(
        state_dir,
        ref_kind="operation_id",
        ref_id="op-v5",
    )
    assert events is not None
    assert [event.id for event in events] == ["evt-v5"]
    with sqlite3.connect(path) as conn:
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(event_ref)").fetchall()
        ]
    assert columns == ["ref_kind", "ref_id", "seq"]


def test_read_model_indexes_sidecar_ref_metadata_without_raw_payload(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    descriptor = write_sidecar_text(
        state_dir,
        "diagnostics/run-1/report.txt",
        "secret full diagnostic payload",
        kind="diagnostic_trace",
        schema_version="diagnostic.v1",
        created_by="test",
        preview="secret full",
    )
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="diagnostic.ready",
        id="evt-diag",
        payload={"refs": {"diagnostic": descriptor}},
    ))

    read_model.rebuild(state_dir)
    page = read_model.sidecar_refs(state_dir)

    assert page is not None
    assert page["items"][0]["kind"] == "diagnostic_trace"
    assert page["items"][0]["ref"] == "diagnostics/run-1/report.txt"
    assert page["items"][0]["sha256"] == descriptor["sha256"]
    assert "secret full diagnostic payload" not in json.dumps(page, ensure_ascii=False)


def test_hydrate_events_combines_exact_types_and_prefixes_with_or(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="approval.requested", id="evt-approval"))
    _write_line(state_dir / "events.jsonl", ZfEvent(type="runtime.attention.owner", id="evt-attention"))
    _write_line(state_dir / "events.jsonl", ZfEvent(type="task.created", id="evt-task"))
    read_model.rebuild(state_dir)

    events = read_model.hydrate_events(
        state_dir,
        types=["approval.requested"],
        type_prefixes=["runtime.attention."],
    )

    assert [event.id for event in events] == ["evt-approval", "evt-attention"]


def test_events_page_serves_last_known_while_tail_catches_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="kanban.agent.turn.started", id="evt-started"))
    read_model.rebuild(state_dir)

    _write_line(state_dir / "events.jsonl", ZfEvent(type="kanban.agent.reply", id="evt-reply"))

    scheduled = []
    monkeypatch.setattr(
        read_model,
        "request_catch_up",
        lambda state_dir, *, config=None: scheduled.append(state_dir),
    )

    page = read_model.events_page(state_dir, limit=10)

    assert page is not None
    assert page["projection_state"] == "ready"
    assert [item["id"] for item in page["items"]] == ["evt-started"]
    assert page["current_seq"] == 1
    assert scheduled == [state_dir]


def test_events_page_explicit_fresh_read_catches_up_tail(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))
    read_model.rebuild(state_dir)
    _write_line(state_dir / "events.jsonl", ZfEvent(type="b", id="evt-b"))

    page = read_model.events_page(state_dir, limit=10, require_fresh=True)

    assert page is not None
    assert [item["id"] for item in page["items"]] == ["evt-a", "evt-b"]
    assert page["current_seq"] == 2


def test_events_page_body_is_bound_to_selected_watermark(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))
    read_model.rebuild(state_dir)
    _write_line(state_dir / "events.jsonl", ZfEvent(type="b", id="evt-b"))
    monkeypatch.setattr(
        read_model,
        "request_catch_up",
        lambda state_dir, *, config=None: read_model.rebuild(state_dir, config=config),
    )

    page = read_model.events_page(state_dir, limit=10)

    assert page is not None
    assert page["current_seq"] == 1
    assert [item["id"] for item in page["items"]] == ["evt-a"]


def test_agent_session_history_exposes_tail_and_supports_fresh_follow_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    base = {
        "project_id": "proj-a",
        "conversation_id": "kanban:proj-a",
        "thread_key": "thread-a",
        "backend": "codex-headless",
    }
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="user.message",
        id="evt-user",
        payload={
            **base,
            "target": "kanban-agent",
            "runtime_delivery": "headless",
            "message": "question",
        },
    ))
    read_model.rebuild(state_dir)
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.reply",
        id="evt-reply",
        payload={**base, "answer": "answer"},
    ))
    scheduled = []
    monkeypatch.setattr(
        read_model,
        "request_catch_up",
        lambda state_dir, *, config=None: scheduled.append(state_dir),
    )

    stale = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        backend="codex-headless",
        limit=10,
    )

    assert stale is not None
    assert stale["tail_behind"] is True
    assert [item["id"] for item in stale["items"]] == ["evt-user"]
    assert scheduled == [state_dir]

    fresh = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        backend="codex-headless",
        limit=10,
        require_fresh=True,
    )

    assert fresh is not None
    assert fresh["tail_behind"] is False
    assert [item["id"] for item in fresh["items"]] == ["evt-user", "evt-reply"]


def test_agent_session_history_reuses_one_event_decoder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    base = {
        "project_id": "proj-a",
        "conversation_id": "kanban:proj-a",
        "thread_key": "thread-a",
        "backend": "codex-headless",
    }
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="user.message",
        id="evt-user",
        payload={
            **base,
            "target": "kanban-agent",
            "runtime_delivery": "headless",
            "message": "question",
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.reply",
        id="evt-reply",
        payload={**base, "answer": "answer"},
    ))
    read_model.rebuild(state_dir)

    real_factory = read_model.event_log_from_project
    calls = 0

    def counted_factory(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(read_model, "event_log_from_project", counted_factory)
    page = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        backend="codex-headless",
        limit=10,
    )

    assert page is not None
    assert [item["id"] for item in page["items"]] == ["evt-user", "evt-reply"]
    assert calls == 1


def test_agent_session_history_body_is_bound_to_selected_watermark(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    base = {
        "project_id": "proj-a",
        "conversation_id": "kanban:proj-a",
        "thread_key": "thread-a",
        "backend": "codex-headless",
    }
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="user.message",
        id="evt-user",
        payload={
            **base,
            "target": "kanban-agent",
            "runtime_delivery": "headless",
            "message": "question",
        },
    ))
    read_model.rebuild(state_dir)
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.reply",
        id="evt-reply",
        payload={**base, "answer": "answer"},
    ))
    monkeypatch.setattr(
        read_model,
        "request_catch_up",
        lambda state_dir, *, config=None: read_model.rebuild(state_dir, config=config),
    )

    page = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        backend="codex-headless",
        limit=10,
    )

    assert page is not None
    assert page["current_seq"] == 1
    assert [item["id"] for item in page["items"]] == ["evt-user"]


def test_events_page_keeps_kanban_agent_projection_fields(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.reply",
        id="evt-reply",
        payload={
            "backend": "claude-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "thread-a",
            "turn_id": "turn-a",
            "answer": '{"action_proposal":{"action":"create-task"}}',
            "action_proposal": {
                "action": "create-task",
                "requested_action": "create-task",
                "payload": {
                    "title": "Fix Channel Group interactive E2E gap",
                    "contract": {"behavior": "cover the flow"},
                },
                "reason": "operator asked for task proposal",
                "valid": True,
            },
        },
    ))
    read_model.rebuild(state_dir)

    page = read_model.events_page(state_dir, limit=10)

    assert page is not None
    payload = page["items"][0]["payload"]
    assert payload["backend"] == "claude-headless"
    assert payload["project_id"] == "project-a"
    assert payload["thread_key"] == "thread-a"
    assert payload["turn_id"] == "turn-a"
    assert payload["answer"].startswith('{"action_proposal"')
    assert payload["action_proposal"]["action"] == "create-task"
    assert payload["action_proposal"]["payload"]["title"] == "Fix Channel Group interactive E2E gap"
    assert payload["action_proposal"]["payload"]["contract"]["behavior"] == "cover the flow"


def test_events_page_keeps_kanban_plan_request_and_response(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    request = {
        "request_event_id": "evt-plan",
        "request_id": "plan-route",
        "request_digest": "digest-route",
        "revision": 1,
        "question_id": "route",
        "question": "Which route?",
        "options": [
            {"id": "research", "label": "Research", "description": "Gather evidence."},
            {"id": "channel", "label": "Channel", "description": "Discuss roles."},
        ],
        "valid": True,
    }
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.plan.requested",
        id="evt-plan",
        payload={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "plan_request": request,
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.plan.answered",
        id="evt-answer",
        payload={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "request_event_id": "evt-plan",
            "request_id": "plan-route",
            "request_digest": "digest-route",
            "revision": 1,
            "question_id": "route",
            "option_id": "research",
            "answer": "Research",
            "answers": [
                {
                    "question_id": "route",
                    "option_id": "research",
                    "answer": "Research",
                },
                {
                    "question_id": "evidence",
                    "option_id": "focused",
                    "answer": "Focused",
                },
            ],
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="operator.action.proposed",
        id="evt-workflow-proposal",
        task_id="TASK-PLAN",
        payload={
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "turn_id": "turn-plan",
            "proposal": {
                "proposal_event_id": "evt-workflow-proposal",
                "proposal_id": "proposal-workflow",
                "proposal_digest": "digest-workflow",
                "action": "workflow-start",
                "payload": {
                    "task_id": "TASK-PLAN",
                    "route_id": "research:fixed",
                },
                "valid": True,
            },
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="operator.action.proposed",
        id="evt-cli-proposal",
        task_id="TASK-PLAN",
        payload={
            "project_id": "project-a",
            "source": "cli",
            "proposal": {
                "action": "workflow-start",
                "payload": {"task_id": "TASK-PLAN"},
                "valid": True,
            },
        },
    ))
    read_model.rebuild(state_dir)

    page = read_model.events_page(state_dir, limit=10)

    assert page is not None
    rows = {row["type"]: row["payload"] for row in page["items"]}
    assert rows["kanban.agent.plan.requested"]["plan_request"]["question"] == "Which route?"
    assert rows["kanban.agent.plan.answered"]["request_event_id"] == "evt-plan"
    assert rows["kanban.agent.plan.answered"]["request_id"] == "plan-route"
    assert rows["kanban.agent.plan.answered"]["revision"] == 1
    assert rows["kanban.agent.plan.answered"]["answer"] == "Research"
    assert rows["kanban.agent.plan.answered"]["answers"] == [
        {
            "question_id": "route",
            "option_id": "research",
            "answer": "Research",
        },
        {
            "question_id": "evidence",
            "option_id": "focused",
            "answer": "Focused",
        },
    ]
    assert rows["operator.action.proposed"]["proposal"]["action"] == (
        "workflow-start"
    )
    history = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="main",
        project_id="project-a",
        conversation_id="kanban:project-a",
        task_id="",
        limit=10,
    )
    assert history is not None
    assert any(
        item["type"] == "operator.action.proposed"
        for item in history["items"]
    )
    assert not any(
        item["id"] == "evt-cli-proposal"
        for item in history["items"]
    )


def test_agent_session_history_pages_full_kanban_thread_past_recent_window(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    for index in range(305):
        event_id = f"evt-user-{index:03d}"
        _write_line(state_dir / "events.jsonl", ZfEvent(
            type="user.message",
            id=event_id,
            task_id="TASK-HIST",
            payload={
                "target": "kanban-agent",
                "runtime_delivery": "headless",
                "message": f"question {index}",
                "project_id": "proj-a",
                "conversation_id": "kanban:proj-a",
                "thread_key": "thread-a",
                "backend": "claude-headless",
            },
        ))
        _write_line(state_dir / "events.jsonl", ZfEvent(
            type="kanban.agent.reply",
            id=f"evt-reply-{index:03d}",
            task_id="TASK-HIST",
            payload={
                "answer": f"answer {index}",
                "project_id": "proj-a",
                "conversation_id": "kanban:proj-a",
                "thread_key": "thread-a",
                "turn_id": event_id,
                "backend": "claude-headless",
            },
        ))
    read_model.rebuild(state_dir)

    latest = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        backend="claude-headless",
        task_id="TASK-HIST",
        limit=120,
    )
    assert latest is not None
    assert latest["items"][0]["payload"]["message"] == "question 245"
    assert latest["items"][-1]["payload"]["answer"] == "answer 304"

    older = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        backend="claude-headless",
        task_id="TASK-HIST",
        before_seq=latest["next_before_seq"],
        limit=500,
    )
    assert older is not None
    assert older["items"][0]["payload"]["message"] == "question 0"


def test_agent_session_history_is_backend_agnostic_for_thread(tmp_path: Path) -> None:
    """Frontend-stress S9/S10 regression: a kanban-agent thread is one durable
    conversation per (project, thread_id) that may span backends. Querying with
    a different backend than the one that produced the turns (fresh browser
    session defaults to Claude; user switched codex<->claude mid conversation)
    must still return the existing transcript — otherwise the panel shows an
    empty chat even though the durable turns exist (cousin of e812d7d2)."""
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="user.message",
        id="evt-user-codex",
        task_id="TASK-BE",
        payload={
            "target": "kanban-agent",
            "runtime_delivery": "headless",
            "message": "create a todo add task",
            "project_id": "proj-be",
            "conversation_id": "kanban:proj-be",
            "thread_key": "kanban:proj-be",
            "backend": "codex-headless",
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.turn.completed",
        id="evt-turn-codex",
        task_id="TASK-BE",
        payload={
            "answer": "proposal ready",
            "project_id": "proj-be",
            "conversation_id": "kanban:proj-be",
            "thread_key": "kanban:proj-be",
            "turn_id": "evt-user-codex",
            "backend": "codex-headless",
        },
    ))
    read_model.rebuild(state_dir)

    # Query with the DIFFERENT (default) backend a fresh session would use.
    page = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="kanban:proj-be",
        project_id="proj-be",
        conversation_id="kanban:proj-be",
        backend="claude-headless",
        limit=120,
    )
    assert page is not None
    types = [item["type"] for item in page["items"]]
    assert "user.message" in types, "codex turn must be visible under claude-headless query"
    assert "kanban.agent.turn.completed" in types


def test_agent_session_history_includes_user_prompt_for_long_kanban_turn(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="user.message",
        id="evt-user-review",
        payload={
            "source": "kanban",
            "target": "kanban-agent",
            "message": "review *.md",
            "runtime_delivery": "headless",
            "backend": "codex-headless",
            "project_id": "proj-a",
            "conversation_id": "kanban:proj-a",
            "thread_key": "thread-a",
            "request": {
                "turn_id": "turn-review",
                "message": "review *.md",
            },
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.turn.created",
        id="evt-turn-created",
        payload={
            "turn_id": "turn-review",
            "thread_key": "thread-a",
            "project_id": "proj-a",
            "conversation_id": "kanban:proj-a",
            "backend": "codex-headless",
            "message_event_id": "evt-user-review",
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="agent.session.run.started",
        id="evt-run-started",
        payload={
            "run_id": "turn-review",
            "thread_id": "thread-a",
            "source": "kanban-agent.headless",
            "project_id": "proj-a",
            "conversation_id": "kanban:proj-a",
            "message_id": "evt-user-review",
            "backend": "codex-headless",
            "provider": "codex-headless",
        },
    ))
    for index in range(220):
        _write_line(state_dir / "events.jsonl", ZfEvent(
            type="agent.session.part.delta",
            id=f"evt-delta-{index:03d}",
            payload={
                "run_id": "turn-review",
                "thread_id": "thread-a",
                "source": "kanban-agent.headless",
                "project_id": "proj-a",
                "conversation_id": "kanban:proj-a",
                "message_id": "evt-user-review",
                "backend": "codex-headless",
                "provider": "codex-headless",
                "part_id": f"text-{index:04d}",
                "kind": "text",
                "content": f"chunk {index}",
                "delta": f"chunk {index}",
                "seq": index,
            },
        ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="kanban.agent.reply",
        id="evt-reply",
        payload={
            "turn_id": "turn-review",
            "thread_key": "thread-a",
            "project_id": "proj-a",
            "conversation_id": "kanban:proj-a",
            "backend": "codex-headless",
            "answer": "final answer",
        },
    ))
    read_model.rebuild(state_dir)

    page = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        backend="codex-headless",
        limit=20,
    )

    assert page is not None
    messages = [
        item["payload"].get("message")
        for item in page["items"]
        if item["type"] == "user.message"
    ]
    assert messages == ["review *.md"]
    assert page["context_event_count"] >= 2
    assert page["next_before_seq"] > 1
    assert page["items"][-1]["payload"]["answer"] == "final answer"


def test_agent_session_history_returns_only_kanban_workflow_results(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    origin = {
        "schema_version": "workflow-origin-binding.v1",
        "surface": "kanban_agent",
        "source": "kanban-agent",
        "project_id": "proj-a",
        "channel_id": "",
        "thread_id": "",
        "conversation_id": "kanban:proj-a",
        "thread_key": "thread-a",
    }
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="workflow.result.available",
        id="evt-result",
        task_id="TASK-1",
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "project_id": "proj-a",
            "origin_surface": "kanban_agent",
            "conversation_id": "kanban:proj-a",
            "thread_key": "thread-a",
            "request_id": "REQ-1",
            "request_revision": 1,
            "task_id": "TASK-1",
            "workflow_run_id": "wf-1",
            "terminal_event_id": "evt-terminal",
            "artifact_ref": "research/TASK-1/result.md",
            "artifact_digest": "a" * 64,
            "summary": "Evidence-backed result.",
            "origin_binding": origin,
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="workflow.research.adopted",
        id="evt-adopted",
        task_id="TASK-1",
        payload={
            "project_id": "proj-a",
            "conversation_id": "kanban:proj-a",
            "thread_key": "thread-a",
            "request_id": "REQ-1",
            "request_revision": 1,
            "result_event_id": "evt-result",
            "artifact_ref": "research/TASK-1/result.md",
            "artifact_digest": "a" * 64,
            "summary": "Evidence adopted.",
            "source_event_id": "evt-result",
            "origin_binding": origin,
        },
    ))
    _write_line(state_dir / "events.jsonl", ZfEvent(
        type="workflow.result.available",
        id="evt-channel-result",
        task_id="TASK-2",
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "origin_surface": "channel",
            "channel_id": "ch-other",
            "request_id": "REQ-2",
            "request_revision": 1,
            "task_id": "TASK-2",
            "workflow_run_id": "wf-2",
            "terminal_event_id": "evt-terminal-2",
            "artifact_ref": "research/TASK-2/result.md",
            "artifact_digest": "b" * 64,
            "summary": "Other Channel result.",
            "origin_binding": {
                **origin,
                "surface": "channel",
                "channel_id": "ch-other",
                "thread_id": "main",
                "conversation_id": "",
                "thread_key": "",
            },
        },
    ))
    read_model.rebuild(state_dir)

    page = read_model.agent_session_history(
        state_dir,
        surface="kanban_agent",
        thread_id="thread-a",
        project_id="proj-a",
        conversation_id="kanban:proj-a",
        limit=20,
    )

    assert page is not None
    assert [item["id"] for item in page["items"]] == [
        "evt-result",
        "evt-adopted",
    ]


def test_event_log_size_rotation_preserves_read_all_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", "1")
    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    log.append(ZfEvent(type="first", id="evt-first"))
    log.append(ZfEvent(type="second", id="evt-second"))

    archives = sorted((tmp_path / ".zf" / "events").glob("*.jsonl"))
    assert archives
    assert archives[0].name.endswith("-0001.jsonl")
    assert [event.type for event in EventLog(tmp_path / ".zf" / "events.jsonl").read_all()] == [
        "first",
        "second",
    ]
    assert [segment.kind for segment in build_event_manifest(tmp_path / ".zf").segments] == [
        "archive",
        "active",
    ]


def test_projection_cli_rebuild_and_status(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n', encoding="utf-8")
    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["emit", "task.created", "--task", "TASK-1"]) == 0
    capsys.readouterr()

    assert main(["projection", "rebuild", "--json"]) == 0
    rebuild = json.loads(capsys.readouterr().out)
    assert rebuild["overall_state"] == "ready"
    assert rebuild["projections"]["event-index"]["source_seq"] >= 1
    assert rebuild["projections"]["artifact-catalog"]["projection_state"] == "ready"

    assert main(["projection", "status", "--count-source", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["schema_version"] == "projection-status.v2"
    event_index = status["projections"]["event-index"]
    assert event_index["source_cursor"]["schema_version"] == "event-segment-cursor.v1"
    assert event_index["projection_lag"] == 0
    assert status["projections"]["artifact-catalog"]["projection_state"] == "ready"


def test_projection_cli_reports_catalog_ready_event_index_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n',
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["emit", "task.created", "--task", "TASK-1"]) == 0
    capsys.readouterr()

    assert main([
        "projection",
        "rebuild",
        "--projection",
        "artifact-catalog",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert main(["projection", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)

    assert status["overall_state"] == "missing"
    assert status["projections"]["event-index"]["projection_state"] == "missing"
    assert status["projections"]["artifact-catalog"]["projection_state"] == "ready"


def test_payload_slim_keeps_proposal_object() -> None:
    """kanban.agent.action.proposed carries the proposal under `proposal`;
    slimming it away leaves the Triage Accept card with nothing to run
    (feishu-proposal e2e finding)."""
    from zf.web.projections.read_model import _payload_slim

    slim = _payload_slim({
        "turn_id": "evt-1",
        "conversation_id": "feishu-kanban_agent-oc_x",
        "proposal": {
            "action": "create-task",
            "valid": True,
            "payload": {"title": "赛车 MVP", "contract": {"behavior": "b", "verification": "v"}},
        },
    })
    assert slim["proposal"]["action"] == "create-task"
    assert slim["proposal"]["payload"]["title"] == "赛车 MVP"


def test_payload_slim_keeps_plan_interaction_objects() -> None:
    from zf.web.projections.read_model import _payload_slim

    slim = _payload_slim({
        "plan_request": {
            "request_id": "plan-route",
            "question": "Which route?",
            "options": [{"id": "research", "label": "Research"}],
        },
        "plan_response": {
            "request_event_id": "evt-plan",
            "option_id": "research",
            "answer": "Research",
        },
        "request_id": "plan-route",
        "revision": 2,
        "answers": [
            {
                "question_id": "route",
                "option_id": "research",
                "answer": "Research",
            },
            {
                "question_id": "evidence",
                "option_id": "focused",
                "answer": "Focused",
            },
        ],
    })

    assert slim["plan_request"]["question"] == "Which route?"
    assert slim["plan_response"]["request_event_id"] == "evt-plan"
    assert slim["request_id"] == "plan-route"
    assert slim["revision"] == 2
    assert len(slim["answers"]) == 2
    assert slim["answers"][1]["question_id"] == "evidence"


def test_payload_slim_keeps_message_event_id() -> None:
    """kanban.agent.turn.* rows link to their user.message via
    message_event_id; slimming it away split the question and answer into
    two separate turns in the web timeline (operator report 2026-07-16)."""
    from zf.web.projections.read_model import _payload_slim

    slim = _payload_slim({
        "turn_id": "turn-1",
        "thread_key": "kanban:proj-x",
        "message_event_id": "evt-user-msg",
    })
    assert slim["message_event_id"] == "evt-user-msg"


def test_payload_slim_keeps_permission_escalation_identity() -> None:
    from zf.web.projections.read_model import _payload_slim

    slim = _payload_slim({
        "permission_profile": "dangerous_full",
        "permission_escalation_retry_for": "evt-sandbox-failed",
    })

    assert slim == {
        "permission_profile": "dangerous_full",
        "permission_escalation_retry_for": "evt-sandbox-failed",
    }


def test_payload_slim_keeps_worker_state_identity_and_transition() -> None:
    from zf.web.projections.read_model import _payload_slim

    slim = _payload_slim({
        "instance_id": "delivery_worker",
        "from": "idle",
        "to": "busy",
        "reason": "dispatched fanout child",
    })

    assert slim["instance_id"] == "delivery_worker"
    assert slim["from"] == "idle"
    assert slim["to"] == "busy"
