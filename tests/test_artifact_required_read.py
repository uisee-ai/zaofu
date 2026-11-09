import hashlib
import json

from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_read_ledger import (
    build_attempt_source_manifest, write_attempt_source_manifest, required_read_satisfied,
)


def test_required_batches_pack_sources_deduplicate_aliases_and_bind_continuation(tmp_path, monkeypatch, capsys):
    state = tmp_path / "custom-state"
    sources = []
    for index in range(9):
        file_index = min(index, 7)  # The ninth source is an independently authorized alias.
        path = state / "artifacts" / f"{file_index}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"value": f"value-{file_index}:" + '中"\\' * 260}, ensure_ascii=False))
        sources.append({"source_id": str(index), "artifact_id": str(index),
                        "ref": str(path.relative_to(state)),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "allowed_paths": ["$.value"]})
    manifest = build_attempt_source_manifest(
        workflow_run_id="run", task_id="T", attempt_id="attempt", dispatch_id="attempt",
        sources=sources, metadata={"read_purpose": "implementation"},
    )
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    requirements = [{"source_id": r["source_id"], "artifact_id": r["artifact_id"],
                     "artifact_sha256": r["sha256"], "json_path": "$.value"} for r in sources]
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(type="task.dispatched", actor="orchestrator",
        payload={"dispatch_id": "attempt", "required_reads": requirements}))
    command = ["artifact", "read", "--attempt", "attempt", "--required", "--state-dir", str(state)]
    cursor, pages, delivered = "", 0, {}
    ledger = state / "artifacts/attempts/attempt/read-ledger.active.jsonl"
    while True:
        assert main([*command, "--cursor", cursor]) == 0
        raw = capsys.readouterr().out
        assert len(raw) <= 8192
        page = json.loads(raw)
        pages += 1
        for row in page["items"]:
            delivered[row["source_id"]] = delivered.get(row["source_id"], "") + row["content"]
        cursor = page["next_cursor"]
        if not cursor:
            break
        reads = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert not all(required_read_satisfied(reads, r) for r in requirements)
    assert pages < len(sources)
    assert set(delivered) == {str(i) for i in range(8)}
    reads = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert all(required_read_satisfied(reads, r) for r in requirements)
    assert all(delivered[str(i)] == f"value-{i}:" + '中"\\' * 260 for i in range(8))
