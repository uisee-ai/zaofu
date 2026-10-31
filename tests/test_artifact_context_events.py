from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_context_events import ArtifactContextEvents
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.workflow_operation import reduce_workflow_operations


def test_context_matches_ledger_while_skipping_irrelevant_hook_bodies(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="workflow.operation.requested", payload={"operation_id": "op"}))
    log.append(ZfEvent(type="workflow.operation.started", payload={"operation_id": "op", "active_attempt_id": "a"}))
    log.append(ZfEvent(type="task.dispatched", payload={"attempt_id": "a", "required_reads": [{"source_id": "s"}]}))
    log.append(ZfEvent(type="codex.hook.post_tool_use", payload={"tool_response": "x" * 100_000}))
    # A hook that actually carries a declaration cannot be skipped.
    log.append(ZfEvent(type="codex.hook.post_tool_use", payload={"attempt_id": "a", "required_reads": [{"source_id": "later"}]}))
    full = log.read_all()
    view = ArtifactContextEvents(log)
    selected = view.read_all()
    assert len(selected) == len(full) - 1
    assert reduce_workflow_operations(selected) == reduce_workflow_operations(full)
    assert required_reads_for_attempt(selected, "a") == required_reads_for_attempt(full, "a")

    log.append(ZfEvent(type="workflow.operation.cancelled", payload={"operation_id": "op"}))
    assert reduce_workflow_operations(view.read_all())["op"]["status"] == "cancelled"


def test_ambiguous_legacy_lines_and_partial_writes_are_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    # Duplicate top-level type follows json.loads' last-key semantics.
    duplicate = ('{"type":"codex.hook.post_tool_use","type":"workflow.operation.blocked",'
                 '"id":"duplicate","payload":{"operation_id":"op"}}\n')
    # Escaped key must still be considered a required-read declaration.
    escaped = ('{"type":"codex.hook.post_tool_use","id":"escaped",'
               '"payload":{"attempt_id":"a","required\\u005freads":[{"source_id":"escaped"}]}}\n')
    path.write_text(duplicate + escaped + '{"type":"partial"', encoding="utf-8")
    view = ArtifactContextEvents(log)
    assert [e.id for e in view.read_all()] == ["duplicate", "escaped"]
    assert required_reads_for_attempt(view.read_all(), "a")[0]["source_id"] == "escaped"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(',"id":"complete","payload":{}}\n')
    assert [e.id for e in view.read_all()] == ["duplicate", "escaped", "complete"]


def test_signed_and_rotated_events_preserve_verification_and_order(tmp_path: Path) -> None:
    from zf.core.security.signing import EventSigner

    path = tmp_path / "events.jsonl"
    log = EventLog(path, signer=EventSigner(b"secret"))
    log.append(ZfEvent(type="workflow.operation.requested", payload={"operation_id": "op"}))
    archive = tmp_path / "events"
    archive.mkdir()
    path.rename(archive / "2026-10-30.jsonl")
    log.append(ZfEvent(type="workflow.operation.started", payload={"operation_id": "op", "active_attempt_id": "a"}))
    view = ArtifactContextEvents(log)
    assert [e.id for e in view.read_all()] == [e.id for e in log.read_all()]
    import os
    before = path.stat()
    path.write_text(path.read_text().replace("workflow.operation.started", "workflow.operation.blocked"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert [e.type for e in view.read_all()] == ["workflow.operation.requested"]


def test_reader_operation_policy_sidecar_remains_authoritative(tmp_path: Path) -> None:
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    log = EventLog(tmp_path / "events.jsonl")
    policy = write_immutable_json_sidecar(tmp_path, {
        "attempt_id": "a", "required_reads": [{"source_id": "sidecar"}],
    }, root="tests/policy", kind="policy", schema_version="policy.v1", created_by="test")
    request = write_immutable_json_sidecar(tmp_path, {
        "request": {"input_consumption_policy_ref": policy},
    }, root="tests/request", kind="request", schema_version="request.v1", created_by="test")
    log.append(ZfEvent(type="workflow.operation.requested", payload={"operation_id": "op", "request_ref": request}))
    log.append(ZfEvent(type="codex.hook.post_tool_use", payload={"tool_response": "x" * 100_000}))
    rows = required_reads_for_attempt(ArtifactContextEvents(log).read_all(), "a", state_dir=tmp_path, operation_id="op")
    assert rows == [{"source_id": "sidecar"}]


def test_rotation_during_snapshot_falls_back_to_complete_ledger(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    archive = tmp_path / "events"
    archive.mkdir()
    (archive / "2026-10-29.jsonl").write_text(ZfEvent(type="older", id="older").to_json() + "\n")
    path.write_text(ZfEvent(type="rotated", id="rotated").to_json() + "\n")
    decode = log.decode_line
    moved = False

    def rotate_once(line):
        nonlocal moved
        if not moved:
            moved = True
            path.rename(archive / "2026-10-30.jsonl")
            path.write_text(ZfEvent(type="current", id="current").to_json() + "\n")
        return decode(line)

    monkeypatch.setattr(log, "decode_line", rotate_once)
    assert [event.id for event in ArtifactContextEvents(log).read_all()] == ["older", "rotated", "current"]
