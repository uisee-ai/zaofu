from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import GoalConfig, ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator


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


def test_orchestrator_reuses_blocked_claim_after_verify_closes_feedback(
    tmp_path: Path,
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
        causation_id=rework.id,
        correlation_id="R-RESUME",
        payload={
            "workflow_run_id": "R-RESUME",
            "task_id": "T-1",
            "dispatch_id": "dispatch-1",
            "rework_request_event_id": rework.id,
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
