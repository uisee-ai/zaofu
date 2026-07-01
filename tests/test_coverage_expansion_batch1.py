"""EVAL-COVERAGE-EXPANSION-001 batch 1 — LongHorizonE2E + SprintProgress +
HookHealth."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.core.metrics.coverage_expansion import (
    HookHealthReport,
    LongHorizonE2EReport,
    SprintProgressReport,
    compute_hook_health,
    compute_longhorizon_e2e,
    compute_sprint_progress,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Ev:
    def __init__(self, etype, ts="", task_id="", payload=None):
        self.type = etype
        self.ts = ts
        self.task_id = task_id
        self.payload = payload or {}


class _Feature:
    def __init__(self, status):
        self.status = status


# ---------------------------------------------------------------------------
# LongHorizonE2E
# ---------------------------------------------------------------------------


def test_longhorizon_e2e_empty_state() -> None:
    report = compute_longhorizon_e2e(
        project="cangjie-mono", events=[], features=[], window_days=30,
    )
    assert report.project == "cangjie-mono"
    assert report.user_messages == 0
    assert report.features_delivered == 0
    assert report.e2e_success_rate == 0.0


def test_longhorizon_e2e_counts_user_messages() -> None:
    events = [
        _Ev("user.message"), _Ev("user.message"), _Ev("dev.build.done"),
    ]
    report = compute_longhorizon_e2e(
        project="x", events=events, features=[],
    )
    assert report.user_messages == 2


def test_longhorizon_e2e_feature_status_buckets() -> None:
    features = [
        _Feature("delivered"), _Feature("shipped"), _Feature("done"),  # 3 delivered
        _Feature("blocked"),                                            # 1 blocked
        _Feature("in_progress"), _Feature("pending"),                   # 2 in_progress
    ]
    report = compute_longhorizon_e2e(
        project="x", events=[], features=features,
    )
    assert report.features_delivered == 3
    assert report.features_blocked == 1
    assert report.features_in_progress == 2
    assert abs(report.e2e_success_rate - 3 / 6) < 0.001


def test_longhorizon_e2e_operator_interventions() -> None:
    events = [
        _Ev("human.escalate"),
        _Ev("human.resolved"),
        _Ev("dev.build.done"),
    ]
    report = compute_longhorizon_e2e(
        project="x", events=events, features=[],
    )
    assert report.operator_interventions == 2


def test_longhorizon_e2e_to_dict_has_all_fields() -> None:
    report = compute_longhorizon_e2e(
        project="x", events=[], features=[],
    )
    d = report.to_dict()
    for key in (
        "project", "window_days", "user_messages", "features_delivered",
        "features_blocked", "features_in_progress", "e2e_success_rate",
        "operator_interventions",
    ):
        assert key in d


# ---------------------------------------------------------------------------
# SprintProgress
# ---------------------------------------------------------------------------


def test_sprint_progress_missing_dir() -> None:
    report = compute_sprint_progress(
        backlogs_dir=Path("/nonexistent"), window_days=7,
    )
    assert report.sprints_total == 0
    assert report.sprints_completed == 0
    assert report.burn_rate == 0.0


def _make_git_repo(tmp_path: Path) -> Path:
    """Init a minimal git repo for git log tests."""
    subprocess.run(
        ["git", "init", "-q"], cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=tmp_path, check=True,
    )
    return tmp_path


def test_sprint_progress_counts_completed_marker(tmp_path: Path) -> None:
    _make_git_repo(tmp_path)
    backlogs = tmp_path / "backlogs"
    backlogs.mkdir()
    (backlogs / "2026-05-18-foo.md").write_text("# Foo\n✅ done\n")
    (backlogs / "2026-05-18-bar.md").write_text("# Bar\nWIP\n")
    (backlogs / "2026-05-18-baz.md").write_text("> status: complete\n")

    report = compute_sprint_progress(
        backlogs_dir=backlogs, window_days=30,
    )
    assert report.sprints_total == 3
    assert report.sprints_completed == 2  # ✅ + status: complete


def test_sprint_progress_counts_obsolete(tmp_path: Path) -> None:
    _make_git_repo(tmp_path)
    backlogs = tmp_path / "backlogs"
    backlogs.mkdir()
    (backlogs / "x.md").write_text("> status: obsolete\n")
    (backlogs / "y.md").write_text("# fresh\n")

    report = compute_sprint_progress(
        backlogs_dir=backlogs, window_days=30,
    )
    assert report.sprints_obsolete == 1


def test_sprint_progress_burn_rate(tmp_path: Path) -> None:
    _make_git_repo(tmp_path)
    backlogs = tmp_path / "backlogs"
    backlogs.mkdir()
    for i in range(4):
        marker = "✅" if i < 2 else "WIP"
        (backlogs / f"{i}.md").write_text(f"# {i}\n{marker}\n")
    report = compute_sprint_progress(
        backlogs_dir=backlogs, window_days=30,
    )
    assert report.sprints_total == 4
    assert report.sprints_completed == 2
    assert abs(report.burn_rate - 0.5) < 0.001


def test_sprint_progress_weekly_throughput(tmp_path: Path) -> None:
    _make_git_repo(tmp_path)
    backlogs = tmp_path / "backlogs"
    backlogs.mkdir()
    (backlogs / "a.md").write_text("✅\n")
    (backlogs / "b.md").write_text("✅\n")
    report = compute_sprint_progress(
        backlogs_dir=backlogs, window_days=7,
    )
    # 2 completed / 1 week = 2.0
    assert abs(report.weekly_throughput - 2.0) < 0.001


def test_sprint_progress_to_dict() -> None:
    report = SprintProgressReport(
        window_days=7, sprints_total=10, sprints_started=5,
        sprints_completed=3, sprints_obsolete=1,
        weekly_throughput=3.0, burn_rate=0.3,
    )
    d = report.to_dict()
    assert d["sprints_total"] == 10
    assert d["weekly_throughput"] == 3.0


# ---------------------------------------------------------------------------
# HookHealth
# ---------------------------------------------------------------------------


def test_hook_health_empty() -> None:
    report = compute_hook_health([])
    assert report.total_invocations == 0
    assert report.failure_rate == 0.0


def test_hook_health_counts_invocations() -> None:
    events = [
        _Ev("claude.hook.pre_tool_use"),
        _Ev("claude.hook.post_tool_use"),
        _Ev("claude.hook.stop"),
        _Ev("codex.hook.session_start"),
        _Ev("dev.build.done"),  # not a hook
    ]
    report = compute_hook_health(events)
    assert report.total_invocations == 4
    assert report.failed_invocations == 0


def test_hook_health_failure_rate() -> None:
    events = [
        _Ev("claude.hook.stop") for _ in range(10)
    ] + [
        _Ev("hook.write_failed") for _ in range(2)
    ]
    report = compute_hook_health(events)
    assert report.failed_invocations == 2
    assert abs(report.failure_rate - 0.2) < 0.001


def test_hook_health_orphan_rate() -> None:
    events = [
        _Ev("claude.hook.stop") for _ in range(10)
    ] + [
        _Ev("hook.orphan_event") for _ in range(3)
    ]
    report = compute_hook_health(events)
    assert report.orphan_invocations == 3
    assert abs(report.orphan_rate - 0.3) < 0.001


def test_hook_health_by_event_type() -> None:
    events = [
        _Ev("claude.hook.stop"), _Ev("claude.hook.stop"),
        _Ev("codex.hook.session_start"),
    ]
    report = compute_hook_health(events)
    assert report.by_event_type["claude.hook.stop"] == 2
    assert report.by_event_type["codex.hook.session_start"] == 1


def test_hook_health_to_dict_serializable() -> None:
    report = compute_hook_health([_Ev("claude.hook.stop")])
    d = report.to_dict()
    assert d["total_invocations"] == 1
    assert "by_event_type" in d
    # Verify dict serialises (no exception)
    import json
    json.dumps(d)
