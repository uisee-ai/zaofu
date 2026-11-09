from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_pipeline_acceptance import (
    reconcile_task_pipeline_acceptance_routes,
)


def _runtime(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig()
    task = Task(id="TASK-RISK", title="Risk task", status="in_progress")
    TaskStore(state_dir / "kanban.json").add(task)
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
        event_writer=EventWriter(log),
        task_store=TaskStore(state_dir / "kanban.json"),
    )


def _result(verdict: str) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "task-integration-acceptance-result.v1",
        "workflow_run_id": "run-1",
        "task_id": "TASK-RISK",
        "task_map_generation": "map-g1",
        "contract_revision": "contract-r1",
        "risk_class": "high",
        "integration_admission_profile": "risk_review",
        "operation_id": "op-risk-1",
        "operation_generation": 1,
        "attempt_id": "attempt-risk-1",
        "exact_task_target_commit": "abc123",
        "target_commit": "abc123",
        "verification_result_ref": "artifacts/verify/result.json",
        "verification_result_digest": "a" * 64,
        "contract_snapshot_ref": "artifacts/contracts/task.json",
        "contract_snapshot_digest": "b" * 64,
        "target_snapshot_ref": "artifacts/targets/task.json",
        "target_snapshot_digest": "c" * 64,
        "execution_profile_id": "direct-v1",
        "execution_profile_digest": "d" * 64,
        "risk_review_timeout_seconds": 180,
        "risk_review_max_turns": 1,
        "risk_review_budget_usd": 1.0,
        "required_read_ledger_ref": "artifacts/reads/ledger.json",
        "required_read_ledger_digest": "e" * 64,
        "execution_status": "completed",
        "verdict": verdict,
        "evidence_refs": ["artifacts/verify/result.json"],
        "finding_refs": [],
        "feedback_refs": [],
        "feedback": [],
        "delta_intent": {},
        "blocker": {},
        "residual_risks": [],
    }
    if verdict == "revise":
        result["feedback"] = [{"scope": "TASK-RISK", "action": "fix API"}]
    elif verdict == "replan":
        result["delta_intent"] = {
            "action": "split_task",
            "reason": "cross-domain ownership",
        }
    elif verdict == "block":
        result["blocker"] = {
            "class": "external_dependency",
            "owner": "operator",
        }
    return result


def _run(runtime, verdict: str, *, generation: str = "map-g1"):
    descriptor = write_immutable_json_sidecar(
        runtime.state_dir,
        _result(verdict),
        root="call-results/control/task-integration-acceptance-result.v1",
        kind="call_control_result",
        schema_version="task-integration-acceptance-result.v1",
        created_by="test",
    )
    canonical = runtime.event_writer.append(ZfEvent(
        type="task.pipeline.acceptance.completed",
        task_id="TASK-RISK",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": "op-risk-1",
            "task_pipeline_stage": "acceptance_review",
            "operation_generation": 1,
            "task_map_generation": "map-g1",
            "control_result_ref": descriptor,
        },
        correlation_id="run-1",
    ))
    operation = {
        "workflow_run_id": "run-1",
        "operation_id": "op-risk-1",
        "task_id": "TASK-RISK",
        "task_pipeline_stage": "acceptance_review",
        "operation_generation": 1,
        "task_map_generation": "map-g1",
        "active_attempt_id": "attempt-risk-1",
        "status": "settled",
        "semantic_verdict": verdict,
        "admitted_control_result_ref": descriptor,
    }
    decisions = reconcile_task_pipeline_acceptance_routes(
        runtime,
        generation_contexts={
            "TASK-RISK": {
                "workflow_run_id": "run-1",
                "task_map_generation": generation,
                "flow_kind": "prd",
            }
        },
        operation_rows=[operation],
    )
    return canonical, decisions


def test_revise_emits_feedback_route_without_blocking_task(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    _, decisions = _run(runtime, "revise")

    assert [item.action for item in decisions] == [
        "task_pipeline_acceptance_revise"
    ]
    assert runtime.task_store.get("TASK-RISK").status == "in_progress"
    assert any(
        event.type == "task.pipeline.acceptance.revision_requested"
        for event in runtime.event_log.read_all()
    )


def test_replan_requests_oa_delta_and_blocks_integration(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    orchestration = runtime.config.workflow.orchestration
    orchestration.mode = "semantic_control"
    orchestration.checkpoints = ["semantic_failure"]
    orchestration.checkpoint_policies = {"semantic_failure": "blocking"}

    _, decisions = _run(runtime, "replan")
    events = runtime.event_log.read_all()

    assert [item.action for item in decisions] == [
        "task_pipeline_acceptance_replan"
    ]
    assert runtime.task_store.get("TASK-RISK").status == "blocked"
    assert any(
        event.type == "orchestrator.semantic.failure.requested"
        for event in events
    )


def test_block_verdict_materializes_typed_blocker_once(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    _, first = _run(runtime, "block")
    _, second = _run(runtime, "block")

    assert [item.action for item in first] == [
        "task_pipeline_acceptance_block"
    ]
    assert second == []
    blocked = [
        event for event in runtime.event_log.read_all()
        if event.type == "task.pipeline.acceptance.blocked"
    ]
    assert len(blocked) == 1
    assert blocked[0].payload["blocker"]["owner"] == "operator"


@pytest.mark.parametrize("verdict,crash_after", [
    ("replan", "task.pipeline.acceptance.replan_requested"),
    ("replan", "orchestrator.rework.triage.requested"),
    ("block", "task.pipeline.acceptance.blocked"),
])
def test_replay_repairs_route_followups(tmp_path, monkeypatch, verdict, crash_after):
    runtime = _runtime(tmp_path)
    append = runtime.event_writer.append

    def crash(event):
        result = append(event)
        if event.type == crash_after:
            raise SystemExit("simulated process loss after durable append")
        return result

    monkeypatch.setattr(runtime.event_writer, "append", crash)
    with pytest.raises(SystemExit):
        _run(runtime, verdict)
    assert runtime.task_store.get("TASK-RISK").status == "in_progress"
    monkeypatch.setattr(runtime.event_writer, "append", append)
    _run(runtime, verdict)
    _run(runtime, verdict)
    assert runtime.task_store.get("TASK-RISK").status == "blocked"
    types = [e.type for e in runtime.event_log.read_all()]
    assert types.count(crash_after) == 1
    if verdict == "replan":
        assert types.count("orchestrator.rework.triage.requested") == 1


def test_old_generation_acceptance_cannot_block_current_task(tmp_path):
    runtime = _runtime(tmp_path)
    _, decisions = _run(runtime, "replan", generation="map-g2")
    assert decisions == []
    assert runtime.task_store.get("TASK-RISK").status == "in_progress"
    assert not any(e.type.endswith("replan_requested") for e in runtime.event_log.read_all())
