"""X17:mailbox 状态机 + Agent View 拓扑投影。"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.agent_mailbox import fold_mailbox, undelivered
from zf.runtime.projections.agent_view_topology import build_agent_topology

_REPO = Path(__file__).resolve().parent.parent


def _msg(etype, mid, **payload):
    return ZfEvent(type=etype, payload={"message_id": mid, **payload})


class TestMailbox:
    def test_lifecycle_sent_delivered_read(self):
        box = fold_mailbox([
            _msg("agent.message.sent", "m1", sender_run_id="orch",
                 recipient_run_ids=["dev-1"], subject="rework hint"),
            _msg("agent.message.delivered", "m1"),
            _msg("agent.message.read", "m1"),
        ])
        assert box["m1"]["state"] == "read"
        assert box["m1"]["recipients"] == ["dev-1"]
        assert box["m1"]["history"] == [
            "agent.message.sent", "agent.message.delivered",
            "agent.message.read",
        ]

    def test_illegal_jump_ignored(self):
        box = fold_mailbox([
            _msg("agent.message.read", "m2"),       # 未 sent 即 read:忽略
            _msg("agent.message.sent", "m2"),
            _msg("agent.message.read", "m2"),       # 跳过 delivered:忽略
        ])
        assert box["m2"]["state"] == "sent"

    def test_undelivered_replay_list(self):
        box = fold_mailbox([
            _msg("agent.message.sent", "a"),
            _msg("agent.message.sent", "b"),
            _msg("agent.message.delivered", "b"),
            _msg("agent.message.sent", "c"),
            _msg("agent.message.failed", "c"),
        ])
        assert undelivered(box) == ["a"]

    def test_no_existing_event_renamed(self):
        # ABI 边界:全新命名空间,模块不触碰既有事件名
        src = Path("src/zf/runtime/agent_mailbox.py").read_text()
        for legacy in ("dev.build.done", "fanout.child", "task.dispatched"):
            assert legacy not in src


class TestAgentTopology:
    def test_run_tree_with_status_and_activity(self):
        events = [
            ZfEvent(type="fanout.started", payload={
                "trace_id": "t1", "fanout_id": "f1", "stage_id": "impl",
                "expected_children": [
                    {"child_id": "c1", "role_instance": "dev-1"},
                    {"child_id": "c2", "role_instance": "dev-2"},
                ]}),
            ZfEvent(type="fanout.child.dispatched",
                    payload={"fanout_id": "f1", "child_id": "c1"}),
            ZfEvent(type="task.dispatched", task_id="T-7",
                    payload={"assignee": "dev-1"}),
            ZfEvent(type="worker.heartbeat", actor="dev-1"),
            ZfEvent(type="fanout.child.completed",
                    payload={"fanout_id": "f1", "child_id": "c1"}),
        ]
        topo = build_agent_topology(events)
        children = topo["runs"]["t1"]["fanouts"]["f1"]["children"]
        assert children["c1"]["status"] == "completed"
        assert children["c1"]["active_task"] == "T-7"
        assert "last_activity_ts" in children["c1"]
        assert children["c2"]["status"] == "expected"

    def test_k1_boundary_not_imported_by_orchestrator_files(self):
        for name in ("orchestrator.py", "orchestrator_dispatch.py",
                     "orchestrator_lifecycle.py"):
            src = (_REPO / "src/zf/runtime" / name).read_text(
                encoding="utf-8", errors="replace",
            )
            assert "agent_view_topology" not in src
            assert "agent_mailbox" not in src
