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


@pytest.mark.parametrize("unauthorized_alias", [False, True])
def test_identical_ports_share_delivery_but_not_authorization_or_receipts(
    tmp_path, monkeypatch, capsys, unauthorized_alias,
):
    state = tmp_path / ".runtime"
    path = state / "artifacts" / "plan.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"facts": "a" * 5000, "secret": "excluded"}))
    sources = [{"source_id": name, "artifact_id": name, "ref": "artifacts/plan.json",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "allowed_paths": ["$.facts"]} for name in ("task_map", "planning_result")]
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
        dispatch_id="attempt-1", sources=sources, metadata={"read_purpose": "implementation"},
    )
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    requirements = [{"source_id": row["source_id"], "artifact_id": row["artifact_id"],
                     "artifact_sha256": row["sha256"], "json_path": "$.facts"}
                    for row in sources]
    if unauthorized_alias:
        requirements[1]["json_path"] = "$.secret"
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T1",
        payload={"dispatch_id": "attempt-1", "required_reads": requirements},
    ))
    result = main(["artifact", "read", "--attempt", "attempt-1", "--required",
                   "--state-dir", str(state)])
    output = capsys.readouterr()
    ledger = state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl"
    if unauthorized_alias:
        assert result == 1
        assert not ledger.exists()
        assert not output.out
    else:
        assert result == 0, output.err
        delivered = json.loads(output.out)
        assert delivered["source_id"] == "task_map"
        assert delivered["aliases"][0]["source_id"] == "planning_result"
        assert delivered["content"] == "a" * 5000
        assert len(output.out) < 8192
        reads = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert len(reads) == 2
        assert {row["source_id"] for row in reads} == {"task_map", "planning_result"}
        assert all(row["artifact_sha256"] == sources[0]["sha256"] and not row["truncated"] for row in reads)


def test_large_required_read_must_be_delivered_in_complete_pages(tmp_path, monkeypatch, capsys):
    state = tmp_path / ".runtime"
    path = state / "artifacts" / "large.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"facts": "中" * 10000}, ensure_ascii=False))
    source = {"source_id": "large", "artifact_id": "large", "ref": "artifacts/large.json",
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "allowed_paths": ["$.facts"]}
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
                                             dispatch_id="attempt-1", sources=[source],
                                             metadata={"read_purpose": "implementation"})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    requirement = {"source_id": "large", "artifact_id": "large", "artifact_sha256": source["sha256"],
                   "json_path": "$.facts"}
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T1",
        payload={"dispatch_id": "attempt-1", "required_reads": [requirement]},
    ))
    common = ["artifact", "read", "--attempt", "attempt-1", "--state-dir", str(state)]
    ledger = state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl"
    from zf.runtime.artifact_read_ledger import required_read_satisfied

    cursor, contents = "", []
    while True:
        assert main([*common, "--required", "--cursor", cursor]) == 0
        output = capsys.readouterr().out
        assert len(output) <= 8192
        page = json.loads(output)
        contents.extend(item["content"] for item in page["items"])
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        cursor = page["next_cursor"]
        assert required_read_satisfied(rows, requirement) is (cursor is None)
        if not cursor:
            break
    assert "".join(contents) == "中" * 10000
    before = ledger.read_bytes()
    assert main([*common, "--required", "--cursor", "bad:0:0"]) == 1
    assert "cursor" in capsys.readouterr().err
    assert ledger.read_bytes() == before
