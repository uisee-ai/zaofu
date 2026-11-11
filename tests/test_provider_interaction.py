from pathlib import Path
from unittest.mock import Mock

import pytest

from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.provider_interaction import (
    ProviderInteractionRequired,
    has_codex_model_switch_prompt,
)
from zf.runtime.transport import DispatchContext, TmuxTransport
from tests.test_scheduler_task_attempt import _runtime, _send
from tests.test_tmux_transport_lifecycle import _FakeTmux


# Visible chooser captured in the isolated multi-task PRD run on 2026-11-11.
CHOOSER = """
  Approaching rate limits
  Switch to gpt-6-luna for lower credit usage?

› 1. Switch to gpt-6-luna                   Fast and affordable model for easier tasks.
  2. Keep current model
  3. Keep current model (never show again)  Hide future rate limit reminders about switching models

  enter select · esc back
"""


@pytest.mark.parametrize("screen", [
    "⚠ weekly limit: only 5% left · /status",
    CHOOSER + "\n› Ask Codex to do anything\n",
    "Approaching rate limits\nKeep current model\n",
    "enter select · esc back",
])
def test_notice_or_historical_chooser_is_not_active(screen):
    assert not has_codex_model_switch_prompt(screen)


def _transport(monkeypatch):
    tmux = _FakeTmux()
    tmux.capture_pane = Mock(return_value=CHOOSER)
    tmux.clear_input = Mock()
    tmux.send_paste = Mock()
    transport = TmuxTransport(tmux)
    monkeypatch.setattr(transport, "_agent_process_liveness", lambda role: (True, ""))
    return transport, tmux


def test_active_chooser_receives_no_input(tmp_path, monkeypatch):
    transport, tmux = _transport(monkeypatch)
    with pytest.raises(ProviderInteractionRequired, match="no input was sent"):
        transport.send_task("critic", tmp_path / "brief.md", "read briefing",
                            context=DispatchContext(backend="codex"))
    tmux.capture_pane.assert_called_once_with("critic", lines=0)
    tmux.clear_input.assert_not_called()
    tmux.send_paste.assert_not_called()
    assert not tmux.sent


def test_runtime_records_human_owned_failure_without_sent_event(tmp_path: Path, monkeypatch):
    runtime, _, state = _runtime(tmp_path)
    runtime.config.roles[0].backend = "codex"
    runtime.transport, tmux = _transport(monkeypatch)
    with pytest.raises(ProviderInteractionRequired):
        _send(runtime, state, dispatch_id="disp-1")
    row = TaskAttemptStore(state / "task_attempts.json").current_for_task("TASK-1")
    assert row["status"] == "failed"
    assert row["failure_class"] == "transport_interaction"
    assert row["retryable"] is False
    assert row["recovery_owner"] == "human"
    events = runtime.event_log.read_all()
    assert any(e.type == "task.attempt.failed" for e in events)
    assert not any(e.type == "task.attempt.sent" for e in events)
    tmux.clear_input.assert_not_called()
    tmux.send_paste.assert_not_called()
