"""Artifact delivery must preserve JSON values and reject lossy decoding."""

import json
from decimal import Decimal

import pytest

from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path,
    read_attempt_artifact,
    source_manifest_from_payload,
)
from zf.runtime.artifact_read_snapshot import open_selection, selected_text

IDENTITY = {"actor": "dev-1", "role": "dev", "provider": "codex", "purpose": "implementation"}


def _publish(state, raw, paths):
    source = state / "artifacts" / "input.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    manifest, _ = source_manifest_from_payload(
        state_dir=state, project_root=state.parent,
        payload={"input_refs": [{"source_id": "s", "artifact_id": "a", "ref": "artifacts/input.json", "allowed_paths": paths}]},
        workflow_run_id="run", task_id="T", attempt_id="attempt", dispatch_id="attempt",
        manifest_metadata={"read_purpose": "implementation"},
    )
    return manifest


def test_precision_and_unicode_survive_snapshot_and_direct_reads(tmp_path):
    raw = ('{"value":0.1234567890123456789012345,"items":[900719925474099312345,1e-324],'
           '"text":"中文\\nemoji 🧪"}').encode()
    manifest = _publish(tmp_path, raw, ["$", "$.value", "$.items", "$.text"])
    for path in ("$", "$.value", "$.items", "$.text"):
        row = {"source_id": "s", "artifact_id": "a", "json_path": path}
        cached = open_selection(tmp_path, manifest=manifest, row=row, identity=IDENTITY).read()
        direct = read_attempt_artifact(tmp_path, manifest=manifest, source_id="s", artifact_id="a",
                                      json_path=path, record=False, **IDENTITY)["content"]
        assert cached == direct
        if path == "$.text":
            assert cached == "中文\nemoji 🧪"
        elif path == "$.value":
            assert Decimal(cached) == Decimal("0.1234567890123456789012345")
        else:
            parsed = json.loads(cached, parse_float=Decimal)
            values = parsed["items"] if isinstance(parsed, dict) else parsed
            assert values == [900719925474099312345, Decimal("1e-324")]


@pytest.mark.parametrize("raw,path", [(b'hello\xffworld', '$'), (b'{"x":1,"x":2}', '$'),
                                         (b'{"x":1,"x":2}', '$.x'), (b'{"x":NaN}', '$')])
def test_ambiguous_or_invalid_source_never_produces_successful_read(tmp_path, raw, path):
    manifest = _publish(tmp_path, raw, [path])
    row = {"source_id": "s", "artifact_id": "a", "json_path": path}
    with pytest.raises(ValueError):
        open_selection(tmp_path, manifest=manifest, row=row, identity=IDENTITY).read()
    with pytest.raises(ValueError):
        read_attempt_artifact(tmp_path, manifest=manifest, source_id="s", artifact_id="a",
                              json_path=path, record=True, **IDENTITY)
    assert not active_read_ledger_path(tmp_path, "attempt").exists()


def test_legacy_lossy_selection_is_not_trusted(tmp_path, monkeypatch):
    import zf.runtime.artifact_read_snapshot as snapshots

    raw = b'{"value":0.1234567890123456789012345}'
    with monkeypatch.context() as old:
        old.setattr(snapshots, "selected_text", lambda _raw, _path: "0.12345678901234568")
        manifest = _publish(tmp_path, raw, ["$.value"])
    manifest["sources"][0]["read_selections"][0].pop("selection_fidelity", None)
    row = {"source_id": "s", "artifact_id": "a", "json_path": "$.value"}
    with pytest.raises(ValueError, match="legacy.*selection"):
        open_selection(tmp_path, manifest=manifest, row=row, identity=IDENTITY).read()


def test_signed_zero_and_high_precision_survive_bounded_pages(tmp_path):
    from zf.cli.artifact_required_read import prepare_required_read

    raw = ('{"signed":-0,"value":0.1234567890123456789012345,"text":"' + '中文' * 2000 + '"}').encode()
    manifest = _publish(tmp_path, raw, ["$"])
    rows = [{"source_id": "s", "artifact_id": "a", "json_path": "$"}]
    cursor, contents = None, []
    while True:
        output, receipts = prepare_required_read(tmp_path, manifest=manifest, rows=rows, identity=IDENTITY, cursor=cursor, page_bytes=1024)
        page = json.loads(output)
        assert len((output + "\n").encode()) <= 1024
        contents.extend(item["content"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    result = "".join(contents)
    assert '"signed": -0' in result
    assert json.loads(result, parse_float=Decimal)["value"] == Decimal("0.1234567890123456789012345")
    assert json.loads(result)["text"] == "中文" * 2000


def test_legacy_lossless_selection_remains_readable(tmp_path):
    manifest = _publish(tmp_path, b'{"value":0.5}', ["$.value"])
    manifest["sources"][0]["read_selections"][0].pop("selection_fidelity", None)
    row = {"source_id": "s", "artifact_id": "a", "json_path": "$.value"}
    assert open_selection(tmp_path, manifest=manifest, row=row, identity=IDENTITY).read() == "0.5"
