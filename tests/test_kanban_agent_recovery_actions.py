from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.web.operator_contract import (
    KANBAN_AGENT_ALLOWED_ACTIONS,
    KANBAN_AGENT_FORBIDDEN_CAPABILITIES,
    kanban_agent_boundary,
)
from zf.web.server import create_app


def _state(tmp_path: Path) -> tuple[Path, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return state_dir, EventLog(state_dir / "events.jsonl")


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="kanban-agent-recovery-test"),
        session=SessionConfig(tmux_session="kanban-agent-recovery-test"),
        roles=[
            RoleConfig(name="dev-lane-0", backend="mock"),
            RoleConfig(name="review-lane-0", backend="mock"),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(enabled=True, graph_review_test_judge_reconcile=True),
        ),
    )


def _append_repair_failed_children_fixture(log: EventLog) -> None:
    task_map = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-RM",
            "feature_id": "CJMIN-RM",
            "trace_id": "trace-rm",
            "task_map_ref": ".zf/artifacts/CJMIN-RM/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-RM/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-rm",
    )
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-rm",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-RM",
            "feature_id": "CJMIN-RM",
            "candidate_ref": "cand/CJMIN-RM",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "completed_task_ids": ["CJMIN-GATEWAY-001"],
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
        correlation_id="trace-rm",
    )
    failed = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-rm",
            "pdd_id": "CJMIN-RM",
            "reason": "assembly failed",
        },
        causation_id=aggregate.id,
        correlation_id="trace-rm",
    )
    log.append(task_map)
    log.append(aggregate)
    log.append(failed)


def _append_candidate_failure_fixture(log: EventLog) -> None:
    log.append(ZfEvent(
        id="verify-rm-1",
        type="verify.failed",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-RM",
            "trace_id": "trace-rm",
            "target_ref": "cand/CJMIN-RM",
            "reason": "contract mismatch",
        },
        correlation_id="trace-rm",
    ))


def _client(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    return TestClient(create_app(state_dir, config=_config(), project_root=tmp_path))


def _headers() -> dict[str, str]:
    return {"x-zf-web-token": "test-token"}


def test_kanban_agent_can_request_workflow_batch_resume_through_web_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log = _state(tmp_path)
    _append_repair_failed_children_fixture(log)
    checkpoint = build_workflow_resume_projection(
        state_dir,
        _config(),
    )["batch_checkpoints"][0]
    client = _client(state_dir, tmp_path, monkeypatch)

    response = client.post(
        "/api/actions/workflow.batch.resume",
        headers=_headers(),
        json={
            "checkpoint_id": checkpoint["checkpoint_id"],
            "safe_resume_action": checkpoint["safe_resume_action"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "workflow-batch-resume"
    assert body["requested_action"] == "workflow.batch.resume"
    assert body["status"] == "applied"
    event_types = [event.type for event in log.read_all()]
    assert "web.action.requested" in event_types
    assert "runtime.action.accepted" in event_types
    assert "runtime.action.attempt.started" in event_types
    assert "workflow.resume.applied" in event_types
    assert "workflow.resume.control_action.result" in event_types
    assert "web.action.completed" in event_types


def test_kanban_agent_can_request_candidate_replan_through_web_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log = _state(tmp_path)
    _append_candidate_failure_fixture(log)
    client = _client(state_dir, tmp_path, monkeypatch)

    response = client.post(
        "/api/actions/candidate.rework.apply",
        headers=_headers(),
        json={
            "checkpoint_id": "ck-replan-1",
            "candidate_rework_action": "replan",
            "pdd_id": "CJMIN-RM",
            "source_event_id": "verify-rm-1",
            "source_event_type": "verify.failed",
            "trace_id": "trace-rm",
            "target_ref": "cand/CJMIN-RM",
            "classification": "contract_gap",
            "rework_feedback": ["contract mismatch"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "candidate-rework-apply"
    assert body["requested_action"] == "candidate.rework.apply"
    assert body["status"] == "applied"
    event_types = [event.type for event in log.read_all()]
    assert "web.action.requested" in event_types
    assert "runtime.action.accepted" in event_types
    assert "runtime.action.attempt.started" in event_types
    assert "orchestrator.replan_requested" in event_types
    assert "workflow.resume.control_action.result" in event_types
    assert "web.action.completed" in event_types


def test_kanban_agent_recovery_actions_fail_closed_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, _log = _state(tmp_path)
    client = _client(state_dir, tmp_path, monkeypatch)

    missing_checkpoint = client.post(
        "/api/actions/workflow.batch.resume",
        headers=_headers(),
        json={"safe_resume_action": "repair_failed_children"},
    )
    missing_source = client.post(
        "/api/actions/candidate.rework.apply",
        headers=_headers(),
        json={
            "checkpoint_id": "ck-replan-1",
            "candidate_rework_action": "replan",
            "pdd_id": "CJMIN-RM",
        },
    )

    assert missing_checkpoint.status_code == 422
    assert missing_checkpoint.json()["reason"] == "checkpoint_id is required"
    assert missing_source.status_code == 422
    assert missing_source.json()["action"] == "candidate-rework-apply"
    assert missing_source.json()["reason"] == "source_event_id is required"


def test_kanban_agent_recovery_contract_stays_operator_only() -> None:
    boundary = kanban_agent_boundary()

    assert "workflow-batch-resume" in KANBAN_AGENT_ALLOWED_ACTIONS
    assert "candidate-rework-apply" in KANBAN_AGENT_ALLOWED_ACTIONS
    assert boundary["direct_truth_write"] is False
    assert boundary["direct_role_dispatch"] is False
    assert boundary["direct_role_terminal_control"] is False
    assert "direct_git_mutation" in KANBAN_AGENT_FORBIDDEN_CAPABILITIES
    assert "role_terminal_control" in KANBAN_AGENT_FORBIDDEN_CAPABILITIES
