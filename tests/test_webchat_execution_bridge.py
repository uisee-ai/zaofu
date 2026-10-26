"""Implicit operator continuations inherit Write unless a profile is explicit."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.provider_permissions import normalize_kanban_permission_profile
from zf.web import webchat_execution_bridge as bridge


@pytest.mark.parametrize("value,expected", [
    (None, "workspace_writer"), ("", "workspace_writer"), ("  ", "workspace_writer"),
    ("read_only", "read_only"), ("dangerous_full", "dangerous_full"),
    ("unknown-profile", "read_only"),
])
def test_operator_permission_default_and_explicit_profiles(value, expected):
    assert normalize_kanban_permission_profile(value) == expected


@pytest.mark.parametrize("surface", ["resume", "recovery", "queue"])
@pytest.mark.parametrize("profile,expected", [(None, "workspace_writer"), ("read_only", "read_only"), ("workspace_writer", "workspace_writer")])
def test_implicit_dispatch_keeps_effective_write_policy(tmp_path, monkeypatch, surface, profile, expected):
    state = tmp_path / "runtime-state"
    state.mkdir()
    monkeypatch.setenv("ZF_KANBAN_WEBCHAT_RUNTIME", "durable")
    request = {"message": "continue the task"}
    if profile is not None:
        request["permission_profile"] = profile
    turn = {"turn_id": "turn-a", "project_id": "project-a", "conversation_id": "chat-a", "thread_id": "main",
            "backend": "claude-headless", "terminal_reason": "lost_owner_lease_expired",
            "request": {"payload": request}}
    handler = Mock(return_value={"ok": True})
    if surface == "resume":
        context = SimpleNamespace(state_dir=state, project_root=tmp_path, config=None)
        bridge.resume_durable_webchat_turn(context, turn, {"response": {"answer": "continue"}}, chat_handler=handler)
    elif surface == "recovery":
        store = SimpleNamespace(list_turns=Mock(return_value=[turn]))
        monkeypatch.setattr(bridge, "AgentTurnStore", lambda _: store)
        assert bridge.schedule_durable_webchat_recovery(
            state_dir=state, project_root=tmp_path, config=None, chat_handler=handler,
        ) == 1
    else:
        item = {"queue_id": "queue-a", "claimed_turn_id": "turn-next", "message": "continue", "request": request}
        store = SimpleNamespace(claim_next_queue=Mock(return_value=item), finish_queue=Mock())
        monkeypatch.setattr(bridge, "AgentTurnStore", lambda _: store)
        result = bridge.safe_dispatch_next_durable_webchat_queue(
            state_dir=state, writer=EventWriter(EventLog(state / "events.jsonl")), durable_run=turn,
            backend="claude-headless", permission_profile="", project_root=tmp_path,
            config=None, chat_handler=handler,
        )
        assert result["result"]["ok"]
    handler.assert_called_once()
    assert handler.call_args.kwargs["payload"]["permission_profile"] == expected
