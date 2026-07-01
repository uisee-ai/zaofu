from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import zf.web.headless_agent as headless_agent
from fastapi.testclient import TestClient

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.web.headless_agent import (
    ClaudeHeadlessBackend,
    CodexHeadlessBackend,
    HeadlessMessage,
    HeadlessThreadStore,
    HeadlessTurnResult,
    KanbanHeadlessAgent,
    canonical_headless_backend,
)
from zf.web.agent_session_runtime import cancel_agent_session_run, run_key
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]", encoding="utf-8")
    (sd / "feature_list.json").write_text("[]", encoding="utf-8")
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    return sd


def _fake_claude_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "payload = sys.stdin.readline()",
                "capture = os.environ.get('FAKE_CLAUDE_CAPTURE')",
                "if capture:",
                "    open(capture, 'w', encoding='utf-8').write(payload)",
                "print(json.dumps({'type':'system','session_id':'claude-session-1'}), flush=True)",
                "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'headless answer'}]}}), flush=True)",
                "print(json.dumps({'type':'result','session_id':'claude-session-1','result':'final headless answer','usage':{'input_tokens':3,'output_tokens':4}}), flush=True)",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _fake_codex_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "expected_thinking = os.environ.get('FAKE_CODEX_THINKING_LEVEL')",
                "expected_sandbox = os.environ.get('FAKE_CODEX_SANDBOX', 'read-only')",
                "expect_resume_security = os.environ.get('FAKE_CODEX_EXPECT_RESUME_SECURITY')",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    msg = json.loads(line)",
                "    method = msg.get('method')",
                "    req_id = msg.get('id')",
                "    if req_id and method == 'initialize':",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
                "    elif method == 'initialized':",
                "        pass",
                "    elif req_id and method == 'thread/start':",
                "        assert msg['params']['approvalPolicy'] == 'never'",
                "        assert msg['params']['sandbox'] == expected_sandbox",
                "        if expected_thinking:",
                "            assert msg['params']['config']['model_reasoning_effort'] == expected_thinking",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-1'}}), flush=True)",
                "    elif req_id and method == 'thread/resume':",
                "        if expect_resume_security:",
                "            assert msg['params']['approvalPolicy'] == 'never'",
                "            assert msg['params']['sandbox'] == expected_sandbox",
                "            print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-resumed'}}), flush=True)",
                "        else:",
                "            print(json.dumps({'jsonrpc':'2.0','id':req_id,'error':{'code':-32000,'message':'missing thread'}}), flush=True)",
                "    elif req_id and method == 'turn/start':",
                "        if expected_thinking:",
                "            assert msg['params']['effort'] == expected_thinking",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turnId':'turn-1'}}), flush=True)",
                "        print(json.dumps({'jsonrpc':'2.0','method':'item/agentMessage/delta','params':{'delta':'codex '}}), flush=True)",
                "        print(json.dumps({'jsonrpc':'2.0','method':'item/completed','params':{'item':{'type':'agentMessage','text':'headless reply'}}}), flush=True)",
                "        print(json.dumps({'jsonrpc':'2.0','method':'thread/tokenUsage/updated','params':{'usage':{'input_tokens':5,'output_tokens':6}}}), flush=True)",
                "        print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'turn':{'status':'completed'}}}), flush=True)",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _fake_codex_patch_approval_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_codex_patch_approval.py"
    script.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "patch_path = os.environ['FAKE_CODEX_PATCH_PATH']",
                "expected_decision = os.environ['FAKE_CODEX_EXPECT_DECISION']",
                "approval_method = os.environ.get('FAKE_CODEX_APPROVAL_METHOD', 'applyPatchApproval')",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    msg = json.loads(line)",
                "    method = msg.get('method')",
                "    req_id = msg.get('id')",
                "    if req_id == 99 and not method:",
                "        assert msg.get('result', {}).get('decision') == expected_decision, msg",
                "        print(json.dumps({'jsonrpc':'2.0','method':'item/agentMessage/delta','params':{'delta':'approval handled'}}), flush=True)",
                "        print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'turn':{'status':'completed'}}}), flush=True)",
                "    elif req_id and method == 'initialize':",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
                "    elif method == 'initialized':",
                "        pass",
                "    elif req_id and method == 'thread/start':",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-1'}}), flush=True)",
                "    elif req_id and method == 'turn/start':",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turnId':'turn-1'}}), flush=True)",
                "        if approval_method == 'applyPatchApproval':",
                "            params = {'conversationId':'codex-thread-1','callId':'patch-1','fileChanges':{patch_path:{'add':{}}},'reason':'write channel artifact','grantRoot':None}",
                "        else:",
                "            params = {'threadId':'codex-thread-1','turnId':'turn-1','itemId':'item-1','startedAtMs':1,'reason':'write channel artifact','grantRoot':patch_path}",
                "        print(json.dumps({'jsonrpc':'2.0','id':99,'method':approval_method,'params':params}), flush=True)",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _wait_for_event_type(state_dir: Path, event_type: str, timeout_s: float = 3.0):
    deadline = time.monotonic() + timeout_s
    events = []
    while time.monotonic() < deadline:
        events = EventLog(state_dir / "events.jsonl").read_all()
        if any(event.type == event_type for event in events):
            return events
        time.sleep(0.05)
    return events


def test_claude_headless_turn_parses_reply_and_persists_thread(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    capture = tmp_path / "stdin.jsonl"
    script = _fake_claude_script(tmp_path)
    monkeypatch.setenv("FAKE_CLAUDE_CAPTURE", str(capture))
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")

    agent = KanbanHeadlessAgent(state_dir=state_dir, project_root=state_dir.parent)
    result = agent.run_turn(
        backend="claude-headless",
        message="Explain TASK-1",
        task_id="TASK-1",
    )

    assert result.ok is True
    assert result.backend == "claude-headless"
    assert result.provider_session_id == "claude-session-1"
    assert result.reply == "final headless answer"
    assert result.usage == {"input_tokens": 3, "output_tokens": 4}
    assert result.permission_snapshot["backend"] == "claude-headless"
    assert result.permission_snapshot["permission_profile"] == "read_only"
    assert result.permission_snapshot["permission_mode"] == "default"
    assert result.permission_drift == {"status": "ok", "items": []}
    sent = json.loads(capture.read_text(encoding="utf-8"))
    assert sent["type"] == "user"
    assert sent["message"]["content"][0]["text"]

    stored = HeadlessThreadStore(
        state_dir=state_dir,
        project_root=state_dir.parent,
    ).load(scope="project", task_id="TASK-1")
    assert stored["providers"]["claude-headless"]["provider_session_id"] == "claude-session-1"
    assert stored["providers"]["claude-headless"]["permission_snapshot"]["permission_mode"] == "default"
    assert stored["last_reply"] == "final headless answer"


def test_claude_headless_streams_before_process_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ack = tmp_path / "stream-ack"
    script = tmp_path / "fake_claude_stream_wait.py"
    script.write_text(
        "\n".join([
            "import json, os, sys, time",
            "ack = os.environ['FAKE_CLAUDE_STREAM_ACK']",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'claude-stream-1'}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'thinking','text':'private reasoning'}, {'type':'text','text':'streamed text'}]}}), flush=True)",
            "deadline = time.time() + 2",
            "while time.time() < deadline and not os.path.exists(ack):",
            "    time.sleep(0.02)",
            "if not os.path.exists(ack):",
            "    raise SystemExit(9)",
            "print(json.dumps({'type':'result','session_id':'claude-stream-1','result':'final streamed answer'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CLAUDE_STREAM_ACK", str(ack))
    backend = ClaudeHeadlessBackend(command=f"{sys.executable} {script}")
    messages: list[HeadlessMessage] = []

    def on_message(message: HeadlessMessage) -> None:
        messages.append(message)
        if message.type == "text" and "streamed text" in message.content:
            ack.write_text("ok", encoding="utf-8")

    result = backend.run_turn(
        prompt="stream",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=on_message,
        timeout_s=5,
    )

    assert result.ok is True
    assert result.reply == "final streamed answer"
    assert [message.type for message in messages] == ["status", "thinking", "text"]
    thinking = messages[1]
    assert thinking.content == "thinking"
    assert thinking.raw == {"type": "thinking", "redacted": True}


def test_claude_headless_timeout_preserves_partial_stream(
    tmp_path: Path,
):
    script = tmp_path / "fake_claude_timeout.py"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'claude-timeout-1'}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'partial text'}]}}), flush=True)",
            "time.sleep(5)",
        ]),
        encoding="utf-8",
    )
    backend = ClaudeHeadlessBackend(command=f"{sys.executable} {script}")
    messages: list[HeadlessMessage] = []

    result = backend.run_turn(
        prompt="timeout",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=messages.append,
        timeout_s=1,
    )

    assert result.ok is False
    assert result.status == "timeout"
    assert result.provider_session_id == "claude-timeout-1"
    assert result.reply == "partial text"
    assert [message.type for message in messages] == ["status", "text"]


def test_codex_headless_turn_uses_app_server_protocol(
    tmp_path: Path,
):
    script = _fake_codex_script(tmp_path)
    backend = CodexHeadlessBackend(command=f"{sys.executable} {script}")
    sessions: list[str] = []

    result = backend.run_turn(
        prompt="Explain TASK-1",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=sessions.append,
        on_message=None,
        timeout_s=5,
    )

    assert result.ok is True
    assert result.backend == "codex-headless"
    assert result.provider_session_id == "codex-thread-1"
    assert sessions == ["codex-thread-1"]
    assert result.reply == "headless reply"
    assert result.usage == {"input_tokens": 5, "output_tokens": 6}


def test_codex_headless_completed_agent_message_does_not_duplicate_stream(
    tmp_path: Path,
):
    script = tmp_path / "fake_codex_cumulative.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "for line in sys.stdin:",
            "    if not line.strip():",
            "        continue",
            "    msg = json.loads(line)",
            "    method = msg.get('method')",
            "    req_id = msg.get('id')",
            "    if req_id and method == 'initialize':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
            "    elif method == 'initialized':",
            "        pass",
            "    elif req_id and method == 'thread/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-1'}}), flush=True)",
            "    elif req_id and method == 'turn/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turnId':'turn-1'}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'item/agentMessage/delta','params':{'delta':'headless reply'}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'item/completed','params':{'item':{'type':'agentMessage','text':'headless reply'}}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'turn':{'status':'completed'}}}), flush=True)",
        ]),
        encoding="utf-8",
    )
    backend = CodexHeadlessBackend(command=f"{sys.executable} {script}")
    messages: list[HeadlessMessage] = []

    result = backend.run_turn(
        prompt="Explain TASK-1",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=messages.append,
        timeout_s=5,
    )

    assert result.ok is True
    assert result.reply == "headless reply"
    assert [message.content for message in messages if message.type == "text"] == ["headless reply"]


def test_codex_headless_timeout_filters_nonfatal_bubblewrap_warning(
    tmp_path: Path,
):
    script = tmp_path / "fake_codex_timeout.py"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "print('ERROR codex_app_server: Codex could not find bubblewrap on PATH. Codex will use the bundled bubblewrap in the meantime.', file=sys.stderr, flush=True)",
            "for line in sys.stdin:",
            "    if not line.strip():",
            "        continue",
            "    msg = json.loads(line)",
            "    method = msg.get('method')",
            "    req_id = msg.get('id')",
            "    if req_id and method == 'initialize':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
            "    elif method == 'initialized':",
            "        pass",
            "    elif req_id and method == 'thread/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-1'}}), flush=True)",
            "    elif req_id and method == 'turn/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turn':{'id':'turn-1','status':'inProgress'}}}), flush=True)",
            "        time.sleep(5)",
        ]),
        encoding="utf-8",
    )
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")

    result = codex.run_turn(
        prompt="slow turn",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=0.2,
    )

    assert result.ok is False
    assert result.status == "timeout"
    assert "codex turn timed out after 0.2s" in result.error
    assert "bubblewrap" not in result.error


def test_codex_headless_streaming_turn_uses_idle_timeout_not_total_timeout(
    tmp_path: Path,
):
    script = tmp_path / "fake_codex_slow_stream.py"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "for line in sys.stdin:",
            "    if not line.strip():",
            "        continue",
            "    msg = json.loads(line)",
            "    method = msg.get('method')",
            "    req_id = msg.get('id')",
            "    if req_id and method == 'initialize':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
            "    elif method == 'initialized':",
            "        pass",
            "    elif req_id and method == 'thread/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-1'}}), flush=True)",
            "    elif req_id and method == 'turn/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turn':{'id':'turn-1','status':'inProgress'}}}), flush=True)",
            "        for part in ['slow ', 'stream ', 'done']:",
            "            time.sleep(0.12)",
            "            print(json.dumps({'jsonrpc':'2.0','method':'item/agentMessage/delta','params':{'delta':part}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'threadId':'codex-thread-1','turn':{'id':'turn-1','status':'completed'}}}), flush=True)",
        ]),
        encoding="utf-8",
    )
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")
    messages: list[HeadlessMessage] = []

    result = codex.run_turn(
        prompt="slow streaming turn",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=messages.append,
        timeout_s=0.2,
    )

    assert result.ok is True
    assert result.reply == "slow stream done"
    assert [message.content for message in messages if message.type == "text"] == [
        "slow ",
        "stream ",
        "done",
    ]


def test_codex_headless_failed_turn_is_not_reported_completed(
    tmp_path: Path,
):
    script = tmp_path / "fake_codex_failed_turn.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "for line in sys.stdin:",
            "    if not line.strip():",
            "        continue",
            "    msg = json.loads(line)",
            "    method = msg.get('method')",
            "    req_id = msg.get('id')",
            "    if req_id and method == 'initialize':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
            "    elif method == 'initialized':",
            "        pass",
            "    elif req_id and method == 'thread/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-1'}}), flush=True)",
            "    elif req_id and method == 'turn/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turn':{'id':'turn-1','status':'inProgress'}}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'threadId':'codex-thread-1','turn':{'id':'turn-1','status':'failed','error':{'message':'model failed','additionalDetails':'quota exhausted'}}}}), flush=True)",
        ]),
        encoding="utf-8",
    )
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")

    result = codex.run_turn(
        prompt="fail turn",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
    )

    assert result.ok is False
    assert result.status == "failed"
    assert "codex turn failed: model failed: quota exhausted" in result.error


def test_headless_backends_accept_thinking_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    claude = ClaudeHeadlessBackend(command=sys.executable)
    claude_args = claude.build_args(
        thread_id="zf-thread",
        system_prompt="system",
        thinking_level="high",
    )
    assert "--effort" in claude_args
    assert claude_args[claude_args.index("--effort") + 1] == "high"

    monkeypatch.setenv("FAKE_CODEX_THINKING_LEVEL", "low")
    script = _fake_codex_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")
    result = codex.run_turn(
        prompt="Explain TASK-1",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        thinking_level="low",
    )
    assert result.ok is True


def test_headless_permission_profiles_map_to_provider_security(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    claude = ClaudeHeadlessBackend(command=sys.executable)
    claude_args = claude.build_args(
        thread_id="zf-thread",
        system_prompt="system",
        permission_profile="dangerous_full",
    )
    assert "--permission-mode" in claude_args
    assert claude_args[claude_args.index("--permission-mode") + 1] == "bypassPermissions"

    monkeypatch.setenv("FAKE_CODEX_SANDBOX", "workspace-write")
    script = _fake_codex_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")
    result = codex.run_turn(
        prompt="write channel artifact",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        permission_profile="artifact_writer",
    )
    assert result.ok is True


def test_codex_headless_resume_reapplies_provider_security(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FAKE_CODEX_SANDBOX", "workspace-write")
    monkeypatch.setenv("FAKE_CODEX_EXPECT_RESUME_SECURITY", "1")
    script = _fake_codex_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")

    result = codex.run_turn(
        prompt="continue",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="old-codex-thread",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        permission_profile="project_writer",
    )

    assert result.ok is True
    assert result.resumed is True
    assert result.provider_session_id == "codex-thread-resumed"


def test_codex_headless_fails_fast_when_real_codex_sandbox_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_which(name: str) -> str | None:
        return {
            "unshare": "/usr/bin/unshare",
        }.get(name)

    def fake_run(argv, **kwargs):
        assert argv == ["/usr/bin/unshare", "-n", "true"]
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="unshare: unshare failed: Operation not permitted\n",
        )

    monkeypatch.setattr(headless_agent.shutil, "which", fake_which)
    monkeypatch.setattr(headless_agent.subprocess, "run", fake_run)
    codex = CodexHeadlessBackend(command="codex")

    result = codex.run_turn(
        prompt="create a project skill",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        permission_profile="project_writer",
    )

    assert result.ok is False
    assert result.status == "sandbox_unsupported"
    assert "Codex sandbox unsupported" in result.error
    assert "ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access" in result.error


def test_codex_headless_project_writer_approves_allowed_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    patch_path = tmp_path / "skills" / "zf-fmea-risk-gate" / "SKILL.md"
    monkeypatch.setenv("FAKE_CODEX_PATCH_PATH", str(patch_path))
    monkeypatch.setenv("FAKE_CODEX_EXPECT_DECISION", "approved")
    script = _fake_codex_patch_approval_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")

    result = codex.run_turn(
        prompt="create a project skill",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        permission_profile="project_writer",
    )

    assert result.ok is True
    assert result.reply == "approval handled"


def test_codex_headless_read_only_denies_patch_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    patch_path = tmp_path / "skills" / "zf-fmea-risk-gate" / "SKILL.md"
    monkeypatch.setenv("FAKE_CODEX_PATCH_PATH", str(patch_path))
    monkeypatch.setenv("FAKE_CODEX_EXPECT_DECISION", "denied")
    script = _fake_codex_patch_approval_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")

    result = codex.run_turn(
        prompt="create a project skill",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        permission_profile="read_only",
    )

    assert result.ok is True
    assert result.reply == "approval handled"


def test_codex_headless_v2_file_change_uses_accept_decline_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    patch_root = tmp_path / "skills"
    monkeypatch.setenv("FAKE_CODEX_PATCH_PATH", str(patch_root))
    monkeypatch.setenv("FAKE_CODEX_EXPECT_DECISION", "accept")
    monkeypatch.setenv("FAKE_CODEX_APPROVAL_METHOD", "item/fileChange/requestApproval")
    script = _fake_codex_patch_approval_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")

    result = codex.run_turn(
        prompt="create a project skill",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        permission_profile="project_writer",
    )

    assert result.ok is True


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("claude-code", "claude-headless"),
        ("claude", "claude-headless"),
        ("codex", "codex-headless"),
        ("codex-app-server", "codex-headless"),
    ],
)
def test_web_chat_backend_aliases_route_to_headless_backend(alias: str, canonical: str):
    assert canonical_headless_backend(alias) == canonical


class _FailThenSuccessBackend:
    backend_id = "claude-headless"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def run_turn(self, **kwargs):
        prior = str(kwargs.get("provider_session_id") or "")
        self.calls.append(prior)
        if prior == "old-session":
            return HeadlessTurnResult(
                ok=False,
                status="failed",
                backend=self.backend_id,
                thread_id=str(kwargs["thread_id"]),
                provider_session_id="",
                reply="",
                messages=[],
                usage={},
                resumed=True,
                fallback_reason="resume failed",
                error="unknown session",
            )
        kwargs["on_session_id"]("new-session")
        return HeadlessTurnResult(
            ok=True,
            status="completed",
            backend=self.backend_id,
            thread_id=str(kwargs["thread_id"]),
            provider_session_id="new-session",
            reply="fresh reply",
            messages=[HeadlessMessage(type="text", content="fresh reply")],
            usage={},
            resumed=False,
            fallback_reason="resume failed; retried fresh",
            error="",
        )


class _UnexpectedBackend:
    backend_id = "claude-headless"

    def available(self) -> bool:
        return True

    def run_turn(self, **kwargs) -> HeadlessTurnResult:
        raise AssertionError("backend should not run when permission drift is blocking")


def test_resume_failure_retries_fresh_without_preserving_bad_session(
    state_dir: Path,
):
    store = HeadlessThreadStore(state_dir=state_dir, project_root=state_dir.parent)
    thread = store.load(scope="project", task_id="TASK-2")
    store.pin_provider_session(
        thread,
        backend="claude-headless",
        provider_session_id="old-session",
        workdir=str(state_dir.parent),
        status="idle",
    )
    backend = _FailThenSuccessBackend()
    agent = KanbanHeadlessAgent(
        state_dir=state_dir,
        project_root=state_dir.parent,
        backends={"claude-headless": backend},
    )

    result = agent.run_turn(
        backend="claude-headless",
        message="resume me",
        task_id="TASK-2",
    )

    assert result.ok is True
    assert backend.calls == ["old-session", ""]
    assert result.provider_session_id == "new-session"
    assert result.fallback_reason == "resume failed; retried fresh"
    stored = store.load(scope="project", task_id="TASK-2")
    assert stored["providers"]["claude-headless"]["provider_session_id"] == "new-session"


def test_headless_permission_snapshot_blocking_drift_prevents_resume(
    state_dir: Path,
):
    store = HeadlessThreadStore(state_dir=state_dir, project_root=state_dir.parent)
    thread = store.load(scope="project", task_id="TASK-DRIFT")
    store.pin_provider_session(
        thread,
        backend="claude-headless",
        provider_session_id="old-session",
        workdir="/tmp/old-project",
        status="idle",
        permission_snapshot={
            "schema_version": "provider-permission-snapshot.v1",
            "backend": "claude-headless",
            "provider_session_id": "old-session",
            "cwd": "/tmp/old-project",
            "workspace_roots": ["/tmp/old-project"],
            "permission_profile": "read_only",
            "permission_mode": "default",
            "project_id": "",
        },
    )
    agent = KanbanHeadlessAgent(
        state_dir=state_dir,
        project_root=state_dir.parent,
        backends={"claude-headless": _UnexpectedBackend()},
    )

    result = agent.run_turn(
        backend="claude-headless",
        message="resume me",
        task_id="TASK-DRIFT",
    )

    assert result.ok is False
    assert result.status == "permission_drift_blocked"
    assert result.provider_session_id == "old-session"
    assert result.permission_drift["status"] == "blocking"
    assert "cwd" in result.error


def test_chat_orchestrator_can_use_claude_headless_backend(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = _fake_claude_script(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "task_id": "TASK-3",
            "message": "summarize this task",
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["backend"] == "claude-headless"
    assert data["turn_id"]

    events = _wait_for_event_type(state_dir, "web.action.completed")
    event_types = [event.type for event in events]
    user_index = event_types.index("user.message")
    tail = event_types[user_index:]
    for required in [
        "user.message",
        "kanban.agent.turn.created",
        "kanban.agent.turn.started",
        "runtime.snapshot.recorded",
        "agent.session.run.started",
        "kanban.agent.reply",
        "agent.session.run.completed",
        "provider.permission.snapshot.recorded",
        "kanban.agent.turn.completed",
        "runtime.action.completed",
        "web.action.completed",
    ]:
        assert required in tail
    assert tail.index("runtime.snapshot.recorded") < tail.index("agent.session.run.started")
    assert tail.index("kanban.agent.reply") < tail.index("agent.session.run.completed")
    deltas = [event for event in events if event.type == "kanban.agent.turn.delta"]
    assert deltas[-1].payload["content"] == "headless answer"
    replies = [event for event in events if event.type == "kanban.agent.reply"]
    assert replies[-1].payload["answer"] == "final headless answer"
    assert replies[-1].payload["backend"] == "claude-headless"
    assert replies[-1].payload["mutates_task_state"] is False
    session_completed = [event for event in events if event.type == "agent.session.run.completed"]
    assert session_completed[-1].payload["usage"] == {"input_tokens": 3, "output_tokens": 4}
    session_started = [event for event in events if event.type == "agent.session.run.started"]
    runtime_snapshots = [event for event in events if event.type == "runtime.snapshot.recorded"]
    assert session_started[-1].payload["snapshot_ref"] == runtime_snapshots[-1].payload["snapshot_ref"]
    snapshots = [event for event in events if event.type == "provider.permission.snapshot.recorded"]
    assert snapshots[-1].payload["snapshot"]["permission_profile"] == "read_only"
    assert snapshots[-1].payload["runtime_snapshot_ref"] == runtime_snapshots[-1].payload["snapshot_ref"]


def test_agent_session_stream_flushes_first_content_delta_immediately(state_dir: Path):
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    stream = AgentSessionStreamEmitter(
        writer=writer,
        identity=AgentSessionIdentity(
            run_id="run-stream-1",
            thread_id="main",
            source="kanban-agent.headless",
            actor="web",
            provider="claude-headless",
            backend="claude-headless",
        ),
        flush_interval_s=10.0,
    )

    stream.start()
    stream.emit_message(HeadlessMessage(type="text", content="first chunk"))

    events = EventLog(state_dir / "events.jsonl").read_all()
    parts = [event for event in events if event.type == "agent.session.part.delta"]
    assert len(parts) == 1
    assert parts[0].payload["content"] == "first chunk"

    stream.emit_message(HeadlessMessage(type="text", content=" second chunk"))
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([event for event in events if event.type == "agent.session.part.delta"]) == 1

    stream.flush()
    events = EventLog(state_dir / "events.jsonl").read_all()
    parts = [event for event in events if event.type == "agent.session.part.delta"]
    assert [event.payload["content"] for event in parts] == ["first chunk", " second chunk"]


def test_agent_session_stream_spills_large_tool_output(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_AGENT_SESSION_RAW_OUTPUT_THRESHOLD_BYTES", "64")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    stream = AgentSessionStreamEmitter(
        writer=writer,
        identity=AgentSessionIdentity(
            run_id="run-raw-1",
            thread_id="main",
            source="kanban-agent.headless",
            actor="web",
            provider="claude-headless",
            backend="claude-headless",
        ),
    )
    raw = "tool output\n" * 2000

    stream.start()
    stream.emit_message(HeadlessMessage(type="tool_result", output=raw))

    events = EventLog(state_dir / "events.jsonl").read_all()
    part = [event for event in events if event.type == "agent.session.part.delta"][-1]
    raw_output = part.payload["refs"]["raw_output"]
    assert part.payload["content"] != raw
    assert raw not in (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert (state_dir / raw_output["raw_ref"]).read_text(encoding="utf-8") == raw


def test_chat_orchestrator_streams_first_text_delta_before_final_reply(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = tmp_path / "fake_claude_slow_stream.py"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'claude-slow-1'}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'first chunk'}]}}), flush=True)",
            "time.sleep(1.0)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':' second chunk'}]}}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-slow-1','result':'first chunk second chunk'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    monkeypatch.setenv("ZF_KANBAN_AGENT_STREAM_FLUSH_INTERVAL_S", "0.2")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "task_id": "TASK-SLOW-STREAM",
            "message": "slow stream",
        },
    )

    assert response.status_code == 202
    deadline = time.monotonic() + 0.6
    events = []
    text_deltas = []
    while time.monotonic() < deadline:
        events = EventLog(state_dir / "events.jsonl").read_all()
        text_deltas = [
            event for event in events
            if event.type == "kanban.agent.turn.delta"
            and event.payload.get("message_type") == "text"
        ]
        if text_deltas:
            break
        time.sleep(0.05)

    assert text_deltas
    assert text_deltas[0].payload["content"] == "first chunk"
    assert not [event for event in events if event.type == "kanban.agent.reply"]

    events = _wait_for_event_type(state_dir, "web.action.completed", timeout_s=3.0)
    replies = [event for event in events if event.type == "kanban.agent.reply"]
    assert replies[-1].payload["answer"] == "first chunk second chunk"


def test_chat_orchestrator_batches_fast_text_and_thinking_deltas(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = tmp_path / "fake_claude_fast_deltas.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'claude-fast-1'}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'thinking','text':'private'}]}}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'alpha '}]}}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'beta'}]}}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-fast-1','result':'alpha beta'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "task_id": "TASK-FAST",
            "sync": True,
            "message": "fast stream",
        },
    )

    assert response.status_code == 200
    events = EventLog(state_dir / "events.jsonl").read_all()
    deltas = [event for event in events if event.type == "kanban.agent.turn.delta"]
    assert [event.payload["message_type"] for event in deltas] == ["status", "thinking", "text"]
    assert [event.payload["content"] for event in deltas if event.payload["message_type"] == "text"] == ["alpha beta"]
    replies = [event for event in events if event.type == "kanban.agent.reply"]
    assert replies[-1].payload["answer"] == "alpha beta"


def test_headless_thread_key_isolates_provider_sessions(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = _fake_claude_script(tmp_path)
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    agent = KanbanHeadlessAgent(state_dir=state_dir, project_root=state_dir.parent)

    first = agent.run_turn(
        backend="claude-headless",
        message="first",
        thread_key="chat-a",
    )
    second = agent.run_turn(
        backend="claude-headless",
        message="second",
        thread_key="chat-b",
    )

    assert first.thread_id != second.thread_id
    store = HeadlessThreadStore(state_dir=state_dir, project_root=state_dir.parent)
    assert store.load(thread_key="chat-a")["thread_key"] == "chat-a"
    assert store.load(thread_key="chat-b")["thread_key"] == "chat-b"


def test_chat_orchestrator_extracts_headless_action_proposal(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = tmp_path / "fake_claude_proposal.py"
    proposal = {
        "action_proposal": {
            "action": "update-task",
            "payload": {"task_id": "TASK-4", "status": "blocked"},
            "reason": "needs external input",
        }
    }
    script.write_text(
        "\n".join([
            "import json",
            f"proposal = {proposal!r}",
            "print(json.dumps({'type':'system','session_id':'claude-session-2'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-session-2','result':json.dumps(proposal)}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "task_id": "TASK-4",
            "sync": True,
            "message": "block this task",
        },
    )

    assert response.status_code == 200
    proposal_data = response.json()["reply"]["action_proposal"]
    assert proposal_data["action"] == "update-task"
    assert proposal_data["payload"]["task_id"] == "TASK-4"
    assert proposal_data["payload"]["status"] == "blocked"
    assert proposal_data["payload"]["run_id"] == response.json()["turn_id"]
    assert proposal_data["payload"]["causation_id"] == response.json()["event_id"]
    assert proposal_data["mutates_task_state"] is True
    assert proposal_data["valid"] is True


def test_chat_orchestrator_extracts_create_task_proposal(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = tmp_path / "fake_claude_create_task.py"
    proposal = {
        "action_proposal": {
            "action": "create-task",
            "payload": {
                "title": "Add auth timeout retry",
                "contract": {
                    "behavior": "Retry transient auth timeout failures.",
                    "verification": "Focused auth retry test passes.",
                },
            },
            "reason": "new work should be tracked as a task",
        }
    }
    script.write_text(
        "\n".join([
            "import json",
            f"proposal = {proposal!r}",
            "print(json.dumps({'type':'system','session_id':'claude-session-3'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-session-3','result':json.dumps(proposal)}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "project_id": "zaofu-test",
            "conversation_id": "kanban:zaofu-test",
            "thread_key": "new-task-thread",
            "sync": True,
            "message": "create a task for auth timeout retry",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["thread_key"] == "new-task-thread"
    proposal_data = data["reply"]["action_proposal"]
    assert proposal_data["action"] == "create-task"
    assert proposal_data["payload"]["title"] == "Add auth timeout retry"
    assert proposal_data["payload"]["project_id"] == "zaofu-test"
    assert proposal_data["payload"]["conversation_id"] == "kanban:zaofu-test"
    assert proposal_data["payload"]["thread_id"] == "new-task-thread"
    assert proposal_data["payload"]["run_id"] == data["turn_id"]
    assert proposal_data["payload"]["causation_id"] == data["event_id"]
    assert proposal_data["mutates_task_state"] is True
    assert proposal_data["valid"] is True


def test_readonly_message_suppresses_example_create_task_proposal():
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "payload": {"title": "任务标题"},
            "reason": "example only",
        }
    })

    proposal = _headless_action_proposal(
        answer,
        user_message="介绍下你自己",
    )

    assert proposal is None


def test_analyze_task_message_suppresses_create_task_proposal():
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "payload": {"title": "Investigate TASK-A734FF failure"},
            "reason": "misclassified analysis as new work",
        }
    })

    proposal = _headless_action_proposal(
        answer,
        user_message="分析下 TASK-A734FF task",
    )

    assert proposal is None


def test_explicit_create_task_message_keeps_create_task_proposal():
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "payload": {"title": "Investigate TASK-A734FF failure"},
            "reason": "operator asked to track it",
        }
    })

    proposal = _headless_action_proposal(
        answer,
        user_message="创建一个任务跟踪这个 bug",
    )

    assert proposal is not None
    assert proposal["action"] == "create-task"
    assert proposal["valid"] is True


def test_explicit_task_proposal_message_keeps_create_task_proposal():
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "payload": {"title": "Fix Channel Group interactive E2E gap"},
            "reason": "operator asked for a task proposal",
        }
    })

    proposal = _headless_action_proposal(
        answer,
        user_message="请把‘修复 Channel Group 真实互动 E2E 缺口’整理成一个 task proposal。",
    )

    assert proposal is not None
    assert proposal["action"] == "create-task"
    assert proposal["valid"] is True


def test_claude_headless_cancel_interrupts_registered_process(tmp_path: Path):
    script = tmp_path / "slow_claude.py"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'slow-session'}), flush=True)",
            "time.sleep(30)",
            "print(json.dumps({'type':'result','session_id':'slow-session','result':'too late'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    backend = ClaudeHeadlessBackend(command=f"{sys.executable} {script}")
    run_id = "run-cancel-test"
    thread_id = "thread-cancel-test"
    seen_message = threading.Event()
    result: dict[str, HeadlessTurnResult] = {}

    def run() -> None:
        result["turn"] = backend.run_turn(
            prompt="cancel me",
            cwd=tmp_path,
            system_prompt="",
            thread_id="stable-thread",
            provider_session_id="",
            on_session_id=lambda _session_id: None,
            on_message=lambda _message: seen_message.set(),
            timeout_s=20,
            run_id=run_id,
            run_thread_id=thread_id,
            project_id="project-a",
            conversation_id="kanban:project-a",
        )

    worker = threading.Thread(target=run)
    worker.start()
    assert seen_message.wait(timeout=5)

    cancel = cancel_agent_session_run(run_key(
        run_id=run_id,
        thread_id=thread_id,
        project_id="project-a",
        conversation_id="kanban:project-a",
    ))

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert cancel.interrupt_supported is True
    assert cancel.process_found is True
    assert cancel.process_terminated is True
    assert result["turn"].status == "cancelled"


def test_create_task_proposal_requires_title():
    from zf.web.server import _headless_action_proposal

    proposal = _headless_action_proposal(json.dumps({
        "action_proposal": {
            "action": "create-task",
            "payload": {"contract": {"behavior": "missing title"}},
        }
    }))

    assert proposal is not None
    assert proposal["action"] == "create-task"
    assert proposal["valid"] is False
    assert "title is required" in proposal["validation_error"]
