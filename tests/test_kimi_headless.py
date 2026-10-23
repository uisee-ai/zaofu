"""Kimi root canary protocol tests using a local process, without a provider."""
import json
from pathlib import Path
import shlex
import sys

import pytest

from zf.web.headless_agent import KanbanHeadlessAgent, canonical_headless_backend
from zf.web.kimi_headless import KimiHeadlessBackend


def backend(tmp_path: Path, body: str) -> KimiHeadlessBackend:
    script = tmp_path / "provider.py"
    script.write_text("import json, sys, time\nfrom pathlib import Path\n" + body)
    return KimiHeadlessBackend(command=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")


def turn(adapter, tmp_path, **overrides):
    args = dict(prompt="Review supplied text", cwd=tmp_path, system_prompt="Use only supplied text",
                thread_id="thread", provider_session_id="", on_session_id=lambda _: None,
                on_message=None, timeout_s=3)
    args.update(overrides)
    return adapter.run_turn(**args)


def test_registered_canary_caller_applies_tool_free_profile(tmp_path):
    adapter = backend(tmp_path, '''
profile = Path(sys.argv[sys.argv.index('--agent-file') + 1]).read_text()
assert 'tools: []' in profile and 'subagents: []' in profile
assert '--session' not in sys.argv
assert sys.argv[sys.argv.index('--output-format') + 1] == 'stream-json'
print(json.dumps({'role': 'assistant', 'content': 'supplied requirement reviewed'}))
''')
    assert canonical_headless_backend(adapter.backend_id) == adapter.backend_id
    agent = KanbanHeadlessAgent(state_dir=tmp_path / "state", project_root=tmp_path,
                               backends={adapter.backend_id: adapter})
    result = agent.run_turn(message="Review this requirement: return one report", backend=adapter.backend_id,
                            permission_profile="read_only")
    assert result.ok
    assert result.reply == "supplied requirement reviewed"
    assert result.completion_reason == "root_turn_only"
    assert result.provider_session_id == ""
    assert not (tmp_path / "state" / "kanban.json").exists()


@pytest.mark.parametrize("row", [
    {"role": "assistant", "content": "done", "tool_calls": [{"function": {"name": "Agent"}}]},
    {"role": "tool", "content": "result"}, {"type": "error", "message": "failed"},
    {"role": "meta", "type": "turn.step.retrying"}, [],
])
def test_rejects_tools_errors_and_missing_root_result(tmp_path, row):
    adapter = backend(tmp_path, f"print({json.dumps(json.dumps(row))})\n")
    assert not turn(adapter, tmp_path).ok


def test_zero_exit_with_unsettled_background_is_not_success(tmp_path):
    adapter = backend(tmp_path, "print('Warning: print background policy failed', file=sys.stderr)\n"
                      "print(json.dumps({'role': 'assistant', 'content': 'done'}))\n")
    assert turn(adapter, tmp_path).status == "failed"


@pytest.mark.parametrize("override", [{"provider_session_id": "old-session"},
                                     {"permission_profile": "workspace_writer"},
                                     {"thinking_level": "high"},
                                     {"image_inputs": [{"path": "x.png"}]}])
def test_unadmitted_modes_never_launch_provider(tmp_path, override):
    adapter = backend(tmp_path, "Path('launched').touch()\n")
    assert turn(adapter, tmp_path, **override).status == "unsupported"
    assert not (tmp_path / "launched").exists()


def test_timeout_terminates_canary_process(tmp_path):
    adapter = backend(tmp_path, "time.sleep(60)\n")
    assert turn(adapter, tmp_path, timeout_s=0.05).status == "timeout"


def test_unbounded_provider_output_is_stopped_before_deadline(tmp_path):
    adapter = backend(tmp_path, "print('x' * 2_000_001, flush=True)\ntime.sleep(60)\n")
    result = turn(adapter, tmp_path, timeout_s=3)
    assert result.status == "failed"
    assert "output limit" in result.error


def test_web_entry_reaches_explicit_canary_without_task_mutation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    adapter = backend(tmp_path, "print(json.dumps({'role':'assistant','content':'bounded answer'}))\n")
    monkeypatch.setenv("ZF_KIMI_CANARY_CMD", adapter.command)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "canary-test")
    state = tmp_path / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]")
    (state / "feature_list.json").write_text("[]")
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post("/api/actions/chat-orchestrator", headers={"x-zf-web-token": "canary-test"},
                           json={"backend": adapter.backend_id, "permission_profile": "read_only",
                                 "sync": True, "message": "解释当前给出的需求，不执行任务"})
    assert response.status_code == 200, response.text
    assert "bounded answer" in response.text
    assert json.loads((state / "kanban.json").read_text()) == []


def test_cancel_running_canary_uses_shared_process_registry(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from zf.web.agent_session_runtime import cancel_agent_session_run, run_key

    adapter = backend(tmp_path, "Path('started').touch()\ntime.sleep(60)\n")
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(turn, adapter, tmp_path, run_id="kimi-cancel", timeout_s=5)
        deadline = time.monotonic() + 3
        while not (tmp_path / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "started").exists()
        cancel_agent_session_run(run_key(run_id="kimi-cancel", thread_id="thread"))
        assert future.result(timeout=3).status == "cancelled"
