import io
import json
from uuid import uuid4

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_output_delivery import emit_artifact_output, observe_artifact_delivery
from zf.runtime.artifact_read_ledger import active_read_ledger_path


@pytest.mark.parametrize("change", ["format", "array_wrapper", "attempt", "delivery", "body", "filtered", "arguments"])
def test_structured_provider_witness_preserves_complete_page_identity(tmp_path, change):
    page = {"schema_version": "required-artifact-page.v2", "attempt_id": "current",
            "delivery_id": str(uuid4()), "bundle_digest": "digest", "complete": True,
            "next_cursor": None, "items": [{"source_id": "requirement", "content": "hello driver"}]}
    row = {"attempt_id": "current", "consumer_actor": "judge", "consumer_provider": "codex",
           "read_at": "2026-10-31T00:00:00+00:00"}
    emit_artifact_output(tmp_path, json.dumps(page), [row], io.StringIO())
    if change == "attempt": page["attempt_id"] = "old"
    if change == "delivery": page["delivery_id"] = str(uuid4())
    if change == "body": page["items"][0]["content"] = "hello"
    if change == "filtered": page = {"items": page["items"]}
    output = json.dumps(page, indent=2, sort_keys=True)
    if change in {"array_wrapper", "arguments"}:
        wrapper = {"chunk_id": "chunk", "output": output, "wall_time_seconds": 0.1}
        output = json.dumps([{"status": "fulfilled", "value": wrapper}], indent=2)
        if change == "arguments": output = json.dumps({"arguments": output})
    sid = str(uuid4())
    path = tmp_path / "provider.jsonl"
    registry = RoleSessionRegistry(tmp_path / "role_sessions.yaml", tmp_path.parent)
    registry.bind_codex_session("judge", sid, session_path=path)
    path.write_text("\n".join(json.dumps(r) for r in [
        {"type": "session_meta", "payload": {"id": sid}},
        {"type": "response_item", "timestamp": "2026-10-31T00:00:01+00:00",
         "payload": {"type": "function_call_output", "call_id": "call", "output": output}},
    ]))
    rows = [json.loads(line) for line in active_read_ledger_path(tmp_path, "current").read_text().splitlines()]
    result = observe_artifact_delivery(tmp_path, rows)
    assert result[0]["provider_observation_scope"] == "historical_tool_output"
    assert result[0]["current_context_availability"] == "unknown"
    assert result[0]["provider_delivery_status"] == (
        "observed_in_transcript" if change in {"format", "array_wrapper"} else "unknown")
    if change == "format":
        # Sealed history remains valid after compaction/session rotation; it
        # does not claim that the full page is still present in model context.
        registry.clear("judge")
        assert observe_artifact_delivery(tmp_path, result) == result
