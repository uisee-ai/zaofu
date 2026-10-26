"""One tool round trip preserves all scoped artifact consumption evidence."""
import hashlib
import json

import pytest

from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest
from tests.test_artifact_read_capability import _grant


@pytest.mark.parametrize("fault", ["", "tampered", "missing_policy", "forged_identity"])
def test_required_read_batch_preserves_authentication_slices_and_hashes(tmp_path, monkeypatch, capsys, fault):
    state = tmp_path / ".runtime"
    sources = []
    for name in ("first", "second"):
        path = state / "artifacts" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"facts": [name, "中文"], "excluded": "not-authorized"}))
        sources.append({"source_id": name, "artifact_id": name, "ref": f"artifacts/{name}.json",
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "allowed_paths": ["$.facts"]})
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
                                             dispatch_id="attempt-1", sources=sources,
                                             metadata={"read_purpose": "implementation"})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    if fault != "missing_policy":
        EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
            type="task.dispatched", actor="orchestrator", task_id="T1",
            payload={"dispatch_id": "attempt-1", "required_reads": [
                {"source_id": item["source_id"], "artifact_id": item["artifact_id"],
                 "artifact_sha256": item["sha256"], "json_path": "$.facts"} for item in sources]},
        ))
    if fault == "tampered":
        (state / "artifacts/first.json").write_text('{"facts": ["changed"]}')
    elif fault == "forged_identity":
        monkeypatch.setenv("ZF_ROLE_NAME", "impostor")
    result = main(["artifact", "read", "--attempt", "attempt-1", "--required", "--state-dir", str(state)])
    output = capsys.readouterr()
    ledger = state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl"
    if fault:
        assert result == 1
        assert not ledger.exists()
        assert not output.out
    else:
        assert result == 0, output.err
        returned = [json.loads(line) for line in output.out.splitlines()]
        assert len(returned) == 2
        assert all("中文" in row["content"] and "not-authorized" not in row["content"] for row in returned)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert {row["source_id"] for row in rows} == {"first", "second"}
        assert all(row["consumer_actor"] == "dev-1" and not row["truncated"] for row in rows)


def test_required_read_batch_rejects_worker_slice_override(capsys):
    assert main(["artifact", "read", "--attempt", "unused", "--required", "--max-chars", "1"]) == 1
    assert "cannot be combined" in capsys.readouterr().err
