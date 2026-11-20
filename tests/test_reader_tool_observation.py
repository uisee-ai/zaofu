"""A missing telemetry result must not hide a bound, completed native tool call."""
import json

import pytest

from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_reader_progress_continuation import read_page, close_turn


@pytest.mark.parametrize("fault", ["", "missing_output", "session", "call", "input", "future", "duplicate", "unregistered"])
def test_reader_reconciles_only_exact_native_tool_completion(tmp_path, fault):
    state, log, transport, runtime, _, child = _durable_reader_state(tmp_path)
    use = runtime.event_writer.append(ZfEvent(type="agent.tool.use", actor="verify-1",
        payload={"tool": "exec_command",
        "tool_use_id": "call-pending", "input": '{"cmd":"cat briefing.md"}'}))
    registry = RoleSessionRegistry(state / "role_sessions.yaml", state.parent)
    session = registry.get_or_create("verify-1", backend="codex")
    path = tmp_path / "rollout.jsonl"
    registry.bind_codex_session("verify-1", str(session), session_path=path)
    call = {"type": "response_item", "payload": {"type": "function_call",
        "call_id": "other" if fault == "call" else "call-pending", "name": "exec_command",
        "arguments": '{"cmd":"different"}' if fault == "input" else use.payload["input"]}}
    output = {"type": "response_item", "timestamp": "2099-01-01T00:00:01Z" if fault == "future" else use.ts,
        "payload": {"type": "function_call_output", "call_id": "call-pending",
                    "output": "Process exited with code 0\nOutput:\nbriefing"}}
    rows = [{"type": "session_meta", "payload": {"id": "foreign" if fault == "session" else str(session)}}, call]
    if fault != "missing_output":
        rows.append(output)
    if fault == "duplicate":
        rows.append(output)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    if fault == "unregistered":
        registry.path.unlink()
    read_page(state, child, 0)
    close_turn(runtime, 1)
    claims = [e for e in log.read_all() if e.type == "worker.continuation.claimed"]
    if fault:
        assert len(transport.sent) == 1
        assert not claims
    else:
        assert len(transport.sent) == 2
        observed = claims[-1].payload["tool_result_observations"]
        assert observed[0]["tool_use_id"] == "call-pending"
        witness = hydrate_sidecar_ref(state, observed[0]["observation_ref"]).payload
        assert witness["tool_use_event_id"] == use.id
        assert witness["session_id"] == str(session)
        assert witness["result"] == output
        assert not any(e.type in {"verify.child.completed", "task.done"} for e in log.read_all())
