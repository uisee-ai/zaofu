"""Selected blocks preserve task scope and remove full-source continuation I/O."""
import hashlib
import json

import pytest

from zf.cli.artifact_required_read import prepare_required_read
from zf.runtime.artifact_read_ledger import active_read_ledger_path, source_manifest_from_payload
from zf.runtime.artifact_read_snapshot import open_selection

IDENTITY = {"actor": "dev-1", "role": "dev", "provider": "codex", "purpose": "implementation"}


def published(state, *, body=None, aliases=False):
    path = state / "artifacts/input.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body or {"shared": "规则", "tasks": [{"id": f"T{i}", "body": str(i) * 1500} for i in range(100)]}, ensure_ascii=False))
    source = {"source_id": "plan", "artifact_id": "task-input", "ref": "artifacts/input.json", "allowed_paths": ["$.shared", "$.tasks[7]"]}
    refs = [source]
    if aliases:
        refs.append({**source, "source_id": "alias", "access_scope": {"roles": ["verify"]}})
    manifest, descriptor = source_manifest_from_payload(state_dir=state, project_root=state.parent,
        payload={"input_refs": refs}, workflow_run_id="r", task_id="T7", attempt_id="a", dispatch_id="a",
        manifest_metadata={"read_purpose": "implementation"})
    rows = [{"source_id": s["source_id"], "artifact_id": s["artifact_id"], "artifact_sha256": s["sha256"], "json_path": p}
            for s in manifest["sources"] for p in s["allowed_paths"]]
    return manifest, rows, descriptor


def test_pages_read_only_blocks_from_approved_task_not_whole_hundred_task_source(tmp_path, monkeypatch):
    manifest, rows, _ = published(tmp_path)
    from zf.runtime import artifact_source_versions as versions
    from zf.runtime import artifact_read_snapshot as snapshots
    monkeypatch.setattr(versions, "read_source_version", lambda *a, **k: pytest.fail("page reparsed complete source"))
    original = snapshots.hydrate_sidecar_ref
    touched = []
    def tracked(state, descriptor):
        touched.append(descriptor["ref"])
        return original(state, descriptor)
    monkeypatch.setattr(snapshots, "hydrate_sidecar_ref", tracked)
    cursor, parts, pages = None, {}, 0
    while True:
        output, receipts = prepare_required_read(tmp_path, manifest=manifest, rows=rows, identity=IDENTITY, cursor=cursor, page_bytes=1024)
        page = json.loads(output)
        assert len((output + "\n").encode()) <= 1024
        for item in page["items"]:
            parts[item["json_path"]] = parts.get(item["json_path"], "") + item["content"]
        assert all(r["recovery_mode"] == "pinned_selection_snapshot" for r in receipts)
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages > 1
    assert json.loads(parts["$.tasks[7]"])["id"] == "T7"
    assert '"T8"' not in str(parts)
    assert len(touched) <= pages + 1  # No repeated reads during binary budget search.
    assert not active_read_ledger_path(tmp_path, "a").exists()


@pytest.mark.parametrize("fault", ["missing", "corrupt", "unrecoverable"])
def test_selection_block_recovers_only_pinned_original(tmp_path, fault):
    manifest, rows, _ = published(tmp_path)
    source = manifest["sources"][0]
    block = tmp_path / source["read_selections"][0]["chunks"][0]["ref"]
    block.unlink() if fault == "missing" else block.write_text("tampered")
    (tmp_path / source["ref"]).write_text('{"shared":"new wrong version"}')
    if fault == "unrecoverable":
        (tmp_path / source["recovery_ref"]["ref"]).write_text("tampered")
        with pytest.raises(ValueError):
            open_selection(tmp_path, manifest=manifest, row=rows[0], identity=IDENTITY).read()
    else:
        selection = open_selection(tmp_path, manifest=manifest, row=rows[0], identity=IDENTITY)
        assert selection.read() == "规则"
        assert selection.receipt["recovery_mode"] == "pinned_selection_rebuilt"
    assert not active_read_ledger_path(tmp_path, "a").exists()


def test_alias_authorization_is_not_unioned_by_content_digest(tmp_path):
    manifest, rows, _ = published(tmp_path, aliases=True)
    with pytest.raises(ValueError, match="not authorized"):
        prepare_required_read(tmp_path, manifest=manifest, rows=rows, identity=IDENTITY)
    reads = [json.loads(x) for x in active_read_ledger_path(tmp_path, "a").read_text().splitlines()]
    assert [r["status"] for r in reads] == ["denied"]


def test_selection_and_cursor_bind_source_version(tmp_path):
    manifest, rows, descriptor = published(tmp_path)
    raw = (tmp_path / descriptor["ref"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
    output, _ = prepare_required_read(tmp_path, manifest=manifest, rows=rows, identity=IDENTITY, page_bytes=1024)
    cursor = json.loads(output)["next_cursor"]
    manifest["task_id"] = "T8"
    with pytest.raises(ValueError, match="stale"):
        prepare_required_read(tmp_path, manifest=manifest, rows=rows, identity=IDENTITY, cursor=cursor)


def test_large_recovery_uses_published_size_not_preview_budget(tmp_path):
    # A 4 MB+ pinned base64 sidecar must remain usable when the original and
    # selected-text cache disappear. Recovery is still exact-version only.
    manifest, rows, _ = published(tmp_path, body={"shared": "中" * 1_400_000, "tasks": [{} for _ in range(8)]})
    source = manifest["sources"][0]
    (tmp_path / source["ref"]).unlink()
    (tmp_path / source["read_selections"][0]["chunks"][0]["ref"]).unlink()
    selection = open_selection(tmp_path, manifest=manifest, row=rows[0], identity=IDENTITY)
    assert selection.read(0, 20) == "中" * 20
    assert selection.receipt["recovery_mode"] == "pinned_selection_rebuilt"


def test_no_required_inputs_returns_complete_empty_page(tmp_path):
    output, receipts = prepare_required_read(tmp_path, manifest={"attempt_id": "empty"}, rows=[], identity=IDENTITY)
    assert json.loads(output)["complete"] is True
    assert json.loads(output)["items"] == receipts == []


def test_optional_snapshot_does_not_mask_or_move_missing_required_read_failure(tmp_path):
    manifest, rows, _ = published(tmp_path)
    from zf.runtime.artifact_read_snapshot import pin_read_selections
    missing = {**rows[0], "json_path": "$.tasks[999]"}
    manifest["sources"][0]["allowed_paths"] = ["$"]
    pin_read_selections(tmp_path, manifest, [missing])
    with pytest.raises(ValueError, match="json_path"):
        prepare_required_read(tmp_path, manifest=manifest, rows=[missing], identity=IDENTITY)
    assert not active_read_ledger_path(tmp_path, "a").exists()
