"""Sprint §2 — eval snapshot collector tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.autoresearch.loop import (
    EvalSnapshot,
    collect_autoresearch_eval_metrics,
    collect_eval_snapshot,
    compute_eval_delta,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _write_health_snapshot(
    state_dir: Path,
    *,
    completed: int = 0,
    rework_looped: int = 0,
    coordinator_ratio: float | None = 0.5,
    healthy: int = 8,
    warning: int = 3,
    critical: int = 7,
    open_backlog: int = 0,
) -> None:
    """Write a fake .zf/projections/health.json fixture matching the
    schema build_health_snapshot produces, so the collector can short-
    circuit the full kanban+events read."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "projections").mkdir(exist_ok=True)
    snap = {
        "throughput": {
            "tasks_completed": completed,
            "tasks_failed": 0,
            "rework_looped_count": rework_looped,
            "rework_looped_tasks": [],
            "rework_threshold": 3,
        },
        "coordinator": {
            "total_wakes": 100,
            "counts": {"dispatch": 5, "no_action": 28, "wait": 14},
            "dispatch_no_action_ratio": coordinator_ratio,
            "health_band": "over_cautious",
        },
        "metric_diagnostics": [
            *[{"health_band": "healthy"} for _ in range(healthy)],
            *[{"health_band": "warning"} for _ in range(warning)],
            *[{"health_band": "critical"} for _ in range(critical)],
        ],
        "metrics_band_summary": {
            "healthy": healthy, "warning": warning, "critical": critical,
        },
    }
    (state_dir / "projections" / "health.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2)
    )


def _write_kanban_with_backlog(state_dir: Path, *, backlog: int) -> None:
    """Write .zf/kanban.json with `backlog` tasks in backlog status."""
    state_dir.mkdir(parents=True, exist_ok=True)
    tasks = []
    for i in range(backlog):
        tasks.append({
            "id": f"TASK-B{i:03d}",
            "title": f"backlog task {i}",
            "status": "backlog",
            "priority": 3,
        })
    (state_dir / "kanban.json").write_text(json.dumps(tasks))


# ---------------------------------------------------------------------------
# collect_eval_snapshot
# ---------------------------------------------------------------------------


def test_collect_from_health_fixture_and_kanban(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_health_snapshot(
        state_dir,
        completed=2, rework_looped=1, coordinator_ratio=0.179,
        healthy=8, warning=3, critical=7,
    )
    _write_kanban_with_backlog(state_dir, backlog=5)

    snap = collect_eval_snapshot(state_dir)

    assert isinstance(snap, EvalSnapshot)
    assert snap.completed_tasks == 2
    assert snap.rework_looped == 1
    assert snap.healthy_metrics == 8
    assert snap.warning_metrics == 3
    assert snap.critical_metrics == 7
    assert snap.coordinator_ratio == pytest.approx(0.179)
    assert snap.open_backlog_count == 5


def test_collect_handles_null_coordinator_ratio(tmp_path: Path) -> None:
    """When no_action=0, the health snapshot reports ratio=None. The
    collector must coerce to 0.0 (or math.inf) without crashing."""
    state_dir = tmp_path / ".zf"
    _write_health_snapshot(state_dir, coordinator_ratio=None)
    _write_kanban_with_backlog(state_dir, backlog=0)

    snap = collect_eval_snapshot(state_dir)

    # Either 0.0 sentinel or float — must not raise.
    assert isinstance(snap.coordinator_ratio, float)


def test_collect_missing_health_falls_back_to_zeros(tmp_path: Path) -> None:
    """If health.json doesn't exist yet (first iter before any run),
    collector must return a zero snapshot rather than raising."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_kanban_with_backlog(state_dir, backlog=2)

    snap = collect_eval_snapshot(state_dir)

    assert snap.healthy_metrics == 0
    assert snap.warning_metrics == 0
    assert snap.critical_metrics == 0
    assert snap.open_backlog_count == 2  # kanban still readable


def test_collect_autoresearch_eval_metrics_sources_and_lop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    state_dir = project / "runtime-state"
    project.mkdir()
    state_dir.mkdir()
    (project / "zf.yaml").write_text(
        "\n".join([
            'version: "1.0"',
            "project:",
            "  name: test",
            "  state_dir: runtime-state",
            "workflow:",
            "  harness_profile: baseline",
            "  strict_triggers:",
            "    rework_attempts_gte: 1",
            "    context_usage_gte: 0.85",
        ])
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="worker.heartbeat", actor="dev"))
    log.append(ZfEvent(type="test.passed", actor="test"))
    log.append(ZfEvent(
        type="worker.context.warning",
        actor="dev",
        payload={"context_usage_ratio": 0.9},
    ))
    snap = EvalSnapshot(healthy_metrics=1, warning_metrics=0, critical_metrics=0,
                        coordinator_ratio=0.0, open_backlog_count=0,
                        rework_looped=1, completed_tasks=0)

    metrics = collect_autoresearch_eval_metrics(
        state_dir,
        eval_snapshot=snap,
        run_status="failed",
        head_changed_since_prev=True,
    )

    assert metrics.metric_sources["profile"].startswith("docs/design/45")
    assert metrics.autoresearch.strict_escalated is True
    assert "rework_looped" in metrics.autoresearch.strict_trigger_reason
    assert metrics.eval.test_gate_passed is True
    assert metrics.lop.state == "context_warn"
    assert metrics.lop.recommended_action == "continuation"
    assert metrics.lop.freshness.context_usage_ratio == pytest.approx(0.9)
    assert metrics.lop.freshness.worktree_head_changed is True


def test_collect_autoresearch_eval_metrics_reports_context_route_resume(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    context_event = ZfEvent(
        type="worker.context.critical",
        actor="dev-1",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "role": "dev",
            "instance_id": "dev-1",
            "backend": "claude-code",
            "context_usage_ratio": 0.93,
            "session_ref": "session-1",
            "source": "session_reader",
            "reason": "hard_cap_exceeded",
        },
    )
    log.append(context_event)
    log.append(ZfEvent(
        type="completion_audit.routed",
        actor="zf-cli",
        task_id="TASK-1",
        causation_id=context_event.id,
        payload={
            "route": "retry",
            "reason": "context critical: hard_cap_exceeded",
            "trigger_event_type": "worker.context.critical",
            "trigger_event_id": context_event.id,
            "resume_packet_path": ".zf/resume_packets/TASK-1.json",
        },
    ))
    snap = EvalSnapshot(healthy_metrics=0, warning_metrics=0, critical_metrics=1,
                        coordinator_ratio=0.0, open_backlog_count=0,
                        rework_looped=0, completed_tasks=0)

    metrics = collect_autoresearch_eval_metrics(
        state_dir,
        eval_snapshot=snap,
        run_status="failed",
        head_changed_since_prev=False,
    )

    assert metrics.lop.observed_route == "retry"
    assert "context_event=worker.context.critical" in metrics.lop.route_reason
    assert "resume_packet_path=.zf/resume_packets/TASK-1.json" in metrics.lop.route_reason
    assert metrics.lop.recovery.context_route_reason == "context critical: hard_cap_exceeded"
    assert metrics.lop.recovery.resume_packet_path == ".zf/resume_packets/TASK-1.json"


def test_collect_autoresearch_eval_metrics_does_not_write_truth(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    snap = EvalSnapshot(healthy_metrics=0, warning_metrics=0, critical_metrics=0,
                        coordinator_ratio=0.0, open_backlog_count=0,
                        rework_looped=0, completed_tasks=0)

    collect_autoresearch_eval_metrics(
        state_dir,
        eval_snapshot=snap,
        run_status="failed",
        head_changed_since_prev=False,
    )

    for name in (
        "events.jsonl",
        "kanban.json",
        "feature_list.json",
        "session.yaml",
        "role_sessions.yaml",
    ):
        assert not (state_dir / name).exists()


def test_collect_autoresearch_eval_metrics_accepts_canonical_done_events(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-1"))
    log.append(ZfEvent(type="test.passed", actor="test", task_id="TASK-1"))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="TASK-1"))
    log.append(ZfEvent(type="discriminator.passed", actor="zf-cli", task_id="TASK-1"))
    log.append(ZfEvent(type="task.done.evidence", actor="zf-cli", task_id="TASK-1"))
    log.append(ZfEvent(
        type="task.status_changed",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"from": "in_progress", "to": "done"},
    ))
    snap = EvalSnapshot(healthy_metrics=1, warning_metrics=0, critical_metrics=0,
                        coordinator_ratio=0.0, open_backlog_count=0,
                        rework_looped=0, completed_tasks=1)

    metrics = collect_autoresearch_eval_metrics(
        state_dir,
        eval_snapshot=snap,
        run_status="passed",
        head_changed_since_prev=False,
    )

    assert metrics.eval.required_command_passed is True
    assert metrics.eval.terminal_evidence_present is True
    assert metrics.eval.quality_gates_passed is True
    assert metrics.lop.why_not_done_count == 0
    assert metrics.lop.next_required_event == ""


def test_collect_autoresearch_eval_metrics_treats_passed_after_rework_as_done(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="judge.failed", actor="judge", task_id="TASK-1"))
    log.append(ZfEvent(type="task.rework.requested", actor="zf-cli", task_id="TASK-1"))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="TASK-1"))
    log.append(ZfEvent(type="discriminator.passed", actor="zf-cli", task_id="TASK-1"))
    log.append(ZfEvent(
        type="task.status_changed",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"from": "testing", "to": "done"},
    ))
    snap = EvalSnapshot(healthy_metrics=1, warning_metrics=0, critical_metrics=0,
                        coordinator_ratio=0.0, open_backlog_count=0,
                        rework_looped=1, completed_tasks=1)

    metrics = collect_autoresearch_eval_metrics(
        state_dir,
        eval_snapshot=snap,
        run_status="passed_after_rework",
        head_changed_since_prev=False,
    )

    assert metrics.lop.recommended_action == "done"
    assert metrics.lop.why_not_done_count == 0
    assert metrics.lop.state == "healthy"


def test_collect_autoresearch_eval_metrics_flags_readonly_gate_mutation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-1"))
    log.append(ZfEvent(
        type="test.passed",
        actor="test",
        task_id="TASK-1",
        payload={"changed_files": ["proof.txt"]},
    ))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="TASK-1"))
    log.append(ZfEvent(type="discriminator.passed", actor="zf-cli", task_id="TASK-1"))
    log.append(ZfEvent(
        type="task.status_changed",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"from": "testing", "to": "done"},
    ))
    snap = EvalSnapshot(healthy_metrics=1, warning_metrics=0, critical_metrics=0,
                        coordinator_ratio=0.0, open_backlog_count=0,
                        rework_looped=0, completed_tasks=1)

    metrics = collect_autoresearch_eval_metrics(
        state_dir,
        eval_snapshot=snap,
        run_status="passed",
        head_changed_since_prev=False,
    )

    assert metrics.eval.mutation_warning is True
    assert metrics.eval.quality_gates_passed is False
    assert metrics.eval.clean_state_passed is False
    assert metrics.eval.product_rework_count == 1
    assert metrics.eval.rework_type == "gate_integrity"
    assert metrics.lop.why_not_done_count > 0
    assert "readonly_gate_mutations=1" in metrics.lop.route_reason


# ---------------------------------------------------------------------------
# compute_eval_delta
# ---------------------------------------------------------------------------


def test_delta_improved_when_critical_drops(tmp_path: Path) -> None:
    prev = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    curr = EvalSnapshot(9, 3, 5, 0.243, 6, 0, 1)
    delta = compute_eval_delta(prev, curr)
    assert delta.healthy_delta == 1
    assert delta.critical_delta == -2
    assert delta.backlog_delta == -2
    assert delta.completed_delta == 1
    assert delta.verdict == "improved"


def test_delta_regressed_when_critical_grows(tmp_path: Path) -> None:
    prev = EvalSnapshot(8, 3, 5, 0.5, 6, 0, 1)
    curr = EvalSnapshot(7, 3, 8, 0.4, 9, 2, 0)
    delta = compute_eval_delta(prev, curr)
    assert delta.critical_delta == 3
    assert delta.verdict == "regressed"


def test_delta_unchanged_when_no_movement() -> None:
    prev = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    curr = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    delta = compute_eval_delta(prev, curr)
    assert delta.verdict == "unchanged"
    assert delta.healthy_delta == 0
    assert delta.critical_delta == 0


def test_delta_completed_progress_counts_as_improved() -> None:
    """Completing a task is the strongest signal of progress even if
    other metrics stay flat."""
    prev = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    curr = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 1)
    delta = compute_eval_delta(prev, curr)
    assert delta.completed_delta == 1
    assert delta.verdict == "improved"


def test_delta_critical_drop_beats_healthy_drop() -> None:
    """If critical goes down but healthy also goes down (some warning
    items re-categorized), verdict should still be improved — fewer
    criticals matters more."""
    prev = EvalSnapshot(8, 3, 7, 0.179, 8, 1, 0)
    curr = EvalSnapshot(7, 5, 4, 0.243, 7, 1, 0)
    delta = compute_eval_delta(prev, curr)
    assert delta.critical_delta == -3
    assert delta.verdict == "improved"
