from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from zf.autoresearch.holdout import holdout_projection, load_holdout_registry
from zf.autoresearch.projection import project_autoresearch_state
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.server import create_app


def test_holdout_registry_loads_json(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "scenarios": [
            {"id": "H1", "purpose": "guard", "command": "pytest"},
            {"id": "H2", "purpose": "smoke"},
        ],
    }), encoding="utf-8")

    scenarios = load_holdout_registry(registry)

    assert [scenario.id for scenario in scenarios] == ["H1", "H2"]


def test_project_autoresearch_state_reads_loop_and_holdout(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    loop_dir = state_dir / "autoresearch" / "loop"
    loop_dir.mkdir(parents=True)
    (loop_dir / "journal.jsonl").write_text(
        json.dumps({"iter": 1, "autoresearch_eval": {"lop": {"state": "healthy"}}}) + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "tests" / "fixtures" / "holdout" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"scenarios": [{"id": "H1"}]}), encoding="utf-8")

    projection = project_autoresearch_state(state_dir, project_root=tmp_path)

    assert projection["latest_iteration"]["iter"] == 1
    assert projection["holdout"]["scenario_count"] == 1


def test_project_autoresearch_state_projects_review_gate_artifacts(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    gate_dir = state_dir / "autoresearch" / "runs" / "run-1" / "review-gate"
    gate_dir.mkdir(parents=True)
    (gate_dir / "summary.json").write_text(json.dumps({
        "schema_version": "autoresearch.review_gate.summary.v1",
        "mode": "auto",
        "status": "triggered",
        "triggered": True,
        "route": "fanout_gate",
        "severity": "high",
        "reason": "runtime fanout failure",
        "failure_fingerprint": "fatal:fanout.timed_out:F1",
        "run_terminal_status": "fatal",
        "primary_failure_class": "pane_grid_respawn_failure",
        "review_gate_summary_fresh": True,
        "attempt": 1,
        "attempt_cap": 2,
        "budget_cap": {"max_runs": 1, "max_minutes": 45},
        "required_roles": ["ar-diagnoser", "ar-critic-verifier"],
        "artifact_refs": {
            "failure_evidence_pack": str(gate_dir / "failure.json"),
        },
        "policy": {"route": "fanout_gate"},
    }), encoding="utf-8")
    (gate_dir / "failure.json").write_text(json.dumps({
        "schema_version": "failure_evidence_pack.v1",
        "state_dir": str(state_dir),
        "run_terminal_status": "fatal",
        "primary_failure_class": "pane_grid_respawn_failure",
    }), encoding="utf-8")
    (gate_dir / "closeout.json").write_text(json.dumps({
        "schema_version": "autoresearch.review_gate.closeout.v1",
        "result": {
            "accepted": True,
            "decision": "approve",
            "status": "accepted",
        },
    }), encoding="utf-8")

    projection = project_autoresearch_state(state_dir, project_root=tmp_path)

    latest = projection["review_gate"]["latest"]
    assert projection["review_gate"]["summary"]["total"] == 1
    assert projection["review_gate"]["summary"]["triggered"] == 1
    assert latest["run_id"] == "run-1"
    assert latest["attempt_cap"] == 2
    assert latest["budget_cap"]["max_minutes"] == 45
    assert latest["decision"] == "approve"
    assert latest["run_terminal_status"] == "fatal"
    assert latest["primary_failure_class"] == "pane_grid_respawn_failure"
    assert latest["review_gate_summary_fresh"] is True
    assert latest["artifact_refs"]["summary"].endswith("summary.json")
    assert projection["runs"][0]["review_gate"]["route"] == "fanout_gate"


def test_project_autoresearch_state_marks_review_gate_summary_stale(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    gate_dir = state_dir / "autoresearch" / "runs" / "run-stale" / "review-gate"
    gate_dir.mkdir(parents=True)
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-safehalt",
        type="runtime.safe_halted",
        ts="2026-06-16T16:02:00+00:00",
        actor="orchestrator",
        payload={"reason": "late safe halt"},
    ))
    (gate_dir / "failure.json").write_text(json.dumps({
        "schema_version": "failure_evidence_pack.v1",
        "state_dir": str(state_dir),
    }), encoding="utf-8")
    (gate_dir / "summary.json").write_text(json.dumps({
        "schema_version": "autoresearch.review_gate.summary.v1",
        "generated_at": "2026-06-16T16:01:29+00:00",
        "mode": "auto",
        "status": "classified",
        "triggered": False,
        "route": "direct_repair",
        "severity": "medium",
        "reason": "early snapshot",
        "failure_fingerprint": "failure:early",
        "review_gate_summary_fresh": True,
        "artifact_refs": {
            "failure_evidence_pack": str(gate_dir / "failure.json"),
        },
    }), encoding="utf-8")

    projection = project_autoresearch_state(state_dir, project_root=tmp_path)

    latest = projection["review_gate"]["latest"]
    assert latest["run_id"] == "run-stale"
    assert latest["review_gate_summary_fresh"] is False


def test_web_autoresearch_endpoint(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    response = client.get("/api/autoresearch")

    assert response.status_code == 200
    assert response.json()["state_dir"] == str(state_dir)
    assert holdout_projection(tmp_path)["gate"] == "skipped"
