"""Tests for zf chat CLI + user.message event (E2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    return tmp_path


def test_chat_emits_user_message_event(project: Path):
    result = main(["chat", "implement OAuth login"])
    assert result == 0
    log = EventLog(project / ".zf" / "events.jsonl")
    events = log.read_all()
    user_messages = [e for e in events if e.type == "user.message"]
    assert len(user_messages) == 1
    e = user_messages[0]
    assert e.actor == "human"
    assert e.payload.get("message") == "implement OAuth login"


def test_chat_message_with_spaces(project: Path):
    result = main(["chat", "please add a profile page with avatar upload"])
    assert result == 0
    log = EventLog(project / ".zf" / "events.jsonl")
    msg = next(e for e in log.read_all() if e.type == "user.message")
    assert "profile page" in msg.payload["message"]


def test_chat_records_actor_human(project: Path):
    main(["chat", "hello"])
    log = EventLog(project / ".zf" / "events.jsonl")
    msg = next(e for e in log.read_all() if e.type == "user.message")
    assert msg.actor == "human"


def test_chat_starts_correlation_trace(project: Path):
    main(["chat", "hello"])
    log = EventLog(project / ".zf" / "events.jsonl")
    msg = next(e for e in log.read_all() if e.type == "user.message")
    assert msg.correlation_id is not None
    assert msg.correlation_id.startswith("trace-")


def test_chat_prints_confirmation(project: Path, capsys):
    main(["chat", "do the thing"])
    out = capsys.readouterr().out
    assert "user.message" in out or "delivered" in out.lower() or "sent" in out.lower()


def test_chat_help_listed_in_main(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "chat" in out


def test_user_message_is_a_wake_pattern():
    """Verify the watcher wake_patterns includes user.message."""
    from zf.runtime.wake_patterns import WAKE_PATTERNS
    assert "user.message" in WAKE_PATTERNS


def test_chat_uses_project_state_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
    )

    result = main(["chat", "hello runtime"])

    assert result == 0
    log = EventLog(tmp_path / "runtime-state" / "events.jsonl")
    msg = next(e for e in log.read_all() if e.type == "user.message")
    assert msg.payload["message"] == "hello runtime"
    assert not (tmp_path / ".zf").exists()
