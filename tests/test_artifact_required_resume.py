"""Resume required pages only from this operation's observed provider output."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest


@pytest.mark.parametrize("witness", ["observed", "missing", "wrong_session", "truncated",
                                      "wrong_bundle", "wrong_operation", "wrong_actor", "gap", "tampered_output"])
def test_cli_resume_uses_only_complete_output_observed_in_current_session(tmp_path, monkeypatch, capsys, witness):
    state = tmp_path / "state"
    source_path = state / "artifacts" / "input.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(json.dumps({"body": "中文" * 10000}, ensure_ascii=False))
    source = {"source_id": "s", "artifact_id": "a", "ref": "artifacts/input.json",
              "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(), "allowed_paths": ["$.body"]}
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
        dispatch_id="attempt-1", sources=[source], metadata={"read_purpose": "implementation"})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest, operation_id="op-1")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for typ in ("workflow.operation.requested", "workflow.operation.started"):
        writer.append(ZfEvent(type=typ, payload={"operation_id": "op-1", "active_attempt_id": "attempt-1"}))
    writer.append(ZfEvent(type="task.dispatched", payload={"attempt_id": "attempt-1", "required_reads": [
        {"source_id": "s", "artifact_id": "a", "artifact_sha256": source["sha256"], "json_path": "$.body"}]}))
    command = ["artifact", "read", "--attempt", "attempt-1", "--state-dir", str(state), "--required"]
    assert main(command) == 0
    first_raw = capsys.readouterr().out.strip()
    first = json.loads(first_raw)
    assert first["next_cursor"]
    if witness in {"wrong_bundle", "wrong_operation", "wrong_actor", "gap", "tampered_output"}:
        from zf.runtime.artifact_read_ledger import active_read_ledger_path

        ledger = active_read_ledger_path(state, "attempt-1")
        row = json.loads(ledger.read_text())
        if witness == "wrong_bundle":
            row["bundle_digest"] = "0" * 64
        elif witness == "wrong_operation":
            row["operation_id"] = "other-operation"
        elif witness == "wrong_actor":
            row["consumer_actor"] = "other-role"
        elif witness == "gap":
            row["page_start"] = 1
        else:
            (state / row["output_ref"]["ref"]).write_text('{"tampered":true}')
        ledger.write_text(json.dumps(row) + "\n")

    sid = str(uuid4())
    transcript = tmp_path / "codex.jsonl"
    RoleSessionRegistry(state / "role_sessions.yaml", tmp_path).bind_codex_session("dev-1", sid, session_path=transcript)
    if witness != "missing":
        transcript.write_text("\n".join(json.dumps(x) for x in [
            {"type": "session_meta", "payload": {"id": str(uuid4()) if witness == "wrong_session" else sid}},
            {"timestamp": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(), "type": "response_item",
             "payload": {"type": "function_call_output", "call_id": "call-1",
                         "output": first_raw[:50] if witness == "truncated" else first_raw}},
        ]) + "\n")

    assert main([*command, "--resume"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["items"][0]["page_start"] == (first["items"][0]["page_end"] if witness == "observed" else 0)


def test_resume_requires_matching_operation_and_gap_free_pages(tmp_path, monkeypatch, capsys):
    from zf.cli.artifact_required_resume import resume_position
    from zf.runtime.artifact_read_ledger import read_attempt_artifact

    state = tmp_path / "state"
    source = state / "artifacts" / "input.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"body": "x" * 10000}))
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
        dispatch_id="attempt-1", sources=[{"source_id": "s", "artifact_id": "a", "ref": "artifacts/input.json",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "allowed_paths": ["$.body"]}],
        metadata={"read_purpose": "implementation"})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest, operation_id="op-1")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for typ in ("workflow.operation.requested", "workflow.operation.started"):
        writer.append(ZfEvent(type=typ, payload={"operation_id": "op-1", "active_attempt_id": "attempt-1"}))
    writer.append(ZfEvent(type="task.dispatched", payload={"attempt_id": "attempt-1", "required_reads": [
        {"source_id": "s", "artifact_id": "a", "json_path": "$.body"}]}))
    cmd = ["artifact", "read", "--attempt", "attempt-1", "--state-dir", str(state), "--required"]
    sid, transcript = str(uuid4()), tmp_path / "codex.jsonl"
    RoleSessionRegistry(state / "role_sessions.yaml", tmp_path).bind_codex_session("dev-1", sid, session_path=transcript)
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid}}) + "\n")
    assert main(cmd) == 0
    raw = capsys.readouterr().out.strip()
    page = json.loads(raw)
    with transcript.open("a") as f:
        f.write(json.dumps({"timestamp": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            "type": "response_item", "payload": {"type": "function_call_output", "call_id": "1", "output": raw}}) + "\n")
    assert main([*cmd, "--resume"]) == 0
    raw2 = capsys.readouterr().out.strip()
    page2 = json.loads(raw2)
    assert page2["items"][0]["page_start"] == page["items"][0]["page_end"]
    assert page2["complete"]
    with transcript.open("a") as f:
        f.write(json.dumps({"timestamp": (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
            "type": "response_item", "payload": {"type": "function_call_output", "call_id": "2", "output": raw2}}) + "\n")
    assert main([*cmd, "--resume"]) == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    from zf.runtime.artifact_read_ledger import seal_read_ledger
    from zf.runtime.artifact_read_ledger_validation import validate_required_reads

    ledger = seal_read_ledger(state, "attempt-1")
    assert validate_required_reads(state, policy={"attempt_id": "attempt-1", "required_reads": [
        {"source_id": "s", "artifact_id": "a", "json_path": "$.body"}]}, ledger_descriptor=ledger) == []
    identity = {"actor": "dev-1", "role": "dev", "provider": "codex", "purpose": "implementation", "operation_id": "another-op"}
    preview = read_attempt_artifact(state, manifest=manifest, source_id="s", artifact_id="a", json_path="$.body",
        record=False, actor="dev-1", role="dev", provider="codex", purpose="implementation")
    groups = [{"source_id": "s", "artifact_id": "a", "json_path": "$.body", "content": preview["content"]}]
    assert resume_position(state, manifest=manifest, identity=identity, bundle_digest=page["bundle_digest"],
        groups=groups, receipts=[[preview["read"]]]) == (0, 0)


@pytest.mark.parametrize("missing_alias", [False, True])
def test_resume_requires_independent_receipts_for_identical_aliases(tmp_path, monkeypatch, capsys, missing_alias):
    from zf.runtime.artifact_read_ledger import active_read_ledger_path

    state = tmp_path / "state"
    path = state / "artifacts" / "input.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"body": "x" * 10000}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sources = [{"source_id": name, "artifact_id": "a", "ref": "artifacts/input.json",
                "sha256": digest, "allowed_paths": ["$.body"]} for name in ("first", "second")]
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
        dispatch_id="attempt-1", sources=sources, metadata={"read_purpose": "implementation"})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest, operation_id="op-1")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for typ in ("workflow.operation.requested", "workflow.operation.started"):
        writer.append(ZfEvent(type=typ, payload={"operation_id": "op-1", "active_attempt_id": "attempt-1"}))
    writer.append(ZfEvent(type="task.dispatched", payload={"attempt_id": "attempt-1", "required_reads": [
        {"source_id": name, "artifact_id": "a", "json_path": "$.body"} for name in ("first", "second")]}))
    cmd = ["artifact", "read", "--attempt", "attempt-1", "--state-dir", str(state), "--required"]
    assert main(cmd) == 0
    raw = capsys.readouterr().out.strip()
    first = json.loads(raw)
    assert first["items"][0]["aliases"][0]["source_id"] == "second"
    if missing_alias:
        ledger = active_read_ledger_path(state, "attempt-1")
        ledger.write_text(ledger.read_text().splitlines()[0] + "\n")
    sid, transcript = str(uuid4()), tmp_path / "codex.jsonl"
    RoleSessionRegistry(state / "role_sessions.yaml", tmp_path).bind_codex_session("dev-1", sid, session_path=transcript)
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": sid}},
        {"timestamp": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(), "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "1", "output": raw}},
    ]) + "\n")
    assert main([*cmd, "--resume"]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["items"][0]["page_start"] == (0 if missing_alias else first["items"][0]["page_end"])
