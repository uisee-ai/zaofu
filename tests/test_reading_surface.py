"""X15:reading-surface 组(动作分面 manifest / zf ctx / consumer cursor)。"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.consumer_cursor import ConsumerCursorStore
from zf.runtime.task_context_manifest import (
    build_task_context_manifest,
    missing_required_refs,
    read_task_context_manifest,
    write_task_context_manifest,
)


class _Contract:
    scope = ["src/auth.py", "tests/test_auth.py"]
    behavior = "JWT 校验通过且过期拒绝"


class _Task:
    id = "T-1"
    title = "auth"
    contract = _Contract()
    payload = {"instruction_ref": "skills/auth/impl.md"}


class TestFacetedManifest:
    def test_four_facets_built(self, tmp_path):
        docs = tmp_path / "task_docs" / "T-1"
        docs.mkdir(parents=True)
        (docs / "source.md").write_text("s")
        (docs / "task.md").write_text("t")
        m = build_task_context_manifest(
            task=_Task(), dispatch_id="d1", state_dir=tmp_path,
            payload=_Task.payload,
        )
        assert set(m["contexts"]) == {"implement", "check", "research", "closeout"}
        kinds = [e["kind"] for e in m["contexts"]["implement"]]
        assert "source_doc" in kinds and "payload_ref" in kinds
        assert any(e["kind"] == "inline" for e in m["contexts"]["check"])

    def test_missing_required_named(self, tmp_path):
        m = build_task_context_manifest(
            task=_Task(), dispatch_id="d1", state_dir=tmp_path,
            payload={"instruction_ref": "skills/none.md"},
        )
        missing = missing_required_refs(m)
        assert any("skills/none.md" in x for x in missing)
        assert any("source.md" in x for x in missing)
        assert any("task.md" in x for x in missing)

    def test_write_read_roundtrip(self, tmp_path):
        m = build_task_context_manifest(
            task=_Task(), dispatch_id="d1", state_dir=tmp_path,
        )
        bdir = tmp_path / "briefings" / "T-1" / "d1"
        write_task_context_manifest(m, briefing_dir=bdir)
        back = read_task_context_manifest(bdir)
        assert back["schema_version"] == "task-context-manifest.v1"
        assert back["task_id"] == "T-1"

    def test_gap_event_declares_observe_first_non_blocking(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "src/zf/runtime/orchestrator_dispatch.py"
        ).read_text(encoding="utf-8")
        assert '"mode": "observe_first"' in src
        assert '"blocking": False' in src
        assert "task.context_manifest.gap" in src


class TestConsumerCursor:
    def test_advance_and_get(self, tmp_path):
        store = ConsumerCursorStore(tmp_path)
        store.advance("orchestrator", consumer_kind="kernel",
                      event_id="evt-1", event_ts="t1")
        c = store.advance("orchestrator", event_id="evt-2", event_ts="t2")
        assert c.events_seen == 2 and c.last_seen_event_id == "evt-2"
        got = store.get("orchestrator")
        assert got.consumer_kind == "kernel"

    def test_reset_is_explicit_rebuild_semantics(self, tmp_path):
        store = ConsumerCursorStore(tmp_path)
        store.advance("a", event_id="e1")
        store.advance("b", event_id="e1")
        store.reset("a")
        assert store.get("a") is None and store.get("b") is not None
        store.reset()
        assert store.get("b") is None  # 全量归零重读 = 可丢弃投影语义


class TestZfCtx:
    def test_build_context_aggregates(self, tmp_path):
        from zf.core.task.store import TaskStore
        from zf.core.events.log import EventLog
        from zf.core.events.writer import EventWriter
        from zf.cli.ctx import build_context

        from zf.core.task.schema import Task
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T-9", title="demo"))
        w = EventWriter(EventLog(tmp_path / "events.jsonl"))
        w.append(ZfEvent(type="task.dispatched", task_id="T-9",
                         payload={"dispatch_id": "d9", "assignee": "dev"}))
        docs = tmp_path / "task_docs" / "T-9"
        docs.mkdir(parents=True)
        (docs / "task.md").write_text("x")
        ctx = build_context(tmp_path, "T-9")
        assert ctx["dispatch"]["dispatch_id"] == "d9"
        assert "task" in ctx["capsule"]
        assert ctx["recent_events"][-1]["type"] == "task.dispatched"

    def test_missing_task_is_error(self, tmp_path):
        from zf.cli.ctx import build_context
        from zf.core.task.store import TaskStore
        TaskStore(tmp_path / "kanban.json")  # 空板
        ctx = build_context(tmp_path, "NOPE")
        assert "error" in ctx
