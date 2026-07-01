from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.robustness_suite import build_scorecard
from tests.e2e.scripted_runner import run_scripted_scenario
from tests.e2e.w5_phase_report import generate_report


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_scripted_happy_path_reaches_task_and_feature_done(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="happy_path",
        tasks_count=2,
    )

    assert result.task_done_count == 2
    assert result.feature_done_count == 1
    assert result.cost_entries == 12
    assert _read_json(result.state_dir / "kanban.json") == []
    assert _read_json(result.state_dir / "feature_list.json") == []

    task_archive = next((result.state_dir / "kanban").glob("*.json"))
    feature_archive = next((result.state_dir / "feature_list").glob("*.json"))
    assert len(_read_json(task_archive)) == 2
    assert _read_json(feature_archive)[0]["status"] == "done"

    phases = generate_report(result.events_path)
    assert all(phase.status == "pass" for phase in phases)


def test_scripted_rework_path_loops_then_closes(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="review_rejected_once",
    )
    events = _read_jsonl(result.events_path)

    assert result.task_done_count == 1
    assert any(event["type"] == "review.rejected" for event in events)
    assert sum(1 for event in events if event["type"] == "dev.build.done") == 2
    assert events[-1]["type"] == "feature.status_changed"


def test_scripted_critic_rejects_design_then_arch_recovers(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="critic_rejected_once",
    )
    events = _read_jsonl(result.events_path)

    assert result.task_done_count == 1
    assert sum(1 for event in events if event["type"] == "arch.proposal.done") == 2
    assert any(
        event["type"] == "gate.failed"
        and event.get("actor") == "critic"
        for event in events
    )
    assert any(event["type"] == "design.critique.done" for event in events)
    assert events[-1]["type"] == "feature.status_changed"
    scorecard = build_scorecard(
        state_dir=result.state_dir,
        scenario="scripted",
        preset="scripted",
    )
    assert scorecard.design_reject_count == 1
    assert scorecard.design_rework_recovery_rate == 1.0


def test_scripted_test_failure_reworks_dev_then_closes(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="test_failed_once",
    )
    events = _read_jsonl(result.events_path)

    assert result.task_done_count == 1
    assert result.cost_entries == 9
    assert any(event["type"] == "test.failed" for event in events)
    assert sum(1 for event in events if event["type"] == "dev.build.done") == 2
    assert sum(1 for event in events if event["type"] == "test.passed") == 1


def test_scripted_judge_failure_reworks_dev_then_closes(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="judge_failed_once",
    )
    events = _read_jsonl(result.events_path)

    assert result.task_done_count == 1
    assert result.cost_entries == 10
    assert any(event["type"] == "judge.failed" for event in events)
    assert sum(1 for event in events if event["type"] == "dev.build.done") == 2
    assert sum(1 for event in events if event["type"] == "judge.passed") == 1


def test_scripted_duplicate_event_does_not_double_close_or_double_cost(
    tmp_path: Path,
):
    duplicate = run_scripted_scenario(
        tmp_path / "duplicate",
        scenario="duplicate_event",
    )
    baseline = run_scripted_scenario(
        tmp_path / "baseline",
        scenario="happy_path",
    )
    events = _read_jsonl(duplicate.events_path)

    assert sum(1 for event in events if event["type"] == "dev.build.done") == 2
    assert duplicate.task_done_count == 1
    assert duplicate.feature_done_count == 1
    assert duplicate.cost_entries == baseline.cost_entries


def test_scripted_invalid_transition_is_recorded_without_state_corruption(
    tmp_path: Path,
):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="invalid_transition",
    )
    events = _read_jsonl(result.events_path)
    kanban = _read_json(result.state_dir / "kanban.json")
    features = _read_json(result.state_dir / "feature_list.json")

    assert result.task_done_count == 0
    assert result.feature_done_count == 0
    assert result.cost_entries == 0
    assert any(event["type"] == "task.invalid_transition" for event in events)
    assert kanban[0]["status"] == "backlog"
    assert features[0]["status"] == "active"


def test_scripted_multi_task_distributes_dev_and_test_instances(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="happy_path",
        tasks_count=3,
    )
    events = _read_jsonl(result.events_path)
    dispatches = [
        event
        for event in events
        if event["type"] == "task.dispatched"
        and (event.get("payload") or {}).get("role") in {"dev", "test"}
    ]

    assert result.task_done_count == 3
    assert {
        event["payload"]["instance_id"]
        for event in dispatches
        if event["payload"]["role"] == "dev"
    } == {"dev-1", "dev-2"}
    assert {
        event["payload"]["instance_id"]
        for event in dispatches
        if event["payload"]["role"] == "test"
    } == {"test-1", "test-2"}


def test_scripted_quality_gates_cover_every_dev_completed_task(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="happy_path",
        tasks_count=3,
    )
    events = _read_jsonl(result.events_path)
    dev_done = {
        event["task_id"] for event in events if event["type"] == "dev.build.done"
    }
    reviewed = {
        event["task_id"] for event in events if event["type"] == "review.approved"
    }
    tested = {
        event["task_id"] for event in events if event["type"] == "test.passed"
    }
    judged = {
        event["task_id"] for event in events if event["type"] == "judge.passed"
    }

    assert dev_done == set(result.task_ids)
    assert {
        event["task_id"] for event in events if event["type"] == "arch.proposal.done"
    } == dev_done
    assert {
        event["task_id"] for event in events if event["type"] == "design.critique.done"
    } == dev_done
    assert reviewed == dev_done
    assert tested == dev_done
    assert judged == dev_done


def test_scripted_worker_timeout_recovers_to_another_instance(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="worker_timeout_recovery",
        tasks_count=2,
    )
    events = _read_jsonl(result.events_path)

    assert result.task_done_count == 2
    assert any(event["type"] == "worker.stuck" for event in events)
    assert any(
        event["type"] == "dev.build.done"
        and (event.get("payload") or {}).get("recovered_from") == "dev-1"
        for event in events
    )


def test_scripted_scorecard_can_be_built_from_state_artifacts(tmp_path: Path):
    result = run_scripted_scenario(
        tmp_path / "run",
        scenario="happy_path",
        tasks_count=2,
    )

    scorecard = build_scorecard(
        state_dir=result.state_dir,
        scenario="scripted",
        preset="scripted",
    )

    assert scorecard.event_count > 0
    assert scorecard.cost_entries == result.cost_entries
    assert scorecard.task_done_count == 2
    assert scorecard.feature_done_count == 1
    assert scorecard.backend_usage == {"scripted": 12}
    assert scorecard.critic_coverage_rate == 1.0
    assert all(scorecard.artifact_completeness.values())
