from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tracemalloc
import pytest

from zf.web.idempotency_index import latest_record, open_idempotency_index
from zf.web.projections.request_util import (
    _complete_idempotency_key, _idempotency_path, _reserve_idempotency_key,
    reconcile_pending_idempotency_keys,
)


def reserve(state, key="request", action="update-task", payload_hash="hash"):
    return _reserve_idempotency_key(state, key=key, action=action, payload_hash=payload_hash)


def complete(state, key="request", response=None):
    _complete_idempotency_key(state, key=key, action="update-task", payload_hash="hash",
                              response=response or {"ok": True, "value": "完整响应"})


def test_reservation_replay_conflict_and_last_receipt(tmp_path):
    assert reserve(tmp_path)["status"] == "reserved"
    assert reserve(tmp_path)["status"] == "pending"
    complete(tmp_path)
    assert reserve(tmp_path)["response"] == {"ok": True, "value": "完整响应"}
    assert reserve(tmp_path, payload_hash="other")["status"] == "conflict"
    complete(tmp_path, response={"ok": True, "newer": True})
    assert reserve(tmp_path)["response"]["newer"]


def test_concurrent_reservation_has_one_owner(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reserve(tmp_path)["status"], range(16)))
    assert results.count("reserved") == 1
    assert results.count("pending") == 15


def test_legacy_import_and_warm_index_do_not_read_whole_history(tmp_path, monkeypatch):
    path = _idempotency_path(tmp_path)
    path.parent.mkdir()
    path.write_text(json.dumps({"key": "legacy", "action": "update-task", "payload_hash": "hash",
                               "state": "completed", "response": {"kept": True}}) + "\n")
    original = Path.read_text

    def no_whole_read(self, *args, **kwargs):
        assert self != path, "must not read the entire receipt history into memory"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_whole_read)
    assert reserve(tmp_path, "legacy")["response"] == {"kept": True}
    import zf.web.idempotency_index as index
    calls = []
    loads = index.json.loads

    def counted(value, *args, **kwargs):
        calls.append(len(value))
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(index.json, "loads", counted)
    assert reserve(tmp_path, "legacy")["status"] == "replayed"
    assert len(calls) == 1  # only the requested receipt, not all prior responses


def test_rebuild_after_log_replacement_and_index_loss(tmp_path):
    reserve(tmp_path, "old")
    complete(tmp_path, "old")
    assert reserve(tmp_path, "old")["status"] == "replayed"
    path = _idempotency_path(tmp_path)
    index = path.with_suffix(".index.sqlite3")
    index.unlink()
    assert reserve(tmp_path, "old")["status"] == "replayed"
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(json.dumps({"key": "other", "state": "pending"}) + "\n")
    replacement.replace(path)
    assert reserve(tmp_path, "old")["status"] == "reserved"


def test_reconcile_streams_only_pending_latest_records(tmp_path):
    reserve(tmp_path, "finished")
    complete(tmp_path, "finished")
    reserve(tmp_path, "unfinished")
    assert reconcile_pending_idempotency_keys(tmp_path) == 1
    assert reconcile_pending_idempotency_keys(tmp_path) == 0
    assert reserve(tmp_path, "finished")["response"]["ok"]
    assert reserve(tmp_path, "unfinished")["response"]["status"] == "interrupted_by_server_restart"


def test_import_memory_is_bounded_by_record_not_history(tmp_path):
    path = _idempotency_path(tmp_path)
    path.parent.mkdir()
    with path.open("w") as output:
        for i in range(2048):
            output.write(json.dumps({"key": str(i), "state": "completed", "response": {"body": "x" * 4096}}) + "\n")
    tracemalloc.start()
    try:
        with open_idempotency_index(path) as index:
            assert latest_record(index, path, "2047")["response"]["body"] == "x" * 4096
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024, f"history-sized allocation: {peak}"


def test_incomplete_receipt_fails_closed_instead_of_reexecuting(tmp_path):
    path = _idempotency_path(tmp_path)
    path.parent.mkdir()
    path.write_text('{"key":"possibly-published","state":"completed"')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Incomplete idempotency"):
        reserve(tmp_path, "possibly-published")
    assert path.read_bytes() == before
