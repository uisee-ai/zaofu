"""Provider write targets, rather than document bodies, own runtime fencing."""

import pytest

from zf.core.events.log import EventLog
from tests.test_hook_recv_codex import _invoke, _seed_scoped_worker_task


@pytest.mark.parametrize("tool_name", ["apply_patch", "Write", "Edit"])
def test_plan_document_can_quote_protected_runtime_paths(tmp_path, monkeypatch, tool_name):
    state = tmp_path / "runtime with spaces"
    state.mkdir()
    transcript = _seed_scoped_worker_task(state, ["docs/plan.md"])
    body = f"Read evidence from {state}/events.jsonl; never mutate it."
    tool_input = {
        "apply_patch": {"command": "*** Begin Patch\n*** Add File: docs/plan.md\n+"
                        + body + "\n*** End Patch"},
        "Write": {"file_path": "docs/plan.md", "content": body},
        "Edit": {"file_path": "docs/plan.md", "old_string": "Evidence", "new_string": body},
    }[tool_name]

    code = _invoke(state, event="codex.hook.pre_tool_use", backend="codex",
                   payload={"session_id": "quote-only", "transcript_path": str(transcript),
                            "tool_name": tool_name, "tool_input": tool_input}, monkeypatch=monkeypatch)

    assert code == 0
    events = EventLog(state / "events.jsonl").read_all()
    assert any(e.type == "codex.hook.pre_tool_use" for e in events)
    assert not any(e.type == "worker.runtime_write.rejected" for e in events)


@pytest.mark.parametrize("operation", ["Add File", "Update File", "Delete File", "Move to", "unknown"])
def test_runtime_patch_targets_remain_blocked(tmp_path, monkeypatch, operation):
    state = tmp_path / "runtime with spaces"
    state.mkdir()
    transcript = _seed_scoped_worker_task(state, ["docs/plan.md"])
    target = str(state / "events.jsonl")
    if operation == "Move to":
        patch = f"*** Begin Patch\n*** Update File: docs/plan.md\n*** Move to: {target}\n@@\n-a\n+b\n*** End Patch"
    elif operation == "unknown":
        patch = f"unrecognized write input targeting {target}"
    else:
        patch = f"*** Begin Patch\n*** {operation}: {target}\n*** End Patch"

    code = _invoke(state, event="codex.hook.pre_tool_use", backend="codex",
                   payload={"session_id": "protected-target", "transcript_path": str(transcript),
                            "tool_name": "apply_patch", "tool_input": {"command": patch}},
                   monkeypatch=monkeypatch)

    assert code == 2
    rejected = [e for e in EventLog(state / "events.jsonl").read_all()
                if e.type == "worker.runtime_write.rejected"]
    assert rejected[-1].payload["protected_targets"] == ["events.jsonl"]
