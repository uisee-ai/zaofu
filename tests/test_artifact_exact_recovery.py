"""Exact reads and recovery share the authenticated operation identity."""
import json

import pytest

from tests.test_artifact_input_recovery import current_dispatch
from tests.test_artifact_read_capability import _grant
from tests.test_artifact_read_snapshot import IDENTITY, published
from zf.cli.main import main
from zf.core.events import EventLog, ZfEvent
from zf.runtime.artifact_input_recovery import build_input_recovery
from zf.runtime.artifact_read_ledger import active_read_ledger_path, read_attempt_artifact


@pytest.mark.parametrize("paged", [False, True])
def test_exact_cli_read_is_visible_to_authenticated_recover(tmp_path, monkeypatch, capsys, paged):
    manifest, required, _ = published(tmp_path, body={"shared": "required rules", "tasks": [{} for _ in range(8)]})
    current_dispatch(tmp_path, required)
    _grant(tmp_path, monkeypatch, manifest=manifest, operation_id="op")
    common = ["--attempt", "a", "--state-dir", str(tmp_path)]
    for item in required:
        command = ["artifact", "read", *common, "--source", item["source_id"], "--artifact", item["artifact_id"], "--json-path", item["json_path"]]
        if paged:
            command += ["--page-start", "0", "--page-size", "8192"]
        assert main(command) == 0
        capsys.readouterr()
    assert main(["artifact", "recover", *common]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["required_visible"] == 2
    assert result["historical_missing"] == 0
    assert result["current_memory"] == "unknown"
    assert result["reconstruction_required"] == 2
    assert result["authorizes_execution"] is False
    rows = [json.loads(line) for line in active_read_ledger_path(tmp_path, "a").read_text().splitlines()]
    assert all(row["operation_id"] == "op" for row in rows)


@pytest.mark.parametrize("fault", [None, "operation_id", "workflow_run_id", "task_id", "dispatch_id", "consumer_actor", "consumer_role", "consumer_provider", "consumer_purpose", "attempt_id", "session", "missing_events", "ambiguous_operation"])
def test_legacy_exact_history_needs_unique_canonical_binding(tmp_path, fault):
    manifest, required, _ = published(tmp_path, body={"shared": "required rules", "tasks": [{} for _ in range(8)]})
    current_dispatch(tmp_path, required)
    events = EventLog(tmp_path / "events.jsonl").read_all()
    reads = [read_attempt_artifact(tmp_path, manifest=manifest, source_id=item["source_id"], artifact_id=item["artifact_id"], json_path=item["json_path"], record=False, actor="dev-1", role="dev", provider="codex", purpose="implementation")["read"] for item in required]
    identity = {**IDENTITY, "operation_id": "op"}
    if fault == "session":
        identity["provider_session_id"] = "current-session"
    elif fault == "missing_events":
        events = []
    elif fault == "ambiguous_operation":
        events.append(ZfEvent(type="workflow.operation.requested", task_id="T7", payload={"workflow_run_id": "r", "operation_id": "another", "active_attempt_id": "a", "dispatch_id": "a", "role_instance": "dev-1"}))
    elif fault:
        for row in reads:
            row[fault] = "another"
    result = build_input_recovery(state_dir=tmp_path, manifest=manifest, identity=identity, events=events, required=required, reads=reads)
    assert result["historical_missing"] == (0 if fault is None else 2)
    assert result["current_memory"] == "unknown"
    assert result["authorizes_execution"] is False
    assert not active_read_ledger_path(tmp_path, "a").exists()
