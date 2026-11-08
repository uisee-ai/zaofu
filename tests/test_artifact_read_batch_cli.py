"""One tool round trip preserves all scoped artifact consumption evidence."""
import hashlib
import json

import pytest

from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest
from tests.test_artifact_read_capability import _grant


@pytest.mark.parametrize("to_file", [False, True])
@pytest.mark.parametrize("fault", ["", "tampered", "missing_policy", "forged_identity"])
def test_required_read_batch_preserves_authentication_slices_and_hashes(tmp_path, monkeypatch, capsys, fault, to_file):
    state = tmp_path / ".runtime"
    if to_file:
        import subprocess
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        (tmp_path / ".gitignore").write_text("__pycache__/\n")
        monkeypatch.chdir(tmp_path)
    cache = tmp_path / "app/__pycache__/required.jsonl"
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
    result = main(["artifact", "read", "--attempt", "attempt-1", "--required", "--state-dir", str(state)]
                  + (["--output-file", str(cache)] if to_file else []))
    output = capsys.readouterr()
    ledger = state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl"
    if fault:
        assert result == 1
        assert not ledger.exists()
        assert not output.out
        assert not cache.exists()
    else:
        assert result == 0, output.err
        if to_file:
            receipt = json.loads(output.out)
            assert receipt["model_consumption_proven"] is False
            assert receipt["sha256"] == hashlib.sha256(cache.read_bytes()).hexdigest()
        returned = [json.loads(line) for line in (cache.read_text() if to_file else output.out).splitlines()]
        assert len(returned) == 2
        assert all(set(row) == {"source_id", "artifact_id", "json_path", "content"} for row in returned)
        assert all("中文" in row["content"] and "not-authorized" not in row["content"] for row in returned)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert {row["source_id"] for row in rows} == {"first", "second"}
        assert all(row["consumer_actor"] == "dev-1" and not row["truncated"] for row in rows)


def test_required_read_batch_rejects_worker_slice_override(capsys):
    assert main(["artifact", "read", "--attempt", "unused", "--required", "--max-chars", "1"]) == 1
    assert "cannot be combined" in capsys.readouterr().err


@pytest.mark.parametrize("fault", ["", "denied_alias", "disallowed_path", "wrong_digest"])
def test_deduplicated_batch_keeps_each_source_authority_and_receipt(
    tmp_path, monkeypatch, capsys, fault,
):
    from zf.runtime.artifact_read_ledger import (
        build_input_consumption_policy, seal_read_ledger, validate_required_reads,
    )

    state = tmp_path / ".runtime"
    path = state / "artifacts/plan.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"facts": ["中文" * 100], "excluded": "not-authorized"}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sources = [{"source_id": name, "artifact_id": name,
                "ref": "artifacts/plan.json", "sha256": digest,
                "allowed_paths": ["$.facts"], "occurrence_id": f"occurrence-{name}"}
               for name in ("task_map", "planning_result")]
    if fault == "denied_alias":
        sources[1]["access_scope"] = {"actor": "another-actor"}
    elif fault == "disallowed_path":
        sources[1]["allowed_paths"] = ["$.excluded"]
    elif fault == "wrong_digest":
        sources[1]["sha256"] = "f" * 64
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
        dispatch_id="attempt-1", sources=sources,
        metadata={"read_purpose": "implementation"},
    )
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    required = [{"source_id": s["source_id"], "artifact_id": s["artifact_id"],
                 "artifact_sha256": s["sha256"], "json_path": "$.facts"} for s in sources]
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T1",
        payload={"dispatch_id": "attempt-1", "required_reads": required},
    ))
    result = main(["artifact", "read", "--attempt", "attempt-1", "--required",
                   "--deduplicate-content", "--state-dir", str(state)])
    output = capsys.readouterr()
    returned = [json.loads(line) for line in output.out.splitlines()]
    rows = [json.loads(line) for line in
            (state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl").read_text().splitlines()]
    if fault:
        assert result == 1
        assert len(returned) == 1
        assert [r["source_id"] for r in rows if r["status"] == "read"] == ["task_map"]
        return
    assert result == 0, output.err
    assert len(returned) == 2
    assert "中文" in returned[0]["content"] and "not-authorized" not in output.out
    assert "content" not in returned[1]
    assert returned[1]["content_ref"] == returned[0]["content_id"]
    assert {r["read_receipt"]["occurrence_id"] for r in returned} == {
        "occurrence-task_map", "occurrence-planning_result",
    }
    ledger_lines = (state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl").read_bytes().splitlines()
    receipts = {hashlib.sha256(line).hexdigest(): json.loads(line) for line in ledger_lines}
    for output_row in returned:
        locator = output_row["read_receipt"]
        receipt = receipts[locator["row_sha256"]]
        assert receipt["attempt_id"] == locator["attempt_id"] == "attempt-1"
        assert receipt["occurrence_id"] == locator["occurrence_id"]
        assert receipt["source_id"] == output_row["source_id"]
        assert receipt["artifact_id"] == output_row["artifact_id"]
        assert receipt["json_path"] == output_row["json_path"]
        assert receipt["consumer_actor"] == "dev-1"
    assert {r["source_id"] for r in rows} == {"task_map", "planning_result"}
    policy = build_input_consumption_policy(
        workflow_run_id="run-1", attempt_id="attempt-1", required_reads=required,
    )
    assert validate_required_reads(state, policy=policy,
                                   ledger_descriptor=seal_read_ledger(state, "attempt-1")) == []
    # A fresh invocation must deliver the body again; old receipts are not a cache.
    assert main(["artifact", "read", "--attempt", "attempt-1", "--required",
                 "--deduplicate-content", "--state-dir", str(state)]) == 0
    assert "content" in json.loads(capsys.readouterr().out.splitlines()[0])


def test_deduplicate_flag_requires_batch(capsys):
    assert main(["artifact", "read", "--attempt", "unused", "--source", "a",
                 "--artifact", "a", "--deduplicate-content"]) == 1
    assert "requires --required" in capsys.readouterr().err


@pytest.mark.parametrize("fault", ["", "denied_alias", "wrong_digest"])
def test_shared_bodies_cli_keeps_authorized_cross_file_reads(tmp_path, monkeypatch, capsys, fault):
    from tests.test_artifact_shared_bodies import decode

    state = tmp_path / ".runtime"
    common = {"text": "完整正文" * 100}
    sources = []
    values = [{"shared": common, "name": name} for name in ("first", "second")]
    for name, value in zip(("first", "second"), values):
        path = state / "artifacts" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"facts": value, "excluded": "not-authorized"}))
        sources.append({"source_id": name, "artifact_id": name, "ref": f"artifacts/{name}.json",
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "allowed_paths": ["$.facts"]})
    if fault == "denied_alias":
        sources[1]["access_scope"] = {"actor": "another-actor"}
    elif fault == "wrong_digest":
        sources[1]["sha256"] = "f" * 64
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
                                             dispatch_id="attempt-1", sources=sources,
                                             metadata={"read_purpose": "implementation"})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T1",
        payload={"dispatch_id": "attempt-1", "required_reads": [
            {"source_id": item["source_id"], "artifact_id": item["artifact_id"],
             "artifact_sha256": item["sha256"], "json_path": "$.facts"} for item in sources]},
    ))
    result = main(["artifact", "read", "--attempt", "attempt-1", "--required", "--shared-bodies",
                   "--state-dir", str(state)])
    output = capsys.readouterr()
    ledger = state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    if fault:
        assert result == 1
        assert not output.out
        assert [r["source_id"] for r in rows if r["status"] == "read"] == ["first"]
        return
    assert result == 0, output.err
    records = [json.loads(line) for line in output.out.splitlines()]
    assert decode(records) == [json.dumps(v, ensure_ascii=False, indent=2) for v in values]
    assert output.out.count(common["text"]) == 1
    assert "not-authorized" not in output.out
    assert {r["source_id"] for r in rows} == {"first", "second"}
    receipts = {hashlib.sha256(line).hexdigest() for line in ledger.read_bytes().splitlines()}
    assert all(r["read_receipt"]["row_sha256"] in receipts for r in records if r["kind"] == "source")
