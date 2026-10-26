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

from zf.core.config.schema import WorkflowConfig, WorkflowStageConfig, ZfConfig
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.runtime.kanban_plan_requests import PLAN_REQUESTED_EVENT
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_anchor import is_workflow_managed_task
from zf.web.plan_extraction import extract_plan_request
from zf.web.plan_runtime import prepare_web_plan_discussion
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


def _proposal_intent(source_quote: str) -> dict[str, str]:
    return {
        "decision": "propose_action",
        "source_quote": source_quote,
    }


def test_codex_turn_completion_detects_output_and_context_exhaustion() -> None:
    incomplete, reason = headless_agent._codex_turn_completion({
        "params": {
            "turn": {
                "status": "incomplete",
                "incompleteDetails": {"reason": "max_output_tokens"},
            },
        },
    })
    assert incomplete is True
    assert reason == "max_output_tokens"

    complete, complete_reason = headless_agent._codex_turn_completion({
        "params": {"turn": {"status": "completed"}},
    })
    assert complete is False
    assert complete_reason == "completed"


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
                "expected_sandbox = os.environ.get('FAKE_CODEX_SANDBOX', 'danger-full-access')",
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


def test_system_prompt_requires_exact_artifact_lineage_for_create_task(
    state_dir: Path,
    tmp_path: Path,
) -> None:
    agent = KanbanHeadlessAgent(
        state_dir=state_dir,
        project_root=tmp_path,
    )

    prompt = agent._system_prompt("read_only")

    assert "contract.spec_ref" in prompt
    assert "contract.source_ref" in prompt
    assert "contract.handoff_artifacts" in prompt
    assert "contract.evidence_contract.channel_prd_digest" in prompt
    assert "must be non-empty" in prompt
    assert "must not be fabricated" in prompt
    assert "canonical_channel_prds" in prompt
    assert "multiple plausible items" in prompt
    assert "Decide that intent semantically" in prompt
    assert "subject_type to exactly one of channel_setup, clarification" in prompt
    assert "never invent a family-specific subject_type" in prompt
    assert '"decision": "propose_action"' in prompt
    assert "exact verbatim substring" in prompt
    assert "do not rely on English or Chinese keyword spelling" in prompt
    assert "subject_type=task_create with two or three options" in prompt
    assert "channel_prd_ref and channel_prd_digest" in prompt
    assert 'channel_prd_intent={"decision":"bind_channel_prd"' in prompt
    assert "Never add them for an unrelated Issue" in prompt
    assert "task_create Plan is reserved" in prompt
    assert "emit a create-task action_proposal" in prompt
    assert "exact option-level submit_payload" in prompt
    assert "Do not wrap Channel options in effect" in prompt
    assert "never as effect.mode or submit_mode" in prompt
    assert "Do not nest a contract or channel_authority" in prompt
    assert "effect.mode=continue, not defer" in prompt
    assert "do not add payload.workflow_plan or plan_request.payload" in prompt
    assert "parameters may contain only executable adapter keys" in prompt
    assert "runner/config, and evidence-producer paths" in prompt
    assert "omit Playwright/evidence paths" in prompt
    assert "target_root" in prompt
    assert "needed lenses" in prompt
    assert "Profile registration" in prompt
    assert "continue the original thread" in prompt


def test_chat_plan_payload_rejects_discussion_and_response_together() -> None:
    from zf.web.plan_runtime import validate_chat_plan_payload

    error = validate_chat_plan_payload({
        "message": "Explain and answer this Plan",
        "plan_discussion": {
            "request_event_id": "evt-plan",
            "request_id": "plan-route",
            "revision": 1,
        },
        "plan_response": {
            "request_event_id": "evt-plan",
            "request_id": "plan-route",
            "revision": 1,
            "question_id": "route",
            "option_id": "direct",
        },
    })

    assert error == "plan_discussion and plan_response are mutually exclusive"


def test_chat_orchestrator_injects_canonical_channel_prd_context(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = tmp_path / "stdin.jsonl"
    script = _fake_claude_script(tmp_path)
    monkeypatch.setenv("FAKE_CLAUDE_CAPTURE", str(capture))
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    monkeypatch.setattr(
        "zf.web.server.canonical_channel_prd_context",
        lambda _state_dir: {
            "schema_version": "channel-prd-context.v1",
            "items": [{
                "channel_id": "ch-prd",
                "artifact_ref": "channel-artifacts/ch-prd/prd.md",
                "artifact_digest": "sha256:canonical",
            }],
        },
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(
        create_app(state_dir, project_root=state_dir.parent)
    )

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "project_id": "zaofu-test",
            "conversation_id": "kanban:zaofu-test",
            "thread_key": "main",
            "sync": True,
            "message": "基于刚才的 canonical PRD 创建 Task proposal",
        },
    )

    assert response.status_code == 200
    sent = json.loads(capture.read_text(encoding="utf-8"))
    prompt = sent["message"]["content"][0]["text"]
    assert '"canonical_channel_prds"' in prompt
    assert '"artifact_ref": "channel-artifacts/ch-prd/prd.md"' in prompt
    assert '"artifact_digest": "sha256:canonical"' in prompt
    messages = [event for event in EventLog(state_dir / "events.jsonl").read_all()
                if event.type == "user.message"]
    assert messages
    assert '"requirement_source_messages"' in prompt
    assert messages[-1].id in prompt


def test_chat_orchestrator_binds_agent_selected_channel_prd_plan(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_ref = "channels/ch-prd/prd/r1.json"
    artifact_digest = "a" * 64
    canonical = {
        "schema_version": "channel-prd-context.v1",
        "items": [{
            "channel_id": "ch-prd",
            "thread_id": "main",
            "channel_member_id": "product-pm",
            "leader_revision": 1,
            "prd_revision": 1,
            "artifact_ref": artifact_ref,
            "artifact_digest": artifact_digest,
        }],
    }
    reply = json.dumps({
        "plan_request": {
            "subject_type": "task_create",
            "channel_prd_ref": artifact_ref,
            "channel_prd_digest": artifact_digest,
            "channel_prd_intent": {
                "decision": "bind_channel_prd",
                "source_quote": "Create the Task from the confirmed PRD.",
            },
            "header": "Create PRD Task",
            "question": "Create the exact Task?",
            "options": [
                {
                    "label": "Create Task (Recommended)",
                    "description": "Create the PRD-bound proposal.",
                    "effect": {
                        "mode": "propose",
                        "action": "create-task",
                        "payload": {"title": "Deliver the Channel PRD", "workflow_plan": {
                            "options": [{"route_id": "delivery:prd:standard", "parameters": {}},
                                        {"mode": "defer"}],
                        }},
                    },
                },
                {
                    "label": "Continue discussion",
                    "description": "Do not create work yet.",
                    "effect": {"mode": "continue"},
                },
            ],
            "allow_other": False,
        },
    })

    def fake_turn(_self, **_kwargs) -> HeadlessTurnResult:
        return HeadlessTurnResult(
            ok=True,
            status="completed",
            backend="codex-headless",
            thread_id="thread-1",
            provider_session_id="provider-1",
            reply=reply,
            messages=[],
            usage={},
            resumed=False,
        )

    monkeypatch.setattr(KanbanHeadlessAgent, "run_turn", fake_turn)
    monkeypatch.setattr(
        "zf.web.server.canonical_channel_prd_context",
        lambda _state_dir: canonical,
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(
        create_app(state_dir, project_root=state_dir.parent)
    )

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "codex-headless",
            "project_id": "zaofu-test",
            "conversation_id": "kanban:zaofu-test",
            "thread_key": "main",
            "sync": True,
            "message": "Create the Task from the confirmed PRD.",
        },
    )

    assert response.status_code == 200
    requested = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == PLAN_REQUESTED_EVENT
    ][-1]
    request = requested.payload["plan_request"]
    assert request["valid"] is True, request["validation_error"]
    assert request["channel_prd_intent"]["source_quote"] == (
        "Create the Task from the confirmed PRD."
    )
    assert request["options"][0]["submit_payload"][
        "channel_authority"
    ]["source_ref"] == artifact_ref


    nested = response.json()["reply"]["plan_request"]["options"][0]["submit_payload"]["workflow_plan"]
    assert nested["options"][0]["parameters"]["target_root"] == str(state_dir.parent.resolve())
    assert "parameters" not in nested["options"][1]

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


def _fake_codex_user_input_request_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_codex_user_input_request.py"
    script.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "expected_code = int(os.environ.get('FAKE_CODEX_USER_INPUT_ERROR_CODE', '-32001'))",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    msg = json.loads(line)",
                "    method = msg.get('method')",
                "    req_id = msg.get('id')",
                "    if req_id == 99 and not method:",
                "        error = msg.get('error', {})",
                "        assert error.get('code') == expected_code, msg",
                "        if expected_code == -32601:",
                "            assert 'plan_request' in error.get('message', ''), msg",
                "            print(json.dumps({'jsonrpc':'2.0','method':'item/agentMessage/delta','params':{'delta':'input rejected safely'}}), flush=True)",
                "            print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'turn':{'status':'completed'}}}), flush=True)",
                "    elif req_id and method == 'initialize':",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
                "    elif method == 'initialized':",
                "        pass",
                "    elif req_id and method == 'thread/start':",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'codex-thread-1'}}), flush=True)",
                "    elif req_id and method == 'turn/start':",
                "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turnId':'turn-1'}}), flush=True)",
                "        params = {'questions':[{'id':'route','question':'Which route?','options':[{'label':'A'},{'label':'B'}]}]}",
                "        print(json.dumps({'jsonrpc':'2.0','id':99,'method':'item/tool/requestUserInput','params':params}), flush=True)",
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
    assert result.permission_snapshot["permission_profile"] == "workspace_writer"
    assert result.permission_snapshot["permission_mode"] == "bypassPermissions"
    assert result.permission_drift == {"status": "ok", "items": []}
    sent = json.loads(capture.read_text(encoding="utf-8"))
    assert sent["type"] == "user"
    assert sent["message"]["content"][0]["text"]

    stored = HeadlessThreadStore(
        state_dir=state_dir,
        project_root=state_dir.parent,
    ).load(scope="project", task_id="TASK-1")
    assert stored["providers"]["claude-headless"]["provider_session_id"] == "claude-session-1"
    assert stored["providers"]["claude-headless"]["permission_snapshot"]["permission_mode"] == "bypassPermissions"
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


def test_claude_headless_streaming_turn_uses_idle_timeout_not_total_timeout(
    tmp_path: Path,
):
    # A2-1: a create-task turn streams grounding/tool frames for far longer than
    # timeout_s in total, yet is never silent for timeout_s. It must NOT be
    # killed by a wall-clock cap (the pre-fix bug returned status:timeout with
    # zero proposal). Only true silence for timeout_s may trip the deadline.
    script = tmp_path / "fake_claude_slow_stream.py"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'claude-slow-1'}), flush=True)",
            "for part in ['reading ', 'grounding ', 'contract']:",
            "    time.sleep(0.12)",
            "    print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':part}]}}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-slow-1','result':'proposal ready'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    backend = ClaudeHeadlessBackend(command=f"{sys.executable} {script}")
    messages: list[HeadlessMessage] = []

    result = backend.run_turn(
        prompt="slow grounding turn",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=messages.append,
        timeout_s=0.2,
    )

    assert result.ok is True
    assert result.status != "timeout"
    assert result.reply == "proposal ready"
    assert [message.content for message in messages if message.type == "text"] == [
        "reading ",
        "grounding ",
        "contract",
    ]


def test_codex_headless_tool_timeout_uses_env_with_safe_fallback(monkeypatch) -> None:
    monkeypatch.delenv("ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S", raising=False)
    assert CodexHeadlessBackend(command="codex").tool_timeout_s == 7200.0

    monkeypatch.setenv("ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S", "14400")
    assert CodexHeadlessBackend(command="codex").tool_timeout_s == 14400.0

    for raw in ("bad", "0", "-1"):
        monkeypatch.setenv("ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S", raw)
        assert CodexHeadlessBackend(command="codex").tool_timeout_s == 7200.0


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
        timeout_s=1.0,
    )

    assert result.ok is False
    assert result.status == "timeout"
    assert "codex turn timed out after 1s" in result.error
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


def test_codex_headless_in_flight_tool_uses_longer_idle_budget(
    tmp_path: Path,
):
    script = tmp_path / "fake_codex_slow_tool.py"
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
            "        print(json.dumps({'jsonrpc':'2.0','method':'item/started','params':{'item':{'id':'tool-1','type':'commandExecution','command':'slow-test'}}}), flush=True)",
            "        time.sleep(0.35)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'item/completed','params':{'item':{'id':'tool-1','type':'commandExecution','output':'ok'}}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'item/agentMessage/delta','params':{'delta':'done'}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'threadId':'codex-thread-1','turn':{'id':'turn-1','status':'completed'}}}), flush=True)",
        ]),
        encoding="utf-8",
    )
    codex = CodexHeadlessBackend(
        command=f"{sys.executable} {script}",
        tool_timeout_s=0.6,
    )

    result = codex.run_turn(
        prompt="run slow tool",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=0.2,
    )

    assert result.ok is True
    assert result.reply == "done"


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

    monkeypatch.setenv("FAKE_CODEX_SANDBOX", "danger-full-access")
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


def test_headless_provider_env_strips_zf_control_plane_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "web-secret")
    monkeypatch.setenv("ZF_WEB_PASSCODE", "passcode-secret")
    monkeypatch.setenv("ZF_WORKFLOW_ACTION_TOKEN", "workflow-secret")
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "feishu-secret")
    monkeypatch.setenv("ZF_DOC156_REQUEST_ID", "non-secret-context")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setenv("ZF_CLI_CMD", "/active-runtime/bin/zf")

    env = headless_agent._headless_subprocess_env()

    assert "ZF_WEB_ACTION_TOKEN" not in env
    assert "ZF_WEB_PASSCODE" not in env
    assert "ZF_WORKFLOW_ACTION_TOKEN" not in env
    assert "ZF_FEISHU_ACTION_TOKEN_SECRET" not in env
    assert env["ZF_DOC156_REQUEST_ID"] == "non-secret-context"
    assert env["OPENAI_API_KEY"] == "provider-secret"
    assert env["ZF_CLI_CMD"] == "/active-runtime/bin/zf"


def test_headless_provider_and_prompt_pin_active_zf_cli(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ZF_CLI_CMD", raising=False)
    monkeypatch.setattr(
        headless_agent,
        "default_zf_cli_cmd",
        lambda: "/repair-worktree/.venv/bin/zf",
    )
    agent = KanbanHeadlessAgent(
        state_dir=state_dir,
        project_root=tmp_path,
    )

    env = headless_agent._headless_subprocess_env()
    system_prompt = agent._system_prompt("read_only")
    turn_prompt = agent._build_prompt(
        message="inspect the current route",
        task_id="TASK-CLI",
        context={"thread_key": "kanban:test"},
    )

    assert env["ZF_CLI_CMD"] == "/repair-worktree/.venv/bin/zf"
    assert "use the exact active-runtime command" in system_prompt
    assert "`/repair-worktree/.venv/bin/zf`" in system_prompt
    assert "Do not use a bare `zf`" in system_prompt
    assert "zf_cli_cmd: /repair-worktree/.venv/bin/zf" in turn_prompt


def test_codex_headless_resume_reapplies_provider_security(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FAKE_CODEX_SANDBOX", "danger-full-access")
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


def test_codex_headless_fails_fast_when_explicit_sandbox_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX", "workspace-write")
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


def test_codex_headless_captures_native_user_input_as_pending_interaction(
    tmp_path: Path,
) -> None:
    script = _fake_codex_user_input_request_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")
    messages = []

    result = codex.run_turn(
        prompt="ask for the route",
        cwd=tmp_path,
        system_prompt="system",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=messages.append,
        timeout_s=5,
        permission_profile="read_only",
    )

    assert result.ok is True
    assert result.status == "waiting_input"
    assert result.reply == ""
    assert len(messages) == 1
    assert messages[0].type == "question"
    assert messages[0].raw["native_interaction"]["questions"] == [
        {
            "id": "route",
            "question": "Which route?",
            "options": [{"label": "A"}, {"label": "B"}],
        }
    ]


def test_codex_headless_legacy_rollout_rejects_native_user_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_KANBAN_WEBCHAT_RUNTIME", "legacy")
    monkeypatch.setenv("FAKE_CODEX_USER_INPUT_ERROR_CODE", "-32601")
    script = _fake_codex_user_input_request_script(tmp_path)
    codex = CodexHeadlessBackend(command=f"{sys.executable} {script}")

    result = codex.run_turn(
        prompt="ask for the route",
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
    assert result.status == "completed"
    assert result.reply == "input rejected safely"


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
    assert "kanban.agent.turn.delta" not in event_types, (
        "deltas are ephemeral bus transport, never ledger truth (doc 106)"
    )
    assert not _bus_rows(state_dir, "kanban.agent.turn.delta")
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
    assert snapshots[-1].payload["snapshot"]["permission_profile"] == "workspace_writer"
    assert snapshots[-1].payload["runtime_snapshot_ref"] == runtime_snapshots[-1].payload["snapshot_ref"]
    turn_completed = [
        event for event in events
        if event.type == "kanban.agent.turn.completed"
    ][-1]
    assert turn_completed.payload["duration_ms"] >= 0
    assert turn_completed.payload["timing"]["context_duration_ms"] >= 0
    assert turn_completed.payload["timing"]["provider_duration_ms"] >= 0
    assert turn_completed.payload["timing"]["time_to_first_output_ms"] >= 0
    assert turn_completed.payload["timing"]["plan_projection_duration_ms"] >= 0
    assert turn_completed.payload["outcome"] == "completed"
    assert turn_completed.payload["failure_class"] == "none"
    assert turn_completed.payload["retryable"] is False


def test_chat_orchestrator_failed_turn_carries_timing(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_turn(_self, **_kwargs):  # noqa: ANN001
        raise RuntimeError("provider failed before output")

    monkeypatch.setattr(KanbanHeadlessAgent, "run_turn", fail_turn)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "message": "fail this turn deterministically",
            "sync": True,
        },
    )

    assert response.status_code == 503
    failed = [
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "kanban.agent.turn.failed"
    ][-1]
    assert failed.payload["duration_ms"] >= 0
    assert failed.payload["timing"]["context_duration_ms"] >= 0
    assert failed.payload["timing"]["provider_duration_ms"] >= 0
    assert failed.payload["timing"]["time_to_first_output_ms"] is None
    assert failed.payload["timing"]["plan_projection_duration_ms"] is None
    assert failed.payload["outcome"] == "failed"
    assert failed.payload["failure_class"] == "provider_error"
    assert failed.payload["retryable"] is True


def _bus_rows(state_dir: Path, type_: str = ""):
    """doc 106 B axis: token deltas live on the ephemeral LiveDeltaBus, not
    in events.jsonl — streaming assertions read the bus."""
    from zf.runtime.live_delta_bus import LiveDeltaBus

    rows, _ = LiveDeltaBus(state_dir).read_since()
    return [r for r in rows if not type_ or r.type == type_]


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

    parts = _bus_rows(state_dir, "agent.session.part.delta")
    assert len(parts) == 1
    assert parts[0].payload["content"] == "first chunk"

    stream.emit_message(HeadlessMessage(type="text", content=" second chunk"))
    assert len(_bus_rows(state_dir, "agent.session.part.delta")) == 1

    stream.flush()
    parts = _bus_rows(state_dir, "agent.session.part.delta")
    assert [event.payload["content"] for event in parts] == ["first chunk", " second chunk"]
    ledger_types = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
    assert "agent.session.part.delta" not in ledger_types


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
    first_chunk_written = tmp_path / "fake_claude_first_chunk_written"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "from pathlib import Path",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'claude-slow-1'}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'first chunk'}]}}), flush=True)",
            f"Path({str(first_chunk_written)!r}).write_text('ready', encoding='utf-8')",
            "time.sleep(2.0)",
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
    provider_deadline = time.monotonic() + 3.0
    while time.monotonic() < provider_deadline and not first_chunk_written.exists():
        time.sleep(0.02)
    assert first_chunk_written.exists()

    deadline = time.monotonic() + 1.5
    text_deltas = []
    while time.monotonic() < deadline:
        text_deltas = [
            row for row in _bus_rows(state_dir, "kanban.agent.turn.delta")
            if row.payload.get("message_type") == "text"
        ]
        if text_deltas:
            break
        time.sleep(0.05)

    assert text_deltas
    assert text_deltas[0].payload["content"] == "first chunk"
    events = EventLog(state_dir / "events.jsonl").read_all()
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
    assert not _bus_rows(state_dir, "kanban.agent.turn.delta")
    assert not [event for event in events if event.type == "kanban.agent.turn.delta"]
    replies = [event for event in events if event.type == "kanban.agent.reply"]
    assert replies[-1].payload["answer"] == "alpha beta"


def test_chat_orchestrator_spills_large_delta_and_reply_to_sidecar(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = tmp_path / "fake_claude_large_output.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "sys.stdin.readline()",
            "text = 'A' * 15000",
            "print(json.dumps({'type':'system','session_id':'claude-large-1'}), flush=True)",
            "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':text}]}}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-large-1','result':text}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    monkeypatch.setenv("ZF_KANBAN_AGENT_SIDECAR_THRESHOLD_BYTES", "16")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "task_id": "TASK-LARGE",
            "sync": True,
            "message": "large output",
        },
    )

    assert response.status_code == 200
    events = EventLog(state_dir / "events.jsonl").read_all()
    reply = [event for event in events if event.type == "kanban.agent.reply"][-1]

    # The committed reply spills to the sidecar; terminal completion discards
    # the ephemeral delta scratch.
    assert reply.payload["answer"] != "A" * 15000
    reply_ref = reply.payload["refs"]["raw_output"]
    assert hydrate_sidecar_ref(state_dir, reply_ref).payload == "A" * 15000
    assert "A" * 15000 not in (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert not _bus_rows(state_dir, "kanban.agent.turn.delta")


def test_workspace_writer_runs_real_headless_executor_and_records_profile(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = tmp_path / "fake_claude_writer.py"
    output = state_dir.parent / "workspace-change.txt"
    script.write_text(
        "\n".join([
            "import json",
            "from pathlib import Path",
            "Path('workspace-change.txt').write_text('implemented', encoding='utf-8')",
            "print(json.dumps({'type':'system','session_id':'writer-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'writer-session','result':'implemented and tested'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "permission_profile": "workspace_writer",
            "permission_escalation_retry_for": "evt-sandbox-failed",
            "sync": True,
            "thread_key": "coding-thread",
            "message": "implement the requested change",
        },
    )

    assert response.status_code == 200
    assert output.read_text(encoding="utf-8") == "implemented"
    assert response.json()["permission_profile"] == "workspace_writer"
    events = EventLog(state_dir / "events.jsonl").read_all()
    snapshots = [
        event for event in events
        if event.type == "provider.permission.snapshot.recorded"
    ]
    assert snapshots[-1].payload["permission_profile"] == "workspace_writer"
    assert snapshots[-1].payload["snapshot"]["permission_mode"] == "bypassPermissions"
    completed = [
        event for event in events if event.type == "kanban.agent.turn.completed"
    ]
    assert completed[-1].payload["permission_profile"] == "workspace_writer"
    user_message = [
        event for event in events if event.type == "user.message"
    ][-1]
    assert (
        user_message.payload["permission_escalation_retry_for"]
        == "evt-sandbox-failed"
    )


def test_provider_dev_chat_start_uses_headless_executor(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = _fake_claude_script(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/provider-dev-chat-start",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "permission_profile": "workspace_writer",
            "thread_id": "dev-thread",
            "sync": True,
            "message": "inspect and update the project",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert "provider.dev_chat.start.requested" in [event.type for event in events]
    assert [
        event for event in events
        if event.type == "kanban.agent.reply"
        and event.payload.get("permission_profile") == "workspace_writer"
    ]


def test_dangerous_full_requires_explicit_ack(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "permission_profile": "dangerous_full",
            "message": "run a full project operation",
        },
    )

    assert response.status_code == 422
    assert "dangerous_ack=true" in json.dumps(response.json())


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

    assert proposal_data["proposal_event_id"].startswith("evt-")
    events = EventLog(state_dir / "events.jsonl").read_all()
    reply_event = [
        event for event in events if event.type == "kanban.agent.reply"
    ][-1]
    proposed_event = [
        event
        for event in events
        if event.id == proposal_data["proposal_event_id"]
    ][0]
    assert reply_event.payload["action_proposal"]["proposal_event_id"] == (
        proposed_event.id
    )
    assert proposed_event.causation_id == reply_event.id


def test_chat_orchestrator_extracts_and_resumes_durable_plan_request(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "fake_claude_plan.py"
    plan = {
        "plan_request": {
            "header": "Route",
            "id": "route",
            "question": "How should this requirement proceed?",
            "options": [
                {
                    "id": "research",
                    "label": "Research (Recommended)",
                    "description": "Collect evidence first.",
                },
                {
                    "id": "channel",
                    "label": "Channel",
                    "description": "Resolve the decision with roles.",
                },
            ],
            "allow_other": True,
        }
    }
    script.write_text(
        "\n".join([
            "import json, sys",
            "sys.stdin.readline()",
            f"plan = {plan!r}",
            "print(json.dumps({'type':'system','session_id':'claude-plan-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-plan-session','result':json.dumps(plan)}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))
    route = "/api/actions/chat-orchestrator"
    headers = {"x-zf-web-token": "test-token"}

    first = client.post(
        route,
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "sync": True,
            "message": "Help me choose the route",
        },
    )

    assert first.status_code == 200
    request = first.json()["reply"]["plan_request"]
    assert request["valid"] is True
    assert request["request_event_id"].startswith("evt-")
    assert request["backend"] == "claude-headless"
    assert request["provider_session_id"] == "claude-plan-session"
    events = EventLog(state_dir / "events.jsonl").read_all()
    reply = [event for event in events if event.type == "kanban.agent.reply"][-1]
    requested = [
        event for event in events
        if event.type == "kanban.agent.plan.requested"
    ][-1]
    assert requested.id == request["request_event_id"]
    assert requested.causation_id == reply.id

    response_payload = {
        "request_event_id": request["request_event_id"],
        "request_id": request["request_id"],
        "revision": request["revision"],
        "question_id": request["question_id"],
        "option_id": "research",
        "answer": "forged text",
    }
    mismatched = client.post(
        route,
        headers=headers,
        json={
            "backend": "codex-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "sync": True,
            "plan_response": response_payload,
        },
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["status"] == "plan_context_mismatch"

    second = client.post(
        route,
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "sync": True,
            "plan_response": response_payload,
        },
    )

    assert second.status_code == 200
    events = EventLog(state_dir / "events.jsonl").read_all()
    answered = [
        event for event in events
        if event.type == "kanban.agent.plan.answered"
    ]
    assert len(answered) == 1
    assert answered[0].payload["answer"] == "Research (Recommended)"
    continuation = [
        event for event in events
        if event.type == "user.message"
        and event.causation_id == answered[0].id
    ][0]
    assert "Answer: Research (Recommended)" in (
        continuation.payload["message"]
    )
    assert continuation.payload["thread_key"] == "main"
    requested_after_answer = [
        event
        for event in events
        if event.type == "kanban.agent.plan.requested"
    ]
    assert len(requested_after_answer) == 2
    assert (
        requested_after_answer[-1].payload["plan_request"][
            "originating_message_event_id"
        ]
        == request["originating_message_event_id"]
    )

    user_message_count = len([
        event for event in events if event.type == "user.message"
    ])
    duplicate = client.post(
        route,
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "sync": True,
            "plan_response": response_payload,
        },
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_answered"
    final_events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([
        event for event in final_events if event.type == "user.message"
    ]) == user_message_count


def test_chat_orchestrator_discusses_exact_pending_plan_without_answering(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = tmp_path / "plan-discussion-stdin.jsonl"
    script = _fake_claude_script(tmp_path)
    monkeypatch.setenv("FAKE_CLAUDE_CAPTURE", str(capture))
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "header": "Delivery route",
                "id": "route",
                "question": "Which delivery route should we use?",
                "options": [
                    {
                        "id": "direct",
                        "label": "Direct",
                        "description": "Create the task directly.",
                    },
                    {
                        "id": "research",
                        "label": "Research",
                        "description": "Collect evidence first.",
                    },
                ],
            },
        }),
        plan_context={
            "backend": "claude-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "turn_id": "turn-plan",
        },
    )
    assert request is not None
    requested = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        correlation_id="plan-discussion",
    )
    request["request_event_id"] = requested.id
    requested.payload = {
        "source": "kanban-agent.headless",
        "project_id": "project-a",
        "conversation_id": "kanban:project-a",
        "thread_key": "main",
        "plan_request": request,
        "request": request,
    }
    EventLog(state_dir / "events.jsonl").append(requested)
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))
    route = "/api/actions/chat-orchestrator"
    headers = {"x-zf-web-token": "test-token"}
    discussion = {
        "request_event_id": requested.id,
        "request_id": request["request_id"],
        "revision": request["revision"],
    }

    response = client.post(
        route,
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "sync": True,
            "message": "Why is Direct recommended over Research?",
            "plan_discussion": discussion,
        },
    )

    assert response.status_code == 200
    sent = json.loads(capture.read_text(encoding="utf-8"))
    prompt = sent["message"]["content"][0]["text"]
    assert "Why is Direct recommended over Research?" in prompt
    assert '"schema_version": "kanban-plan-discussion.v1"' in prompt
    assert '"request_event_id":' in prompt
    assert "Chat about this plan before I choose" not in prompt
    events = EventLog(state_dir / "events.jsonl").read_all()
    user_message = [
        event for event in events
        if event.type == "user.message"
        and event.payload.get("message")
        == "Why is Direct recommended over Research?"
    ][-1]
    persisted_discussion = user_message.payload["request"]["plan_discussion"]
    assert persisted_discussion["request_event_id"] == requested.id
    assert persisted_discussion["request_id"] == request["request_id"]
    assert persisted_discussion["questions"][0]["id"] == "route"
    assert not [
        event for event in events
        if event.type == "kanban.agent.plan.answered"
    ]

    stale = client.post(
        route,
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-a",
            "conversation_id": "kanban:project-a",
            "thread_key": "main",
            "sync": True,
            "message": "Discuss a stale revision",
            "plan_discussion": {**discussion, "revision": 2},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["status"] == "plan_request_revision_mismatch"


def test_plan_discussion_can_repair_current_invalid_draft(
    state_dir: Path,
) -> None:
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "header": "Broken route draft",
                "id": "route",
                "question": "Which route?",
                "options": [{"id": "only", "label": "Only option"}],
            },
        }),
    )
    assert request is not None and request["valid"] is False
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    source = writer.append(ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        payload={"request": request, "plan_request": request},
    ))
    requested = writer.append(ZfEvent(
        type="web.action.requested",
        actor="web",
        payload={"action": "chat-orchestrator"},
    ))

    prepared, terminal = prepare_web_plan_discussion(
        writer,
        requested=requested,
        action="chat-orchestrator",
        requested_action="chat-orchestrator",
        task_id=None,
        payload={
            "message": "Remove the unsupported fields.",
            "plan_discussion": {
                "request_event_id": source.id,
                "request_id": request["request_id"],
                "revision": request["revision"],
            },
        },
    )

    assert terminal is None
    canonical = prepared["plan_discussion"]
    assert canonical["request_valid"] is False
    assert canonical["validation_error"] == request["validation_error"]
    assert canonical["request_event_id"] == source.id


def test_chat_orchestrator_rejects_combined_plan_and_approve_output(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "fake_claude_mixed_interaction.py"
    mixed = {
        "plan_request": {
            "header": "Route",
            "id": "route",
            "question": "Which route?",
            "options": [
                {"id": "research", "label": "Research"},
                {"id": "channel", "label": "Channel"},
            ],
        },
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("create a task"),
            "payload": {
                "title": "Mixed output must not execute",
                "contract": {"behavior": "b", "verification": "v"},
            },
        },
    }
    script.write_text(
        "\n".join([
            "import json, sys",
            "sys.stdin.readline()",
            f"mixed = {mixed!r}",
            "print(json.dumps({'type':'system','session_id':'mixed-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'mixed-session','result':json.dumps(mixed)}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "sync": True,
            "message": "create a task, but ask me which route first",
        },
    )

    assert response.status_code == 422
    assert response.json()["status"] == "invalid_interaction_contract"
    reply = response.json()["reply"]
    assert "action_proposal" not in reply
    assert reply["plan_request"]["valid"] is False
    assert "mutually exclusive" in reply["plan_request"]["validation_error"]
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [
        event for event in events
        if event.type == "operator.action.proposed"
    ]


def test_chat_orchestrator_validates_workflow_proposal_against_project_config(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "fake_claude_workflow_proposal.py"
    proposal = {
        "action_proposal": {
            "action": "workflow-invoke",
            "payload": {
                "task_id": "TASK-WORKFLOW",
                "pattern_id": "delivery-smoke",
            },
            "reason": "start the declared workflow",
        }
    }
    script.write_text(
        "\n".join([
            "import json",
            f"proposal = {proposal!r}",
            "print(json.dumps({'type':'system','session_id':'claude-session-workflow'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'claude-session-workflow','result':json.dumps(proposal)}), flush=True)",
        ]),
        encoding="utf-8",
    )
    config = ZfConfig(
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="delivery-smoke",
                trigger="workflow.invoke.requested",
                topology="fanout_reader",
                roles=["delivery_worker"],
            ),
        ]),
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    client = TestClient(
        create_app(
            state_dir,
            config=config,
            project_root=state_dir.parent,
        )
    )

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "task_id": "TASK-WORKFLOW",
            "sync": True,
            "message": "start delivery-smoke",
        },
    )

    assert response.status_code == 200
    action_proposal = response.json()["reply"]["action_proposal"]
    assert action_proposal["action"] == "workflow-invoke"
    assert action_proposal["valid"] is True
    assert action_proposal["validation_error"] == ""


def test_chat_orchestrator_extracts_create_task_proposal(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    script = tmp_path / "fake_claude_create_task.py"
    proposal = {
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("创建task"),
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
            "message": "docs/prd-auth-retry.md 基于这个创建task",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["thread_key"] == "new-task-thread"
    proposal_data = data["reply"]["action_proposal"]
    assert proposal_data["action"] == "create-task"
    assert proposal_data["payload"]["title"] == "Add auth timeout retry"
    assert proposal_data["payload"]["execution_mode"] == "workflow"
    assert proposal_data["payload"]["project_id"] == "zaofu-test"
    assert proposal_data["payload"]["conversation_id"] == "kanban:zaofu-test"
    assert proposal_data["payload"]["thread_id"] == "new-task-thread"
    assert proposal_data["payload"]["run_id"] == data["turn_id"]
    assert proposal_data["payload"]["causation_id"] == data["event_id"]
    assert proposal_data["mutates_task_state"] is True
    assert proposal_data["valid"] is True

    approved = client.post(
        "/api/actions/create-task",
        headers={"x-zf-web-token": "test-token"},
        json={
            **proposal_data["payload"],
            "proposal_event_id": proposal_data["proposal_event_id"],
        },
    )

    assert approved.status_code == 201, approved.text
    task = TaskStore(state_dir / "kanban.json").get(
        approved.json()["task_id"]
    )
    assert task is not None
    assert is_workflow_managed_task(task)


def test_readonly_message_keeps_agent_contract_error_visible():
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

    assert proposal is not None
    assert proposal["valid"] is False
    assert "intent is required" in proposal["validation_error"]


def test_analyze_task_message_keeps_agent_contract_error_visible():
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

    assert proposal is not None
    assert proposal["valid"] is False
    assert "intent is required" in proposal["validation_error"]


def test_explicit_create_task_message_keeps_create_task_proposal():
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("创建一个任务"),
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


def test_proposal_list_verification_becomes_readable_text():
    """An LLM verification list must be joined into readable lines, not
    persisted as a Python-list repr string (racing-e2e kanban-autonomy)."""
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("创建任务"),
            "payload": {
                "title": "赛车小游戏 MVP",
                "contract": {
                    "verification": ["打开页面 3 秒内可开始", "按 ↑ 车辆加速"],
                },
            },
        }
    })

    proposal = _headless_action_proposal(answer, user_message="创建任务")

    assert proposal is not None
    verification = proposal["payload"]["contract"]["verification"]
    assert verification == "打开页面 3 秒内可开始\n按 ↑ 车辆加速"
    assert "['" not in verification  # not a Python-list repr


def test_proposal_prose_scope_moved_to_notes_paths_kept():
    """Prose scope entries would be gated as unmatchable path globs by writer
    fanout; keep only path-like entries in scope and preserve prose in notes."""
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("创建任务"),
            "payload": {
                "title": "赛车小游戏 MVP",
                "contract": {
                    "scope": [
                        "src/**",
                        "index.html",
                        "仅包含现代桌面浏览器 Web 页面、键盘方向键输入",
                    ],
                },
            },
        }
    })

    proposal = _headless_action_proposal(answer, user_message="创建任务")

    assert proposal is not None
    payload = proposal["payload"]
    assert payload["contract"]["scope"] == ["src/**", "index.html"]
    assert "scope(non-path):" in payload["notes"]
    assert "键盘方向键输入" in payload["notes"]


def test_proposal_cjk_prose_with_slash_is_not_path_like():
    """CJK prose has no whitespace, so a bare "/" must not qualify it as a
    path — but real CJK paths with a glob or extension stay path-like."""
    from zf.web.projections.common import _scope_entry_is_path_like

    assert not _scope_entry_is_path_like("修改src/core下的文件")
    assert not _scope_entry_is_path_like("只允许改动前端目录")
    assert _scope_entry_is_path_like("文档/说明.md")
    assert _scope_entry_is_path_like("前端/**")
    assert _scope_entry_is_path_like("src/core/**")


def test_proposal_acceptance_synonym_maps_into_verification():
    """chat-e2e F3: a real codex proposal used `acceptance` (a list) instead
    of `verification`; the schema dropped it and TASK-8EA6C8 landed with no
    acceptance criteria. Synonyms must map into verification."""
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("创建任务"),
            "payload": {
                "title": "实现键盘方向键和移动端触摸滑动操作",
                "contract": {
                    "behavior": "接入键盘与触摸输入并映射到核心移动逻辑。",
                    "acceptance": [
                        "按方向键时棋盘执行一次有效移动",
                        "移动端滑动手势触发对应方向移动",
                    ],
                },
            },
        }
    })

    proposal = _headless_action_proposal(answer, user_message="创建任务")

    assert proposal is not None and proposal["valid"] is True
    contract = proposal["payload"]["contract"]
    assert contract["verification"] == (
        "按方向键时棋盘执行一次有效移动\n移动端滑动手势触发对应方向移动"
    )
    assert "acceptance" not in contract


def test_proposal_unknown_contract_keys_preserved_in_notes():
    from zf.web.projections.common import normalize_proposed_task_contract

    payload = normalize_proposed_task_contract({
        "title": "t",
        "contract": {
            "behavior": "做某事",
            "verification": "step -> verify",
            "risk_notes": ["低端机可能掉帧"],
        },
    })

    assert "risk_notes" not in payload["contract"]
    assert "contract.risk_notes(unmapped): 低端机可能掉帧" in payload["notes"]


def test_proposal_semantically_empty_contract_is_invalid():
    """A contract whose semantic fields all normalized away must not sail
    through as valid — the task would land with no behavior/verification."""
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("创建任务"),
            "payload": {
                "title": "空语义任务",
                "contract": {"scope": ["src/**"]},
            },
        }
    })

    proposal = _headless_action_proposal(answer, user_message="创建任务")

    assert proposal is not None
    assert proposal["valid"] is False
    assert "behavior/verification" in proposal["validation_error"]


def test_proposal_without_contract_stays_title_only_valid():
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("创建任务"),
            "payload": {"title": "纯标题直建"},
        }
    })

    proposal = _headless_action_proposal(answer, user_message="创建任务")

    assert proposal is not None and proposal["valid"] is True


def test_explicit_task_proposal_message_keeps_create_task_proposal():
    from zf.web.server import _headless_action_proposal

    answer = json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": _proposal_intent("整理成一个 task proposal"),
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
            "intent": _proposal_intent("创建任务"),
            "payload": {"contract": {"behavior": "missing title"}},
        }
    }), user_message="创建任务")

    assert proposal is not None
    assert proposal["action"] == "create-task"
    assert proposal["valid"] is False
    assert "title is required" in proposal["validation_error"]
