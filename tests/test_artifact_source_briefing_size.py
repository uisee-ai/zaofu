"""Large read policies must not turn navigation into another full input pack."""

from copy import deepcopy
import hashlib
import json

from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_read_ledger import (
    build_attempt_source_manifest,
    build_input_consumption_policy,
    read_attempt_artifact,
    render_attempt_source_briefing,
    seal_read_ledger,
    validate_required_reads,
    write_attempt_source_manifest,
)
from tests.test_artifact_read_capability import _grant


def test_large_briefing_bounds_navigation_without_changing_required_reads(monkeypatch):
    monkeypatch.setenv("ZF_CLI_CMD", "/workspace/zaofu/.venv/bin/zf")
    payload = {
        "attempt_source_manifest_ref": "artifacts/attempts/attempt-1/source.json",
        "attempt_id": "attempt-1",
        "required_reads": [
            {"source_id": "plan", "artifact_id": "task_map", "artifact_sha256": "a" * 64,
             "json_path": f"$.tasks[{i}].body"}
            for i in range(218)
        ],
    }
    before = deepcopy(payload)

    briefing = render_attempt_source_briefing(payload)

    assert len(briefing) < 6000
    assert payload == before
    assert "218" in briefing
    assert "--required" in briefing
    assert "--cursor <next_cursor>" in briefing
    assert "navigation only" in briefing
    assert "$.tasks[217].body" not in briefing


def test_unlisted_sources_still_require_consumption_and_cli_delivers_them(
    tmp_path, monkeypatch, capsys,
):
    state = tmp_path / ".runtime"
    sources = []
    for i in range(13):
        path = state / "artifacts" / f"source-{i}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fact": f"required-fact-{i}"}))
        sources.append({"source_id": f"source-{i}", "artifact_id": f"source-{i}",
                        "ref": f"artifacts/source-{i}.json", "allowed_paths": ["$"],
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1",
        dispatch_id="attempt-1", sources=sources,
        metadata={"read_purpose": "implementation"},
    )
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    required = [{"source_id": s["source_id"], "artifact_id": s["artifact_id"],
                 "artifact_sha256": s["sha256"], "json_path": "$"} for s in sources]
    payload = {"attempt_source_manifest_ref": "artifacts/attempts/attempt-1/source.json",
               "attempt_id": "attempt-1", "required_reads": required}
    briefing = render_attempt_source_briefing(payload)
    assert "--source source-12 " not in briefing
    policy = build_input_consumption_policy(
        workflow_run_id="run-1", attempt_id="attempt-1", required_reads=required,
    )
    for source in sources[:8]:
        read_attempt_artifact(state, manifest=manifest, source_id=source["source_id"],
                              artifact_id=source["artifact_id"], json_path="$")
    assert validate_required_reads(
        state, policy=policy, ledger_descriptor=seal_read_ledger(state, "attempt-1"),
    )
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T1", payload=payload,
    ))
    contents = []
    cursor = None
    while True:
        command = ["artifact", "read", "--attempt", "attempt-1", "--required",
                   "--state-dir", str(state)]
        if cursor:
            command.extend(["--cursor", cursor])
        assert main(command) == 0
        page = json.loads(capsys.readouterr().out)
        contents.extend(item["content"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert any("required-fact-12" in content for content in contents)
    assert validate_required_reads(
        state, policy=policy, ledger_descriptor=seal_read_ledger(state, "attempt-1"),
    ) == []
