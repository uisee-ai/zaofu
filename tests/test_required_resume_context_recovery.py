"""Compaction must not silently turn complete historical delivery into a reread."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest


def test_complete_delivery_after_compaction_returns_context_recovery_without_body(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    source = state / "artifacts/input.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"body": "frozen obligation"}))
    descriptor = {"source_id": "s", "artifact_id": "a", "ref": "artifacts/input.json",
                  "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "allowed_paths": ["$.body"]}
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
        dispatch_id="attempt-1", sources=[descriptor], metadata={"read_purpose": "implementation"})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest, operation_id="op-1")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for kind in ("workflow.operation.requested", "workflow.operation.started"):
        writer.append(ZfEvent(type=kind, payload={"operation_id": "op-1", "active_attempt_id": "attempt-1"}))
    writer.append(ZfEvent(type="task.dispatched", payload={"attempt_id": "attempt-1", "required_reads": [
        {"source_id": "s", "artifact_id": "a", "artifact_sha256": descriptor["sha256"], "json_path": "$.body"}]}))
    command = ["artifact", "read", "--attempt", "attempt-1", "--state-dir", str(state), "--required"]
    assert main(command) == 0
    original = capsys.readouterr().out.strip()
    assert json.loads(original)["complete"]
    session = str(uuid4())
    transcript = tmp_path / "codex.jsonl"
    RoleSessionRegistry(state / "role_sessions.yaml", tmp_path).bind_codex_session("dev-1", session, session_path=transcript)
    now = datetime.now(timezone.utc)
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": session}},
        {"timestamp": (now + timedelta(seconds=1)).isoformat(), "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "call-1", "output": original}},
        {"timestamp": (now + timedelta(seconds=2)).isoformat(), "type": "compacted", "payload": {}},
    ]) + "\n")
    assert main([*command, "--resume"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["items"] == []
    assert resumed["continuation"]["mode"] == "context_recovery"
    assert resumed["continuation"]["historical_delivery"] == "complete"
    assert resumed["continuation"]["current_memory"] == "unknown"
    assert resumed["next_action"] == "recover_work_record_then_retrieve_context_gaps"
    # An explicit full read remains available; no source or permission is dropped.
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["content"] == "frozen obligation"


def test_acceptance_verdict_error_names_field_and_allowed_values():
    import pytest
    from tests.test_product_acceptance import _report, _spec
    from zf.runtime.product_acceptance import ProductAcceptanceError, validate_product_acceptance_report

    report = _report({"ref": "artifacts/spec.json", "sha256": "a" * 64})
    report["verdict"] = "fail"
    with pytest.raises(ProductAcceptanceError) as error:
        validate_product_acceptance_report(report, spec=_spec(), expected={})
    assert "product_acceptance_report.verdict" in str(error.value)
    assert "passed, rejected, blocked" in str(error.value)
