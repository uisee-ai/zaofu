from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events import EventLog, ZfEvent


def _init_project(root: Path, monkeypatch) -> Path:
    monkeypatch.chdir(root)
    (root / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-contract\n"
        "  state_dir: .zf\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    return root / ".zf"


def _json_output(capsys) -> dict:
    captured = capsys.readouterr()
    assert captured.err == ""
    value = json.loads(captured.out)
    assert value["schema_version"] == "zf.cli.result.v1"
    assert value["identity"]["project_id"] == "cli-contract"
    return value


def test_core_query_commands_offer_stable_json_envelope(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    state_dir = _init_project(tmp_path, monkeypatch)
    capsys.readouterr()

    assert main(["status", "--json"]) == 0
    assert _json_output(capsys)["command"] == "status"

    assert main(["kanban", "--board", "--json"]) == 0
    assert _json_output(capsys)["data"]["tasks"] == []

    assert main(["events", "--json"]) == 0
    assert _json_output(capsys)["command"] == "events"

    assert main(["cost", "--json"]) == 0
    assert _json_output(capsys)["data"]["grand_total_usd"] == 0

    assert main(["validate", "--json"]) == 0
    assert _json_output(capsys)["ok"] is True

    assert main(["preflight", "--json"]) == 0
    assert _json_output(capsys)["ok"] is True

    manifest_dir = (
        state_dir
        / "artifacts"
        / "attempts"
        / "dispatch-1"
        / "source-manifests"
    )
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps({
            "schema_version": "attempt-source-manifest.v1",
            "attempt_id": "dispatch-1",
            "sources": [],
        }),
        encoding="utf-8",
    )
    assert main(["artifact", "list", "--attempt", "dispatch-1", "--json"]) == 0
    assert _json_output(capsys)["data"]["attempt_id"] == "dispatch-1"


def test_runs_list_and_explain_share_terminal_workflow_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    state_dir = _init_project(tmp_path, monkeypatch)
    capsys.readouterr()
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="run.goal.completed",
        correlation_id="trace-run-1",
        payload={"run_id": "run-1"},
    ))

    assert main(["runs", "list", "--json"]) == 0
    listed = _json_output(capsys)
    workflow_runs = listed["data"]["workflow_runs"]
    assert workflow_runs == [{
        "run_id": "run-1",
        "milestones": 1,
        "last_milestone": "run.goal.completed",
        "last_ts": workflow_runs[0]["last_ts"],
        "status": "completed",
        "terminal_event_id": workflow_runs[0]["terminal_event_id"],
        "attention": False,
    }]

    assert main(["runs", "--json", "list"]) == 0
    assert _json_output(capsys)["command"] == "runs.list"

    assert main(["runs", "explain", "--json"]) == 0
    explained = _json_output(capsys)
    assert explained["data"]["runs"]["run-1"]["status"] == "completed"


def test_emit_invalid_payload_writes_only_stderr(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _init_project(tmp_path, monkeypatch)
    capsys.readouterr()

    assert main(["emit", "task.created", "--payload", "{"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "valid JSON" in captured.err
