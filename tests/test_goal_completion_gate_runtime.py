from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import GoalConfig, ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.wake_patterns import LAYER2_NOISE_EVENTS, WAKE_PATTERNS


class _Transport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _orchestrator(tmp_path: Path) -> tuple[Orchestrator, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="goal-gate-runtime"),
        goal=GoalConfig(enabled=True),
    )
    return (
        Orchestrator(state_dir, config, _Transport()),  # type: ignore[arg-type]
        EventLog(state_dir / "events.jsonl"),
    )


def test_orchestrator_emits_claim_before_unique_completion(tmp_path: Path) -> None:
    orchestrator, log = _orchestrator(tmp_path)
    log.append(ZfEvent(
        type="run.goal.started",
        payload={"run_id": "R-RUNTIME", "objective": "ship product"},
    ))
    judge = ZfEvent(type="judge.passed", id="judge-runtime", payload={})
    log.append(judge)

    orchestrator._maybe_complete_run_goal(judge)
    orchestrator._maybe_complete_run_goal(judge)

    events = log.read_all()
    types = [event.type for event in events]
    claim_index = types.index("run.goal.completion.claimed")
    completion_index = types.index("run.goal.completed")
    assert claim_index < completion_index
    assert types.count("run.goal.completion.claimed") == 1
    assert types.count("run.goal.completed") == 1


def test_periodic_reconcile_recovers_missing_completion_claim(
    tmp_path: Path,
) -> None:
    orchestrator, log = _orchestrator(tmp_path)
    run_id = "R-RESTART-CLAIM"
    log.append(ZfEvent(
        type="run.goal.started",
        correlation_id=run_id,
        payload={"run_id": run_id, "objective": "recover completion"},
    ))
    log.append(ZfEvent(
        type="judge.passed",
        correlation_id=run_id,
        payload={"workflow_run_id": run_id},
    ))

    orchestrator._reconcile_run_goal_completion()
    orchestrator._reconcile_run_goal_completion()

    types = [event.type for event in log.read_all()]
    assert types.count("run.goal.completion.claimed") == 1
    assert types.count("run.goal.completed") == 1


def test_explicit_blocked_goal_closure_cannot_claim_on_redrive(
    tmp_path: Path,
) -> None:
    orchestrator, log = _orchestrator(tmp_path)
    run_id = "R-BLOCKED-CLOSURE"
    log.append(ZfEvent(
        type="run.goal.started",
        correlation_id=run_id,
        payload={"run_id": run_id},
    ))
    closure = ZfEvent(
        type="goal.closure.synthesized",
        correlation_id=run_id,
        payload={
            "goal_closure_result": {
                "workflow_run_id": run_id,
                "goal_id": run_id,
                "verdict": "blocked",
            },
        },
    )
    log.append(closure)

    orchestrator._maybe_complete_run_goal(closure)
    orchestrator._reconcile_run_goal_completion()

    types = [event.type for event in log.read_all()]
    assert "run.goal.completion.claimed" not in types
    assert "run.goal.completed" not in types


def test_orchestrator_records_blocked_claim_without_completing(tmp_path: Path) -> None:
    orchestrator, log = _orchestrator(tmp_path)
    log.append(ZfEvent(type="run.goal.started", payload={"run_id": "R-OPEN"}))
    log.append(ZfEvent(
        id="rework-open",
        type="task.rework.requested",
        task_id="T-OPEN",
        payload={"task_id": "T-OPEN", "finding_ids": ["finding-open"]},
    ))
    judge = ZfEvent(type="judge.passed", id="judge-open", payload={})
    log.append(judge)

    orchestrator._maybe_complete_run_goal(judge)

    types = [event.type for event in log.read_all()]
    assert "run.goal.completion.claimed" in types
    assert "run.goal.completion.blocked" in types
    assert "run.goal.completed" not in types


@pytest.mark.parametrize("pipeline_retry", [False, True])
def test_orchestrator_reuses_blocked_claim_after_verify_closes_feedback(
    tmp_path: Path, pipeline_retry: bool,
) -> None:
    orchestrator, log = _orchestrator(tmp_path)
    target = "a" * 40
    log.append(ZfEvent(
        type="run.goal.started",
        payload={"run_id": "R-RESUME"},
    ))
    rework = ZfEvent(
        id="rework-resume",
        type="task.rework.requested",
        task_id="T-1",
        correlation_id="R-RESUME",
        payload={
            "workflow_run_id": "R-RESUME",
            "task_id": "T-1",
            "dispatch_id": "dispatch-1",
            "finding_ids": ["finding-1"],
            "task_map_generation": "gen-1",
        },
    )
    log.append(rework)
    judge = ZfEvent(
        type="judge.passed",
        correlation_id="R-RESUME",
        payload={"workflow_run_id": "R-RESUME"},
    )
    log.append(judge)
    orchestrator._maybe_complete_run_goal(judge)

    log.append(ZfEvent(
        type="task.dispatched",
        task_id="T-1",
        causation_id="plan-trigger" if pipeline_retry else rework.id,
        correlation_id="R-RESUME",
        payload={
            "workflow_run_id": "R-RESUME",
            "task_id": "T-1",
            "dispatch_id": "dispatch-1",
            "task_map_generation": "gen-1",
            **({"task_pipeline_stage": "impl",
                "rework_feedback": [{"blocking_event_id": rework.id}]}
               if pipeline_retry else {"rework_request_event_id": rework.id}),
        },
    ))
    log.append(ZfEvent(
        type="dev.build.done",
        task_id="T-1",
        correlation_id="R-RESUME",
        payload={
            "workflow_run_id": "R-RESUME",
            "task_id": "T-1",
            "dispatch_id": "dispatch-1",
            "source_commit": target,
        },
    ))
    verified = ZfEvent(
        type="verify.passed",
        task_id="T-1",
        correlation_id="R-RESUME",
        payload={
            "workflow_run_id": "R-RESUME",
            "task_id": "T-1",
            "dispatch_id": "dispatch-1",
            "target_commit": target,
        },
    )
    log.append(verified)
    orchestrator._maybe_complete_run_goal(verified)

    types = [event.type for event in log.read_all()]
    assert types.count("run.goal.completion.claimed") == 1
    assert types.count("run.goal.completed") == 1


@pytest.mark.parametrize(
    "trigger_type",
    [
        "run.manager.action.effect.passed",
        "run.manager.tick.completed",
    ],
)
def test_run_once_rechecks_blocked_claim_after_action_effect_passes(
    tmp_path: Path,
    trigger_type: str,
) -> None:
    orchestrator, log = _orchestrator(tmp_path)
    run_id = "R-EFFECT-RESUME"
    log.append(ZfEvent(
        type="run.goal.started",
        correlation_id=run_id,
        payload={"run_id": run_id},
    ))
    log.append(ZfEvent(
        type="run.manager.action.effect.pending",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "effect_id": "effect-resume",
        },
    ))
    judge = ZfEvent(
        type="judge.passed",
        correlation_id=run_id,
        payload={"workflow_run_id": run_id},
    )
    log.append(judge)
    orchestrator._maybe_complete_run_goal(judge)
    assert not any(
        event.type == "run.goal.completed"
        for event in log.read_all()
    )

    effect = ZfEvent(
        type="run.manager.action.effect.passed",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "effect_id": "effect-resume",
            "status": "passed",
        },
    )
    log.append(effect)
    trigger = effect
    if trigger_type == "run.manager.tick.completed":
        trigger = ZfEvent(
            type=trigger_type,
            correlation_id=run_id,
            payload={"schema_version": "run-manager.tick.v1"},
        )
        log.append(trigger)
    orchestrator.run_once(events=[trigger])

    types = [event.type for event in log.read_all()]
    assert types.count("run.goal.completion.claimed") == 1
    assert types.count("run.goal.completed") == 1


def test_run_manager_tick_is_a_mechanical_completion_wake() -> None:
    assert "run.manager.tick.completed" in WAKE_PATTERNS
    assert "run.manager.tick.completed" in LAYER2_NOISE_EVENTS


def test_delivery_request_preserves_candidate_pdd_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from zf.runtime.goal_completion_gate import _apply_delivery_request
    from zf.runtime.ship import ShipResult, ShipService

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    captured: dict = {}

    def _ship(self, **kwargs):  # noqa: ANN001, ARG001
        captured.update(kwargs)
        return ShipResult(
            status="completed",
            ok=True,
            event_type="ship.completed",
            payload={"final_commit": "a" * 40},
        )

    monkeypatch.setattr(ShipService, "ship", _ship)
    runtime = SimpleNamespace(
        config=SimpleNamespace(runtime=SimpleNamespace(git=object())),
        state_dir=state_dir,
        project_root=tmp_path,
        event_log=log,
        event_writer=writer,
    )
    request = ZfEvent(
        id="delivery-request",
        type="run.delivery.requested",
        correlation_id="RUN-X",
        payload={
            "run_id": "RUN-X",
            "goal_id": "RUN-X",
            "pdd_id": "PDD-X",
            "claim_id": "claim-x",
            "delivery_operation_id": "delivery-claim-x",
            "candidate_ref": "refs/heads/candidate/PDD-X",
            "target_commit": "a" * 40,
        },
    )

    _apply_delivery_request(runtime, request)

    assert captured["pdd_id"] == "PDD-X"
    assert [event.type for event in log.read_all()] == ["run.delivery.settled"]
