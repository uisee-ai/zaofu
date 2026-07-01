

# ---------------------------------------------------------------- B19


def _manifest_with_required(paths: list[str]) -> dict:
    return {
        "schema_version": "task-context-manifest.v1",
        "task_id": "T1",
        "contexts": {
            "implement": [
                {"kind": "payload_ref", "path": p, "required": True,
                 "reason": "test"}
                for p in paths
            ] + [
                {"kind": "inline", "path": "contract.behavior: x",
                 "required": True, "reason": "inline exempt"},
            ],
            "check": [],
        },
    }


def test_read_receipt_gaps_flags_unread_required():
    from zf.runtime.task_context_manifest import read_receipt_gaps

    manifest = _manifest_with_required(["/a/source.md", "/b/spec.md"])
    payload = {"read_receipts": [{"path": "/a/source.md", "digest": "d1"}]}
    assert read_receipt_gaps(manifest, payload) == ["implement:/b/spec.md"]


def test_read_receipt_gaps_no_receipts_means_all_gap():
    from zf.runtime.task_context_manifest import read_receipt_gaps

    manifest = _manifest_with_required(["/a/source.md"])
    assert read_receipt_gaps(manifest, {}) == ["implement:/a/source.md"]


def test_read_receipt_full_receipts_clean_and_string_form():
    from zf.runtime.task_context_manifest import read_receipt_gaps

    manifest = _manifest_with_required(["/a/source.md", "/b/spec.md"])
    payload = {"read_receipts": ["/a/source.md", "/b/spec.md"]}
    assert read_receipt_gaps(manifest, payload) == []
