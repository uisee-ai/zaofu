"""Projection-specific rebuild tests.

These checks prove only that cost and the terminal-task index can be rebuilt
from their declared inputs. They do not prove that EventLog can rebuild every
canonical runtime store; see design 142.
"""

from __future__ import annotations

import json

from zf.core.cost.tracker import CostTracker
from zf.core.events.model import ZfEvent


def _usage_event(actor: str, inp: int, out: int) -> ZfEvent:
    return ZfEvent(
        type="agent.usage", actor=actor,
        payload={"usage": {"input_tokens": inp, "output_tokens": out},
                 "backend": "mock"},
    )


class TestCostRebuild:
    def test_rebuild_equals_incremental_aggregates(self, tmp_path):
        live = CostTracker(tmp_path / "cost.jsonl")
        events = [
            _usage_event("dev-1", 1000, 200),
            _usage_event("dev-2", 500, 100),
            _usage_event("review", 300, 50),
        ]
        from zf.runtime.housekeeping import apply_agent_usage_event
        for e in events:
            apply_agent_usage_event(live, e)

        rebuilt = CostTracker.rebuild_from_events(
            events, tmp_path / "cost.rebuilt.jsonl",
        )
        assert rebuilt.per_role_totals() == live.per_role_totals()
        assert rebuilt.per_instance_totals() == live.per_instance_totals()

    def test_rebuild_ignores_non_usage_events(self, tmp_path):
        events = [
            ZfEvent(type="task.done", actor="dev"),
            _usage_event("dev", 100, 10),
        ]
        rebuilt = CostTracker.rebuild_from_events(
            events, tmp_path / "cost.jsonl",
        )
        lines = (tmp_path / "cost.jsonl").read_text().splitlines()
        assert len(lines) == 1


class TestTerminalIndexRebuild:
    def test_rebuild_from_archive(self, tmp_path):
        from zf.core.task.store import TaskStore
        store = TaskStore(tmp_path / "kanban.json")
        archive = tmp_path / "kanban"
        archive.mkdir()
        (archive / "2026-06-10.json").write_text(json.dumps(
            {"tasks": [{"id": "T-1"}, {"id": "T-2"}]},
        ))
        (archive / "2026-06-11.json").write_text(json.dumps(
            {"tasks": [{"id": "T-3"}]},
        ))
        index = store.rebuild_terminal_index_from_archive()
        assert index == {"T-1": "2026-06-10", "T-2": "2026-06-10",
                         "T-3": "2026-06-11"}
        # 落盘且可经私有 loader 读回(丢弃-重建闭环)
        assert store._load_terminal_index() == index
