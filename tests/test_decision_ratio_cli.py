"""EVAL-COORDINATOR-RATIO-001 — zf metrics decision-ratio CLI tests."""

from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path

import pytest

from zf.cli.metrics import _run_decision_ratio


def _bootstrap(
    tmp_path: Path,
    monkeypatch,
    decisions: list[tuple[str, str]],
) -> Path:
    """Create a fresh zf project with N decision.recorded events."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (tmp_path / "zf.yaml").write_text(
        "project:\n  name: test\nroles: []\n"
    )
    # events.jsonl with decision.recorded events
    lines = []
    for kind, reason in decisions:
        evt = {
            "type": "orchestrator.decision.recorded",
            "id": f"evt-{kind}-{reason}",
            "ts": "2026-05-18T10:00:00Z",
            "actor": "zf-cli",
            "payload": {
                "decision": kind,
                "outcome_reason": reason,
            },
        }
        lines.append(json.dumps(evt))
    (state_dir / "events.jsonl").write_text("\n".join(lines) + "\n")
    (state_dir / "kanban.json").write_text("[]")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_no_decision_events_friendly_message(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _bootstrap(tmp_path, monkeypatch, decisions=[])
    rc = _run_decision_ratio(
        Namespace(state_dir=None, format="md", by_reason=False),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "No orchestrator.decision.recorded events found" in out


def test_healthy_ratio_dispatch_to_no_action(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """dispatch=10, no_action=10 → ratio=1.0 → healthy."""
    decisions = [("dispatch", "")] * 10 + [("no_action", "out_of_scope")] * 10
    _bootstrap(tmp_path, monkeypatch, decisions=decisions)
    rc = _run_decision_ratio(
        Namespace(state_dir=None, format="md", by_reason=False),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "✓ healthy" in out
    assert "dispatch:no_action = 10:10" in out


def test_over_cautious_warning(tmp_path: Path, monkeypatch, capsys) -> None:
    """dispatch=2, no_action=20 → ratio=0.1 → over_cautious."""
    decisions = [("dispatch", "")] * 2 + [("no_action", "out_of_scope")] * 20
    _bootstrap(tmp_path, monkeypatch, decisions=decisions)
    rc = _run_decision_ratio(
        Namespace(state_dir=None, format="md", by_reason=False),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "over-cautious" in out


def test_over_eager_warning(tmp_path: Path, monkeypatch, capsys) -> None:
    """dispatch=20, no_action=2 → ratio=10 → over_eager."""
    decisions = [("dispatch", "")] * 20 + [("no_action", "out_of_scope")] * 2
    _bootstrap(tmp_path, monkeypatch, decisions=decisions)
    rc = _run_decision_ratio(
        Namespace(state_dir=None, format="md", by_reason=False),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "over-eager" in out


def test_json_format(tmp_path: Path, monkeypatch, capsys) -> None:
    decisions = [("dispatch", "")] * 5 + [("no_action", "idle_sweep")] * 3
    _bootstrap(tmp_path, monkeypatch, decisions=decisions)
    rc = _run_decision_ratio(
        Namespace(state_dir=None, format="json", by_reason=False),
    )
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["total"] == 8
    assert data["counts"]["dispatch"] == 5
    assert data["counts"]["no_action"] == 3
    assert abs(data["dispatch_no_action_ratio"] - 5 / 3) < 0.001
    assert data["health_band"] == "healthy"


def test_by_reason_groups_no_action(tmp_path: Path, monkeypatch, capsys) -> None:
    decisions = [
        ("dispatch", ""),
        ("no_action", "out_of_scope"),
        ("no_action", "idle_sweep"),
        ("no_action", "idle_sweep"),
        ("blocked", "circuit_open"),
    ]
    _bootstrap(tmp_path, monkeypatch, decisions=decisions)
    rc = _run_decision_ratio(
        Namespace(state_dir=None, format="md", by_reason=True),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "By outcome_reason:" in out
    assert "no_action:" in out
    assert "idle_sweep: 2" in out
    assert "out_of_scope: 1" in out
    assert "blocked:" in out
    assert "circuit_open: 1" in out


def test_by_reason_json_includes_by_reason_field(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    decisions = [
        ("no_action", "out_of_scope"),
        ("no_action", "idle_sweep"),
    ]
    _bootstrap(tmp_path, monkeypatch, decisions=decisions)
    rc = _run_decision_ratio(
        Namespace(state_dir=None, format="json", by_reason=True),
    )
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "by_reason" in data
    assert data["by_reason"]["no_action"] == {
        "out_of_scope": 1, "idle_sweep": 1,
    }
