"""Bounded batch reads preserve every scoped artifact consumption receipt."""
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

    cursor = None
    contents = []
    while True:
        assert main([*common, "--required", *(["--cursor", cursor] if cursor else [])]) == 0
        output = capsys.readouterr()
        assert len(output.out.rstrip("\n")) <= 8192
        page = json.loads(output.out)
        contents.append(page["items"][0]["content"])
        cursor = page["next_cursor"]
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        if cursor is None:
            break
        assert not required_read_satisfied(rows, requirement)
    assert "".join(contents) == "中" * 10000
    assert required_read_satisfied(rows, requirement)


@pytest.mark.parametrize("fault", ["", "tampered", "changed_attempt", "changed_requirement", "gap"])
def test_required_pages_keep_aliases_bound_and_reject_incomplete_consumption(tmp_path, fault):
    from zf.cli.artifact_required_read import prepare_required_read
    from zf.runtime.artifact_output_delivery import emit_artifact_output
    import io

    def required_read_output(state, **kwargs):
        output, receipts = prepare_required_read(state, **kwargs)
        emit_artifact_output(state, output, receipts, io.StringIO())
        return output
    from zf.runtime.artifact_read_ledger import required_read_satisfied

    state = tmp_path / ".runtime"
    path = state / "artifacts" / "plan.json"
    path.parent.mkdir(parents=True)
    text = '中文 "quoted" \\ newline\n' * 1000
    path.write_text(json.dumps({"facts": text}, ensure_ascii=False))
    sources = [{"source_id": name, "artifact_id": name, "ref": "artifacts/plan.json",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "allowed_paths": ["$.facts"]}
               for name in ("plan-port-task_map", "oa-route-task_map")]
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1", dispatch_id="attempt-1",
        sources=sources, metadata={"read_purpose": "implementation"},
    )
    requirements = [{"source_id": s["source_id"], "artifact_id": s["artifact_id"],
                     "artifact_sha256": s["sha256"], "json_path": "$.facts"} for s in sources]
    identity = {"actor": "dev", "role": "dev", "provider": "codex", "purpose": "implementation"}
    ledger = state / "artifacts/attempts/attempt-1/read-ledger.active.jsonl"
    output = required_read_output(state, manifest=manifest, rows=requirements, identity=identity)
    page = json.loads(output)
    contents = [page["items"][0]["content"]]
    prefix = ledger.read_bytes()
    if fault == "tampered":
        path.write_text('{"facts":"changed"}')
    elif fault == "changed_attempt":
        manifest = {**manifest, "attempt_id": "attempt-2"}
    elif fault == "changed_requirement":
        requirements = requirements[:1]
    if fault not in ("", "gap"):
        with pytest.raises(ValueError):
            required_read_output(state, manifest=manifest, rows=requirements, identity=identity,
                                 cursor=page["next_cursor"])
        assert ledger.read_bytes() == prefix
        assert not (state / "artifacts/attempts/attempt-2/read-ledger.active.jsonl").exists()
        return
    while page["next_cursor"]:
        output = required_read_output(state, manifest=manifest, rows=requirements, identity=identity,
                                      cursor=page["next_cursor"])
        assert len(output) <= 8192
        page = json.loads(output)
        assert len(page["items"][0]["aliases"]) == 1
        contents.append(page["items"][0]["content"])
    assert "".join(contents) == text
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    if fault == "gap":
        rows = [row for row in rows if row["page_start"] != 0]
    assert all(required_read_satisfied(rows, r) for r in requirements) == (fault != "gap")
