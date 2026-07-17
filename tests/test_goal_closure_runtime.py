from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.goal_closure_runtime import process_goal_closure_result
from zf.runtime.terminal_events import is_successful_run_terminal


def _result(verdict: str, action: str) -> dict:
    status = {"passed": "closed", "rejected": "open", "blocked": "blocked"}[verdict]
    return {
        "schema_version": "goal-closure-result.v1",
        "workflow_run_id": "run-1",
        "goal_id": "GOAL-1",
        "flow_kind": "refactor",
        "task_map_generation": "generation-1",
        "target_commit": "a" * 40,
        "objective_ref": "docs/objective.md",
        "goal_claim_set_ref": "goal-closure/claim-sets/claims.json",
        "goal_claim_set_digest": "b" * 64,
        "planning_result_ref": "artifacts/task-map.json",
        "candidate_ref": "candidate/GOAL-1",
        "closure_fact_ref": "goal-closure/facts/fact.json",
        "closure_fact_digest": "c" * 64,
        "input_result_refs": ["call-results/verify.json"],
        "goal_coverage": [{
            "goal_claim_id": "GOAL-AC-1",
            "status": status,
            "supporting_result_refs": (
                ["call-results/verify.json"] if status == "closed" else []
            ),
        }],
        "open_gap_refs": ["gaps/GAP-1.json"] if verdict == "rejected" else [],
        "verdict": verdict,
        "recommended_action": action,
        "summary": "goal closure synthesis",
    }


def _runtime(tmp_path):  # noqa: ANN001
    log = EventLog(tmp_path / "events.jsonl")
    return SimpleNamespace(
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(goal=SimpleNamespace(enabled=False)),
    )


def _source(verdict: str, action: str) -> ZfEvent:
    return ZfEvent(
        id=f"closure-{verdict}",
        type="goal.closure.synthesized",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "pdd_id": "GOAL-1",
            "feature_id": "GOAL-1",
            "goal_closure_result": _result(verdict, action),
            "admitted_call_result_ref": {
                "ref": "call-results/judge.json",
                "sha256": "d" * 64,
            },
        },
    )


def test_rejected_result_routes_once_and_projects_legacy_only(tmp_path) -> None:  # noqa: ANN001
    runtime = _runtime(tmp_path)
    source = _source("rejected", "gap_plan")
    runtime.event_log.append(source)

    process_goal_closure_result(runtime, source)
    process_goal_closure_result(runtime, source)

    events = runtime.event_log.read_all()
    types = [event.type for event in events]
    assert types.count("goal.closure.rejected") == 1
    assert types.count("orchestrator.replan_requested") == 1
    assert types.count("judge.failed") == 1
    assert types.count("goal.closure.compat.projected") == 1


def test_passed_compat_projection_is_not_run_terminal(tmp_path) -> None:  # noqa: ANN001
    runtime = _runtime(tmp_path)
    source = _source("passed", "complete")
    runtime.event_log.append(source)

    process_goal_closure_result(runtime, source)

    compat = next(
        event for event in runtime.event_log.read_all()
        if event.type == "judge.passed"
    )
    assert compat.payload["authority"] == "compat_projection"
    assert is_successful_run_terminal(compat) is False
    assert not any(
        event.type == "run.goal.completed"
        for event in runtime.event_log.read_all()
    )


def test_blocked_result_routes_to_run_manager_attention(tmp_path) -> None:  # noqa: ANN001
    runtime = _runtime(tmp_path)
    source = _source("blocked", "human")
    runtime.event_log.append(source)

    process_goal_closure_result(runtime, source)

    attention = next(
        event for event in runtime.event_log.read_all()
        if event.type == "runtime.attention.needed"
    )
    assert attention.payload["owner_route"] == "run_manager"
    assert attention.payload["human_action_required"] is True
