from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.web.server import create_app


def test_cli_and_web_share_catalog_task_attempt_and_lineage_queries(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("ZF_STATE_DIR", str(state_dir))
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/results/verify.json",
        {"status": "passed"},
        kind="verification_result",
        schema_version="verification-result.v1",
        created_by="verify-1",
        required=True,
    )
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-consumer",
        type="verify.passed",
        actor="verify-1",
        task_id="T-consumer",
        causation_id="evt-consumer-parent",
        correlation_id="run-consumer",
        payload={
            "workflow_run_id": "run-consumer",
            "attempt_id": "attempt-consumer",
            "attempt_domain": "task_verify",
            "verification_result_ref": descriptor,
            "required_reads": [],
        },
    ))

    assert main([
        "artifact",
        "catalog",
        "list",
        "--task-id",
        "T-consumer",
        "--state-dir",
        str(state_dir),
    ]) == 0
    cli_catalog = json.loads(capsys.readouterr().out)
    assert main([
        "task",
        "artifacts",
        "T-consumer",
        "--state-dir",
        str(state_dir),
    ]) == 0
    cli_task = json.loads(capsys.readouterr().out)
    assert main([
        "attempt",
        "inspect",
        "attempt-consumer",
        "--state-dir",
        str(state_dir),
    ]) == 0
    cli_attempt = json.loads(capsys.readouterr().out)
    assert main([
        "artifact",
        "catalog",
        "lineage",
        "--subject-kind",
        "task",
        "--subject-id",
        "T-consumer",
        "--state-dir",
        str(state_dir),
    ]) == 0
    cli_lineage = json.loads(capsys.readouterr().out)

    client = TestClient(create_app(
        state_dir,
        project_root=project_root,
    ))
    web_catalog = client.get(
        "/api/artifacts/catalog",
        params={"task_id": "T-consumer"},
    ).json()
    web_task = client.get("/api/tasks/T-consumer/artifacts").json()
    web_attempt = client.get("/api/attempts/attempt-consumer").json()
    web_lineage = client.get(
        "/api/artifacts/lineage/task/T-consumer",
    ).json()

    assert cli_catalog["items"][0]["occurrence_id"] == (
        web_catalog["items"][0]["occurrence_id"]
    )
    assert cli_catalog["items"][0]["sha256"] == (
        web_catalog["items"][0]["sha256"]
    )
    assert cli_catalog["source_snapshot"] == web_catalog["source_snapshot"]
    assert cli_task["items"] == web_task["items"]
    assert cli_attempt["attempt_domain"] == web_attempt["attempt_domain"]
    assert cli_attempt["handoff"] == web_attempt["handoff"]
    assert cli_lineage["items"] == web_lineage["items"]
    assert cli_lineage["items"][0]["source_event_id"] == "evt-consumer"
    assert cli_lineage["items"][0]["causation_event_id"] == (
        "evt-consumer-parent"
    )
    assert cli_lineage["items"][0]["result_event_id"] == "evt-consumer"
