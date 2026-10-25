from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.web.kanban_turn_context import kanban_turn_context


def test_plan_repair_context_is_bound_to_headless_turn():
    writer = SimpleNamespace(event_log=SimpleNamespace(read_all=lambda: []))
    context = kanban_turn_context(
        writer,
        ZfEvent(id="evt-user", type="user.message", actor="web", correlation_id="trace-1"),
        {
            "plan_context": {
                "plan_id": "evt-plan",
                "task_map_ref": "artifacts/plan/task_map.json",
            },
            "intent_type": "plan_repair",
        },
        requested_action="chat-orchestrator",
        thread_key="main",
        turn_id="turn-1",
        run_thread_id="main",
        project_id="dataloop",
        conversation_id="kanban:dataloop",
        runtime_snapshot_ref="artifacts/runtime/snapshot.json",
    )

    assert context["plan_context"] == {
        "plan_id": "evt-plan",
        "task_map_ref": "artifacts/plan/task_map.json",
    }
    assert context["intent_type"] == "plan_repair"
