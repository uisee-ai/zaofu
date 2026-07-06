"""doc 106 D axis (visibility batch #1): oversized codex hook tool_response
is externalized to a sidecar ref instead of head-truncated — pytest/green
markers live at the TAIL and the reactor green-check reads them."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from zf.cli.hook_recv import run as hook_recv_run
from zf.core.events.log import EventLog
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _invoke(state_dir: Path, payload: dict, monkeypatch) -> int:
    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": staticmethod(lambda: json.dumps(payload))})(),
    )
    args = argparse.Namespace(
        event="codex.hook.post_tool_use",
        state_dir=str(state_dir),
        backend="codex",
    )
    return hook_recv_run(args)


def _hook_event(state_dir: Path):
    events = EventLog(state_dir / "events.jsonl").read_all()
    matches = [e for e in events if e.type == "codex.hook.post_tool_use"]
    assert matches, [e.type for e in events]
    return matches[-1]


def test_small_tool_response_stays_inline(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _invoke(state_dir, {
        "session_id": "s1", "hook_event_name": "PostToolUse",
        "tool_name": "bash", "tool_input": {"command": "pytest -q"},
        "tool_response": "1 passed in 0.01s",
    }, monkeypatch)
    event = _hook_event(state_dir)
    assert event.payload["tool_response"] == "1 passed in 0.01s"
    assert "raw_output" not in (event.payload.get("refs") or {})


def test_large_tool_response_externalizes_and_keeps_tail(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    body = "collecting tests...\n" + ("test_module.py::test_case PASSED\n" * 400)
    tail = "==== 400 passed in 12.34s ===="
    raw = body + tail
    _invoke(state_dir, {
        "session_id": "s1", "hook_event_name": "PostToolUse",
        "tool_name": "bash", "tool_input": {"command": "pytest -q"},
        "tool_response": raw,
    }, monkeypatch)
    event = _hook_event(state_dir)
    inline = event.payload["tool_response"]
    assert inline != raw, "oversized response must not be inlined verbatim"
    assert tail in inline, "head+tail preview must keep the green summary tail"
    ref = event.payload["refs"]["raw_output"]
    assert hydrate_sidecar_ref(state_dir, ref).payload == raw
    assert raw not in (state_dir / "events.jsonl").read_text(encoding="utf-8")


def test_green_check_hydrates_externalized_response(tmp_path: Path, monkeypatch) -> None:
    from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
    from zf.core.events.model import ZfEvent
    from zf.core.state.session import SessionStore
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    (state_dir / "kanban.json").write_text("[]\n")

    raw = ("x" * 6000) + "\n==== 12 passed in 3.21s ===="
    _invoke(state_dir, {
        "session_id": "s1", "hook_event_name": "PostToolUse",
        "tool_name": "bash", "tool_input": {"command": "pytest tests/ -q"},
        "tool_response": raw,
    }, monkeypatch)
    event = _hook_event(state_dir)

    orch = Orchestrator(
        state_dir,
        ZfConfig(project=ProjectConfig(name="t"), session=SessionConfig(tmux_session="t"),
                 roles=[RoleConfig(name="dev", backend="mock")]),
        TmuxTransport(TmuxSession(session_name="t", dry_run=True)),
    )
    assert orch._provider_tool_response_looks_green(event.payload) is True

    # a red run stays red even when externalized
    red = ("y" * 6000) + "\n==== 3 failed, 9 errors in 3.21s ===="
    _invoke(state_dir, {
        "session_id": "s1", "hook_event_name": "PostToolUse",
        "tool_name": "bash", "tool_input": {"command": "pytest tests/ -q"},
        "tool_response": red,
    }, monkeypatch)
    assert orch._provider_tool_response_looks_green(_hook_event(state_dir).payload) is False
