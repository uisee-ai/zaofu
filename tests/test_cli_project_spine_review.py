from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _init_project(path: Path) -> Path:
    state_dir = path / ".zf"
    state_dir.mkdir()
    (path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: spine-demo\n  state_dir: .zf\n',
        encoding="utf-8",
    )
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
    return state_dir


def test_project_review_spine_cli_json(tmp_path: Path, monkeypatch, capsys) -> None:
    _init_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = main(["project", "review-spine", "--format", "json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == "project-spine-review.v1"
    assert data["verdict"]
    assert "reflection" in data


def test_project_review_spine_fails_on_state_dir_mismatch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _init_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = main([
        "project",
        "review-spine",
        "--state-dir",
        str(tmp_path / "other-state"),
    ])

    assert rc == 1
    assert "state_dir mismatch" in capsys.readouterr().err


def test_project_review_spine_cli_write_artifact_and_propose(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    state_dir = _init_project(tmp_path)
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="orchestrator",
        payload={"reason": "dispatch failed"},
    ))
    monkeypatch.chdir(tmp_path)

    rc = main([
        "project",
        "review-spine",
        "--format",
        "json",
        "--write-artifact",
    ])
    out = json.loads(capsys.readouterr().out)
    review_id = out["review_id"]
    assert rc == 0
    assert out["artifact"]["event_id"]

    rc = main([
        "project",
        "review-spine",
        "propose",
        "--review-id",
        review_id,
        "--action",
        "1",
        "--json",
    ])
    proposal = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert proposal["proposal"]["review_id"] == review_id
    assert proposal["proposal"]["schema_version"] == "spine-review.proposal.v1"
