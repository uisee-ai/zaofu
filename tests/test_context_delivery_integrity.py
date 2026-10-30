"""Production failures, delivery visibility and exact-version recovery."""
import hashlib
import io
import json
from uuid import uuid4

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_output_delivery import emit_artifact_output, observe_artifact_delivery
from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path, read_attempt_artifact, source_manifest_from_payload,
)


@pytest.mark.parametrize("failure", ["write", "flush", "short"])
def test_failed_stdout_has_no_read_receipt(tmp_path, failure):
    class Broken(io.StringIO):
        def write(self, value):
            if failure == "write":
                raise BrokenPipeError()
            return 1 if failure == "short" else super().write(value)

        def flush(self):
            if failure == "flush":
                raise BrokenPipeError()

    with pytest.raises(OSError):
        emit_artifact_output(tmp_path, "full body", [{"attempt_id": "a"}], Broken())
    assert not active_read_ledger_path(tmp_path, "a").exists()


@pytest.mark.parametrize("fault", ["", "truncated", "wrong_session", "old", "echo", "missing", "malformed"])
@pytest.mark.parametrize("format", ["function", "custom_blocks"])
def test_observation_requires_complete_current_provider_output(tmp_path, fault, format):
    row = {"schema_version": "artifact-read.v1", "attempt_id": "a", "consumer_actor": "dev",
           "consumer_provider": "codex", "read_at": "2026-10-30T10:00:00+00:00"}
    output = '{"content":"原始需求与回答"}'
    emit_artifact_output(tmp_path, output, [row], io.StringIO())
    rows = [json.loads(line) for line in active_read_ledger_path(tmp_path, "a").read_text().splitlines()]
    assert rows[0]["provider_delivery_status"] == "unknown"
    sid = str(uuid4())
    path = tmp_path / "provider.jsonl"
    registry = RoleSessionRegistry(tmp_path / "role_sessions.yaml", tmp_path.parent)
    registry.bind_codex_session("dev", sid, session_path=path)
    records = [
        {"type": "session_meta", "payload": {"id": str(uuid4()) if fault == "wrong_session" else sid}},
        {"timestamp": "2026-10-30T09:00:00+00:00" if fault == "old" else "2026-10-30T10:00:01+00:00",
         "type": "response_item", "payload": {"type": "function_call" if fault == "echo" else "function_call_output",
          "call_id": "call-1", "output": output[:12] if fault == "truncated" else "Output:\n" + output + "\n"}},
    ]
    if format == "custom_blocks":
        payload = records[1]["payload"]
        if payload["type"] == "function_call_output":
            payload["type"] = "custom_tool_call_output"
        payload["output"] = [{"type": "input_text", "text": payload["output"]}]
    if fault == "malformed":
        records = [records[0], None, [], {"type": "response_item", "payload": None}]
    if fault != "missing":
        path.write_text("\n".join(json.dumps(record) for record in records))
    observed = observe_artifact_delivery(tmp_path, rows)[0]
    assert observed["provider_delivery_status"] == ("unknown" if fault else "observed_in_transcript")
    if not fault:
        assert observed["provider_session_id"] == sid
        assert observed["output_sha256"] == hashlib.sha256(output.encode()).hexdigest()
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        from zf.runtime.artifact_read_ledger import seal_read_ledger
        witness = hydrate_sidecar_ref(tmp_path, observed["provider_observation_ref"]).payload
        assert witness["output"] == "Output:\n" + output + "\n"
        sealed = seal_read_ledger(tmp_path, "a")
        assert "observed_in_transcript" in (tmp_path / sealed["ref"]).read_text()
        registry.clear("dev")
        resealed = seal_read_ledger(tmp_path, "a")
        assert resealed["sha256"] == sealed["sha256"]


@pytest.mark.parametrize("fault", ["", "original_missing", "original_changed", "cache_changed", "both_changed"])
def test_runtime_manifest_pins_recoverable_full_versions(tmp_path, fault):
    state = tmp_path / "state"
    path = state / "artifacts/source.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"facts":"完整正文", "excluded":"private"}')
    manifest, _ = source_manifest_from_payload(
        state_dir=state, project_root=tmp_path,
        payload={"input_refs": [{"source_id": "facts", "ref": "artifacts/source.json", "allowed_paths": ["$.facts"]}]},
        workflow_run_id="run", task_id="task", attempt_id="a", dispatch_id="d",
    )
    source = manifest["sources"][0]
    if fault == "original_missing":
        path.unlink()
    if fault in {"original_changed", "both_changed"}:
        path.write_text('{"facts":"new version"}')
    if fault in {"cache_changed", "both_changed"}:
        (state / source["recovery_ref"]["ref"]).write_text("corrupt")
    if fault == "both_changed":
        with pytest.raises(ValueError):
            read_attempt_artifact(state, manifest=manifest, source_id="facts", artifact_id=source["artifact_id"], json_path="$.facts")
        assert not active_read_ledger_path(state, "a").exists()
    else:
        result = read_attempt_artifact(state, manifest=manifest, source_id="facts", artifact_id=source["artifact_id"], json_path="$.facts")
        assert result["content"] == "完整正文"
        assert result["read"]["recovery_mode"] == ("pinned_full_version" if fault.startswith("original_") else "authoritative_ref")
        with pytest.raises(ValueError, match="not allowed"):
            read_attempt_artifact(state, manifest=manifest, source_id="facts", artifact_id=source["artifact_id"], json_path="$.excluded")


def test_required_unicode_page_obeys_explicit_byte_budget(tmp_path):
    from zf.cli.artifact_required_read import prepare_required_read

    path = tmp_path / "artifacts/input.json"
    path.parent.mkdir()
    body = "中文😀\\\n" * 500
    path.write_text(json.dumps(body))
    manifest, _ = source_manifest_from_payload(
        state_dir=tmp_path, project_root=tmp_path.parent, payload={"input_refs": ["artifacts/input.json"]},
        workflow_run_id="r", task_id="t", attempt_id="a", dispatch_id="d",
    )
    source = manifest["sources"][0]
    rows = [{"source_id": source["source_id"], "artifact_id": source["artifact_id"]}]
    parts, cursor = [], None
    while True:
        output, receipts = prepare_required_read(tmp_path, manifest=manifest, rows=rows,
            identity={"actor": "dev", "role": "dev", "provider": "codex", "purpose": "implementation"},
            cursor=cursor, page_bytes=2048)
        assert len((output + "\n").encode()) <= 2048
        assert not active_read_ledger_path(tmp_path, "a").exists()  # prepare is not delivery
        page = json.loads(output)
        parts.append(page["items"][0]["content"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert "".join(parts) == body


@pytest.mark.parametrize("registry_body", ["roles: [", "roles: {dev: invalid-uuid}\ninstance_meta: {dev: {session_path: /missing}}"])
def test_unobservable_session_registry_does_not_block_sealing(tmp_path, registry_body):
    from zf.runtime.artifact_read_ledger import seal_read_ledger

    emit_artifact_output(tmp_path, "body", [{"schema_version": "artifact-read.v1", "attempt_id": "a",
        "consumer_actor": "dev", "consumer_provider": "codex", "read_at": "2026-10-30T10:00:00+00:00"}], io.StringIO())
    (tmp_path / "role_sessions.yaml").write_text(registry_body)
    descriptor = seal_read_ledger(tmp_path, "a")
    row = json.loads((tmp_path / descriptor["ref"]).read_text())
    assert row["provider_delivery_status"] == "unknown"


def test_packed_pages_preserve_small_sources_large_tails_and_alias_receipts(tmp_path):
    from zf.cli.artifact_required_read import prepare_required_read
    from zf.runtime.artifact_read_ledger import required_read_satisfied

    refs, expected = [], {}
    for index, size in enumerate([300, 400, 12000, 200, 600, 500]):
        path = tmp_path / f"artifacts/{index}.json"
        path.parent.mkdir(exist_ok=True)
        value = ("中文😀" if index == 2 else str(index)) * size
        path.write_text(json.dumps(value, ensure_ascii=False))
        refs.append({"source_id": f"s{index}", "ref": f"artifacts/{index}.json"})
        expected[f"s{index}"] = value
    refs.append({"source_id": "alias", "ref": "artifacts/2.json"})
    expected["alias"] = expected["s2"]
    manifest, _ = source_manifest_from_payload(
        state_dir=tmp_path, project_root=tmp_path.parent, payload={"input_refs": refs},
        workflow_run_id="r", task_id="t", attempt_id="a", dispatch_id="d",
    )
    requirements = [{"source_id": s["source_id"], "artifact_id": s["artifact_id"],
                     "artifact_sha256": s["sha256"], "json_path": "$"} for s in manifest["sources"]]
    contents = {name: "" for name in expected}
    receipts, cursor, multi = [], None, False
    while True:
        output, emitted = prepare_required_read(tmp_path, manifest=manifest, rows=requirements,
            identity={"actor": "dev", "role": "dev", "provider": "codex", "purpose": "implementation"},
            cursor=cursor)
        assert len((output + "\n").encode()) <= 8192
        page = json.loads(output)
        assert page["schema_version"] == "required-artifact-page.v2"
        multi |= len(page["items"]) > 1
        for item in page["items"]:
            for source in [item, *item.get("aliases", [])]:
                assert len(contents[source["source_id"]]) == item["page_start"]
                contents[source["source_id"]] += item["content"]
        receipts.extend(emitted)
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert multi and contents == expected
    assert all(required_read_satisfied(receipts, r) for r in requirements)
    assert not required_read_satisfied([r for r in receipts if r.get("page_start") != 0], requirements[2])
