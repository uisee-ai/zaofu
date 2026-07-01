"""Feishu bridge integration test — mock transport end-to-end."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.integrations.feishu.transport import MockFeishuTransport, FeishuWebhookEvent
from zf.integrations.feishu.gateway import CommandGateway, AuthLevel, FeishuCommandEnvelope
from zf.integrations.feishu.projection import ProjectionRouter, RoutingConfig
from zf.integrations.feishu.queries import QueryExecutor
from zf.integrations.feishu.controls import ControlHandler
from zf.integrations.feishu.views import TaskView, AlertView, SummaryView


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "feishu-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


class TestPushNotifications:
    def test_must_push_events(self, project: Path):
        transport = MockFeishuTransport()
        routing = RoutingConfig(channels={
            "progress": "ch_progress",
            "alert": "ch_alert",
            "approval": "ch_approval",
        })
        router = ProjectionRouter(transport, routing, project / ".zf")

        # human.escalate should push to approval channel
        event = ZfEvent(type="human.escalate", actor="orch", task_id="T1",
                        payload={"reason": "need help"})
        assert router.should_push(event)
        result = router.route_event(event)
        assert result is True
        assert len(transport.sent_messages) == 1
        assert transport.sent_messages[0].chat_id == "ch_approval"

    def test_non_push_events_ignored(self, project: Path):
        transport = MockFeishuTransport()
        routing = RoutingConfig(channels={"progress": "ch1"})
        router = ProjectionRouter(transport, routing, project / ".zf")

        event = ZfEvent(type="gate.started", actor="zf-cli")
        assert not router.should_push(event)

    def test_summary_view(self, project: Path):
        transport = MockFeishuTransport()
        routing = RoutingConfig()
        router = ProjectionRouter(transport, routing, project / ".zf")

        store = TaskStore(project / ".zf" / "kanban.json")
        store.add(Task(title="A", status="done"))
        store.add(Task(title="B", status="in_progress"))

        summary = router.build_summary()
        assert summary.done == 1
        assert summary.in_progress == 1


class TestCommandGateway:
    def test_parse_text_command(self):
        gw = CommandGateway()
        event = FeishuWebhookEvent(
            event_type="message",
            payload={"text": "/zf status"},
            user_id="u1",
            chat_id="c1",
        )
        envelope = gw.parse(event)
        assert envelope is not None
        assert envelope.command == "status"

    def test_parse_text_with_args(self):
        gw = CommandGateway()
        event = FeishuWebhookEvent(
            event_type="message",
            payload={"text": "/zf task TASK-001"},
            user_id="u1",
        )
        envelope = gw.parse(event)
        assert envelope.command == "task"
        assert envelope.args == ["TASK-001"]

    def test_reject_unknown_command(self):
        gw = CommandGateway()
        event = FeishuWebhookEvent(
            event_type="message",
            payload={"text": "/zf hack"},
            user_id="u1",
        )
        assert gw.parse(event) is None

    def test_authz_viewer_can_query(self):
        gw = CommandGateway(user_levels={"u1": AuthLevel.VIEWER})
        env = FeishuCommandEnvelope(command="status", user_id="u1")
        assert gw.is_authorized(env) is True

    def test_authz_viewer_cannot_control(self):
        gw = CommandGateway(user_levels={"u1": AuthLevel.VIEWER})
        env = FeishuCommandEnvelope(command="pause", user_id="u1")
        assert gw.is_authorized(env) is False

    def test_authz_operator_can_control(self):
        gw = CommandGateway(user_levels={"u1": AuthLevel.OPERATOR})
        env = FeishuCommandEnvelope(command="pause", user_id="u1")
        assert gw.is_authorized(env) is True

    def test_dedup(self):
        gw = CommandGateway()
        env = FeishuCommandEnvelope(command="status", idempotency_key="key1")
        assert gw.is_duplicate(env) is False
        assert gw.is_duplicate(env) is True  # second time is duplicate

    def test_button_action(self):
        gw = CommandGateway()
        event = FeishuWebhookEvent(
            event_type="button_action",
            payload={"action": "retry:TASK-001"},
            user_id="u1",
        )
        envelope = gw.parse(event)
        assert envelope is not None
        assert envelope.command == "retry"
        assert envelope.args == ["TASK-001"]


class TestQueryExecutor:
    def test_status_query(self, project: Path):
        store = TaskStore(project / ".zf" / "kanban.json")
        store.add(Task(title="A", status="done"))
        store.add(Task(title="B", status="in_progress"))

        executor = QueryExecutor(project / ".zf")
        env = FeishuCommandEnvelope(command="status")
        result = executor.execute(env)
        assert "2 total" in result
        assert "1 active" in result

    def test_tasks_query(self, project: Path):
        store = TaskStore(project / ".zf" / "kanban.json")
        store.add(Task(title="Build auth", id="T1"))
        executor = QueryExecutor(project / ".zf")
        result = executor.execute(FeishuCommandEnvelope(command="tasks"))
        assert "Build auth" in result

    def test_cost_query(self, project: Path):
        executor = QueryExecutor(project / ".zf")
        result = executor.execute(FeishuCommandEnvelope(command="cost"))
        assert "$" in result


class TestControlHandler:
    def test_pause(self, project: Path):
        handler = ControlHandler(project / ".zf")
        result = handler.execute(FeishuCommandEnvelope(command="pause", user_id="u1"))
        assert "pause" in result.lower()

        events = EventLog(project / ".zf" / "events.jsonl").read_all()
        assert any(e.type == "loop.pause_requested" for e in events)

    def test_retry(self, project: Path):
        handler = ControlHandler(project / ".zf")
        result = handler.execute(
            FeishuCommandEnvelope(command="retry", args=["T1"], user_id="u1"))
        assert "T1" in result

    def test_note(self, project: Path):
        handler = ControlHandler(project / ".zf")
        result = handler.execute(
            FeishuCommandEnvelope(command="note", args=["important", "info"], user_id="u1"))
        assert "Note" in result

    def test_serial_execution(self, project: Path):
        handler = ControlHandler(project / ".zf")
        # Execute multiple commands
        handler.execute(FeishuCommandEnvelope(command="pause", user_id="u1"))
        handler.execute(FeishuCommandEnvelope(command="resume", user_id="u1"))

        events = EventLog(project / ".zf" / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "loop.pause_requested" in types
        assert "loop.resume_requested" in types
