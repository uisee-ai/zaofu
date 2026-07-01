"""Unit tests for the W5-E2E phase report generator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.w5_phase_report import generate_report


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events))


def test_empty_log_all_phases_not_reached(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    phases = generate_report(events_path)
    assert all(p.status == "not-reached" for p in phases)


def test_happy_path_all_pass(tmp_path):
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "session.started"},
        {"type": "user.message"},
        {"type": "arch.proposal.done", "task_id": "T1"},
        {"type": "design.critique.done", "task_id": "T1"},
        {"type": "task.contract.update", "task_id": "T1"},
        {"type": "gan.round.started", "task_id": "T1"},
        {"type": "gan.round.completed", "task_id": "T1"},
        {"type": "task.assigned", "task_id": "T1"},
        {"type": "dev.build.done", "task_id": "T1"},
        {"type": "review.approved", "task_id": "T1"},
        {"type": "test.passed", "task_id": "T1"},
        {"type": "judge.passed", "task_id": "T1"},
        {"type": "task.status_changed", "task_id": "T1",
         "payload": {"to": "done"}},
        {"type": "feature.status_changed", "feature_id": "F1",
         "payload": {"to": "done"}},
    ])
    phases = generate_report(events_path)
    for p in phases:
        assert p.status == "pass", (
            f"Phase {p.phase} expected pass, got {p.status}: {p.fail_reasons}"
        )


def test_no_gan_round_partial(tmp_path):
    """Arch proposal without GAN round → Partial, not pass."""
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "session.started"},
        {"type": "arch.proposal.done", "task_id": "T1"},
        # no gan.round.started
    ])
    phases = generate_report(events_path, mode="w5")
    p2 = next(p for p in phases if "PDD" in p.phase)
    assert p2.status == "partial"
    assert any("gan" in r.lower() for r in p2.fail_reasons)


def test_scope_violation_flagged_as_partial(tmp_path):
    """Verify phase passes but scope.violation flag lowers it to partial."""
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "test.passed", "task_id": "T1"},
        {"type": "discriminator.passed", "task_id": "T1"},
        {"type": "scope.violation", "task_id": "T1"},
    ])
    phases = generate_report(events_path)
    verify = next(p for p in phases if "Verify" in p.phase)
    assert verify.status == "partial"
    assert any("scope" in r.lower() for r in verify.fail_reasons)


def test_w5_mode_requires_discriminator(tmp_path):
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "test.passed", "task_id": "T1"},
    ])
    phases = generate_report(events_path, mode="w5")
    verify = next(p for p in phases if "Verify" in p.phase)
    assert verify.status == "partial"
    assert any("discriminator.passed" in r for r in verify.fail_reasons)


def test_full_mode_requires_test_spec(tmp_path):
    """In full mode, test.spec.done absence makes P3 not-reached."""
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "session.started"},
        {"type": "user.message"},
        {"type": "arch.proposal.done", "task_id": "T1"},
    ])
    phases = generate_report(events_path, mode="full")
    p3 = next((p for p in phases if "Test Spec" in p.phase), None)
    assert p3 is not None, "full mode should include a P3 Test Spec phase"
    assert p3.status == "not-reached"


def test_missing_feature_done_projection_lowers_ship(tmp_path):
    """task done without feature done projection → Ship is partial."""
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "task.status_changed", "task_id": "T1",
         "payload": {"to": "done"}},
    ])
    phases = generate_report(events_path)
    ship = next(p for p in phases if p.phase == "Ship")
    assert ship.status == "partial"


def test_feature_archive_counts_as_feature_done(tmp_path):
    events_path = tmp_path / ".zf" / "events.jsonl"
    _write_events(events_path, [
        {"type": "task.status_changed", "task_id": "T1",
         "payload": {"to": "done"}},
    ])
    archive = tmp_path / ".zf" / "feature_list" / "2026-05-04.json"
    archive.parent.mkdir(parents=True)
    archive.write_text(json.dumps([
        {"id": "F1", "title": "feature", "status": "done"},
    ]))

    phases = generate_report(events_path)

    ship = next(p for p in phases if p.phase == "Ship")
    assert ship.status == "pass"
