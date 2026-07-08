"""EVAL-WORKFLOW-AUDIT-001 — zf workflow audit CLI tests."""

from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

import pytest

from zf.cli.workflow import _parse_since, _run_audit, audit_task
from zf.core.workflow.topology import WorkflowEventSets


@dataclass
class _Ev:
    type: str
    id: str = ""
    ts: str = ""
    task_id: str = ""


def _baseline() -> WorkflowEventSets:
    return WorkflowEventSets.baseline()


# ---------------------------------------------------------------------------
# audit_task pure function
# ---------------------------------------------------------------------------


def test_audit_task_no_events_returns_no_events_status() -> None:
    r = audit_task("TASK-NEVER", [], _baseline())
    assert r["status"] == "no_events"
    assert r["evidence_completeness"] == 0.0


def test_audit_task_complete_has_all_required_events() -> None:
    events = [
        _Ev("task.dispatched", "evt-1", "2026-05-18T10:00:00", "TASK-X"),
        _Ev("arch.proposal.done", "evt-2", "2026-05-18T10:05:00", "TASK-X"),
        _Ev("design.critique.done", "evt-3", "2026-05-18T10:10:00", "TASK-X"),
        _Ev("dev.build.done", "evt-4", "2026-05-18T10:15:00", "TASK-X"),
        _Ev("impl.child.completed", "evt-5", "2026-05-18T10:18:00", "TASK-X"),
        _Ev("static_gate.passed", "evt-6", "2026-05-18T10:20:00", "TASK-X"),
        _Ev("review.approved", "evt-7", "2026-05-18T10:25:00", "TASK-X"),
        _Ev("test.passed", "evt-8", "2026-05-18T10:30:00", "TASK-X"),
        # verify.passed joined the canonical handoff baseline in 38c3ce1
        # (doc 73: verify is the merged review/test lane exit).
        _Ev("verify.passed", "evt-9", "2026-05-18T10:32:00", "TASK-X"),
        _Ev("judge.passed", "evt-10", "2026-05-18T10:35:00", "TASK-X"),
    ]
    r = audit_task("TASK-X", events, _baseline())
    assert r["status"] == "complete"
    assert r["evidence_completeness"] == 1.0
    assert r["missing_events"] == []
    assert r["stage_order_violations"] == []


def test_audit_task_partial_missing_static_gate() -> None:
    events = [
        _Ev("task.dispatched", "evt-1", "2026-05-18T10:00:00", "TASK-Y"),
        _Ev("dev.build.done", "evt-2", "2026-05-18T10:15:00", "TASK-Y"),
        _Ev("review.approved", "evt-3", "2026-05-18T10:25:00", "TASK-Y"),
        _Ev("test.passed", "evt-4", "2026-05-18T10:30:00", "TASK-Y"),
        _Ev("judge.passed", "evt-5", "2026-05-18T10:35:00", "TASK-Y"),
    ]
    r = audit_task("TASK-Y", events, _baseline())
    assert r["status"] == "partial"
    assert "static_gate.passed" in r["missing_events"]
    assert r["evidence_completeness"] < 1.0


def test_audit_task_stage_order_violation() -> None:
    """judge.passed before test.passed → violation reported."""
    events = [
        _Ev("task.dispatched", "evt-1", "2026-05-18T10:00:00", "TASK-Z"),
        _Ev("dev.build.done", "evt-2", "2026-05-18T10:15:00", "TASK-Z"),
        _Ev("static_gate.passed", "evt-3", "2026-05-18T10:20:00", "TASK-Z"),
        _Ev("review.approved", "evt-4", "2026-05-18T10:25:00", "TASK-Z"),
        _Ev("judge.passed", "evt-5", "2026-05-18T10:28:00", "TASK-Z"),  # EARLY (before test)
        _Ev("test.passed", "evt-6", "2026-05-18T10:30:00", "TASK-Z"),
    ]
    r = audit_task("TASK-Z", events, _baseline())
    assert r["status"] == "partial"
    assert any(
        "judge.passed" in v and "test.passed" in v
        for v in r["stage_order_violations"]
    )


def test_audit_task_filters_by_task_id() -> None:
    """audit_task only considers events with matching task_id."""
    events = [
        _Ev("task.dispatched", "evt-1", "2026-05-18T10:00:00", "TASK-OTHER"),
        _Ev("dev.build.done", "evt-2", "2026-05-18T10:15:00", "TASK-OTHER"),
    ]
    r = audit_task("TASK-Z", events, _baseline())
    assert r["status"] == "no_events"


# ---------------------------------------------------------------------------
# _parse_since
# ---------------------------------------------------------------------------


def test_parse_since_none_returns_none() -> None:
    assert _parse_since(None) is None
    assert _parse_since("") is None


def test_parse_since_hours() -> None:
    cutoff = _parse_since("24h")
    assert cutoff is not None


def test_parse_since_days() -> None:
    cutoff = _parse_since("7d")
    assert cutoff is not None


def test_parse_since_minutes() -> None:
    cutoff = _parse_since("30m")
    assert cutoff is not None


def test_parse_since_unknown_returns_none() -> None:
    """Unknown format → silently None (no error)."""
    assert _parse_since("foobar") is None


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def _bootstrap(tmp_path: Path, monkeypatch, events: list[dict]) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (tmp_path / "zf.yaml").write_text(
        "project:\n  name: test\nroles: []\n"
    )
    lines = [json.dumps(e) for e in events]
    (state_dir / "events.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))
    (state_dir / "kanban.json").write_text("[]")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli_audit_explicit_task_complete(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    events = [
        {"type": "task.dispatched", "id": "evt-1", "ts": "2026-05-18T10:00:00",
         "actor": "zf-cli", "task_id": "TASK-A", "payload": {}},
        {"type": "arch.proposal.done", "id": "evt-2", "ts": "2026-05-18T10:05:00",
         "actor": "arch", "task_id": "TASK-A", "payload": {}},
        {"type": "design.critique.done", "id": "evt-3", "ts": "2026-05-18T10:10:00",
         "actor": "critic", "task_id": "TASK-A", "payload": {}},
        {"type": "dev.build.done", "id": "evt-4", "ts": "2026-05-18T10:15:00",
         "actor": "dev-1", "task_id": "TASK-A", "payload": {}},
        {"type": "impl.child.completed", "id": "evt-5", "ts": "2026-05-18T10:18:00",
         "actor": "zf-cli", "task_id": "TASK-A", "payload": {}},
        {"type": "static_gate.passed", "id": "evt-6", "ts": "2026-05-18T10:20:00",
         "actor": "zf-cli", "task_id": "TASK-A", "payload": {}},
        {"type": "review.approved", "id": "evt-7", "ts": "2026-05-18T10:25:00",
         "actor": "review", "task_id": "TASK-A", "payload": {}},
        {"type": "test.passed", "id": "evt-8", "ts": "2026-05-18T10:30:00",
         "actor": "test", "task_id": "TASK-A", "payload": {}},
        {"type": "verify.passed", "id": "evt-9", "ts": "2026-05-18T10:32:00",
         "actor": "zf-cli", "task_id": "TASK-A", "payload": {}},
        {"type": "judge.passed", "id": "evt-10", "ts": "2026-05-18T10:35:00",
         "actor": "judge", "task_id": "TASK-A", "payload": {}},
    ]
    _bootstrap(tmp_path, monkeypatch, events)
    rc = _run_audit(Namespace(
        task="TASK-A", since=None, format="md", strict=False,
        state_dir=None,
    ))
    out = capsys.readouterr().out
    assert rc == 0
    assert "TASK-A: ✓ complete" in out
    assert "100%" in out


def test_cli_audit_partial_shows_missing(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    events = [
        {"type": "task.dispatched", "id": "evt-1", "ts": "2026-05-18T10:00:00",
         "actor": "zf-cli", "task_id": "TASK-B", "payload": {}},
        {"type": "dev.build.done", "id": "evt-2", "ts": "2026-05-18T10:15:00",
         "actor": "dev-1", "task_id": "TASK-B", "payload": {}},
    ]
    _bootstrap(tmp_path, monkeypatch, events)
    rc = _run_audit(Namespace(
        task="TASK-B", since=None, format="md", strict=False,
        state_dir=None,
    ))
    out = capsys.readouterr().out
    assert rc == 0
    assert "TASK-B: ⚠ partial" in out
    assert "static_gate.passed — MISSING" in out
    assert "judge.passed — MISSING" in out


def test_cli_audit_strict_exits_1_on_partial(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    events = [
        {"type": "task.dispatched", "id": "evt-1", "ts": "2026-05-18T10:00:00",
         "actor": "zf-cli", "task_id": "TASK-C", "payload": {}},
    ]
    _bootstrap(tmp_path, monkeypatch, events)
    rc = _run_audit(Namespace(
        task="TASK-C", since=None, format="md", strict=True,
        state_dir=None,
    ))
    assert rc == 1  # partial + strict → exit 1


def test_cli_audit_json_format(tmp_path: Path, monkeypatch, capsys) -> None:
    events = [
        {"type": "task.dispatched", "id": "evt-1", "ts": "2026-05-18T10:00:00",
         "actor": "zf-cli", "task_id": "TASK-J", "payload": {}},
    ]
    _bootstrap(tmp_path, monkeypatch, events)
    rc = _run_audit(Namespace(
        task="TASK-J", since=None, format="json", strict=False,
        state_dir=None,
    ))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["audited"] == 1
    assert data["partial"] == 1
    assert data["tasks"][0]["task_id"] == "TASK-J"
    assert "missing_events" in data["tasks"][0]


def test_cli_audit_no_events_no_task_friendly(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _bootstrap(tmp_path, monkeypatch, [])
    rc = _run_audit(Namespace(
        task="TASK-NEVER", since=None, format="md", strict=False,
        state_dir=None,
    ))
    out = capsys.readouterr().out
    assert rc == 0
    assert "TASK-NEVER: — no_events" in out
