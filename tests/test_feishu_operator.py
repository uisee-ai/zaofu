"""Tests for the Feishu operator agent (``zf feishu operate``).

The operator agent is a thin consumer of ``user.message`` events that
``zf feishu handle`` (the ``/zf ask`` path) normalizes with
``target=feishu-operator-agent``. It drives a headless backend (which calls the
``zf`` CLI to read state) and replies to the originating Feishu chat. All tests
run offline with a fake backend + ``MockFeishuTransport`` — no real LLM or
Feishu API.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from zf.cli.feishu import (
    build_feishu_operator_system_prompt,
    feishu_operator_requests,
    run_operate,
)
from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.integrations.feishu.transport import MockFeishuTransport


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "feishu-operate-test", "state_dir": "runtime-state"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config), encoding="utf-8")
    assert main(["init"]) == 0
    return tmp_path


def _feishu_user_message(
    message: str = "当前 blocker 是什么？",
    *,
    chat_id: str = "c1",
    user_id: str = "u1",
    message_id: str = "m-ask",
) -> ZfEvent:
    return ZfEvent(
        type="user.message",
        actor=f"feishu:{user_id}",
        payload={
            "source": "feishu",
            "target": "feishu-operator-agent",
            "message": message,
            "chat_id": chat_id,
            "user_id": user_id,
            "message_id": message_id,
        },
    )


class _FakeResult:
    def __init__(self, *, ok: bool = True, reply: str = "", error: str = "") -> None:
        self.ok = ok
        self.reply = reply
        self.error = error
        self.backend = "fake-headless"
        self.provider_session_id = "sess-fake"
        self.status = "completed" if ok else "failed"
        self.usage: dict = {}


class _FakeBackend:
    def __init__(self, result: _FakeResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def run_turn(self, **kwargs) -> _FakeResult:
        self.calls.append(kwargs)
        return self._result


def _operate_args(state_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        state_dir=str(state_dir),
        transport="mock",
        from_beginning=False,
        backend="codex-headless",
    )


# --- Acceptance 1: request filter -----------------------------------------


def test_feishu_operator_requests_filters_target() -> None:
    events = [
        _feishu_user_message(message="hi", message_id="m1"),
        ZfEvent(
            type="user.message",
            actor="cli",
            payload={"source": "cli", "message": "web ask"},
        ),  # not feishu
        ZfEvent(
            type="user.message",
            actor="feishu:u2",
            payload={"source": "feishu", "target": "other", "message": "x"},
        ),  # wrong target
        ZfEvent(type="dev.build.done", actor="dev", payload={}),  # not user.message
    ]
    reqs = feishu_operator_requests(events)
    assert len(reqs) == 1
    assert reqs[0].message == "hi"
    assert reqs[0].chat_id == "c1"
    assert reqs[0].user_id == "u1"
    assert reqs[0].message_id == "m1"


# --- Acceptance 2: operator system prompt ----------------------------------


def test_build_feishu_operator_system_prompt_guides_cli_readonly() -> None:
    sp = build_feishu_operator_system_prompt()
    assert "zf" in sp  # the agent reads state via the zf CLI
    # read-only / propose, not direct write of runtime truth
    assert any(token in sp for token in ("只读", "read-only", "不要直接写", "不写"))
    assert "中文" in sp  # default Chinese replies


# --- Acceptance 3: run_operate drain -> turn -> reply -> offset ------------


def test_run_operate_sends_reply_and_advances_offset(project: Path) -> None:
    state_dir = project / "runtime-state"
    EventLog(state_dir / "events.jsonl").append(
        _feishu_user_message(message="status?", chat_id="c9", message_id="mm1"),
    )
    transport = MockFeishuTransport()
    backend = _FakeBackend(_FakeResult(ok=True, reply="当前 2 个任务在 review。"))

    rc = run_operate(_operate_args(state_dir), backend=backend, transport=transport)

    assert rc == 0
    assert len(transport.sent_messages) == 1
    assert transport.sent_messages[0].chat_id == "c9"
    assert "review" in transport.sent_messages[0].content
    assert len(backend.calls) == 1
    assert "status?" in backend.calls[0]["prompt"]
    types = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
    assert "feishu.notification.sent" in types

    # Idempotent: re-running drains nothing new, no resend.
    rc2 = run_operate(_operate_args(state_dir), backend=backend, transport=transport)
    assert rc2 == 0
    assert len(transport.sent_messages) == 1


def test_run_operate_skips_non_operator_events(project: Path) -> None:
    state_dir = project / "runtime-state"
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="dev.build.done", actor="dev", payload={}),
    )
    transport = MockFeishuTransport()
    backend = _FakeBackend(_FakeResult(ok=True, reply="x"))

    rc = run_operate(_operate_args(state_dir), backend=backend, transport=transport)

    assert rc == 0
    assert transport.sent_messages == []
    assert backend.calls == []


def test_run_operate_failed_turn_emits_failed_without_send(project: Path) -> None:
    state_dir = project / "runtime-state"
    EventLog(state_dir / "events.jsonl").append(
        _feishu_user_message(message="hi", chat_id="c5"),
    )
    transport = MockFeishuTransport()
    backend = _FakeBackend(_FakeResult(ok=False, error="provider timeout"))

    rc = run_operate(_operate_args(state_dir), backend=backend, transport=transport)

    assert rc == 0
    assert transport.sent_messages == []
    types = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
    assert "feishu.notification.failed" in types


# --- Acceptance 4: subcommand registration ---------------------------------


def test_feishu_operate_subcommand_registered(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["feishu", "operate", "--help"])
    out = capsys.readouterr().out
    assert "operate" in out
