from __future__ import annotations

from pathlib import Path

from zf.autoresearch.artifacts import (
    ReplanProposal,
    build_replan_contract_eval_request,
)
from zf.autoresearch.loop_requests import (
    RESEARCH_MODE_EXPECTED_OUTPUTS,
    build_loop_request_payload,
)
from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def test_autoresearch_replan_proposal_requests_contract_eval() -> None:
    proposal = ReplanProposal(
        artifact_id="replan-run-1-2",
        run_id="run-1",
        iteration=2,
        reason="regression",
        risk="high",
        evidence_refs=[".zf/autoresearch/run-1/eval.json"],
    )

    request = build_replan_contract_eval_request(
        proposal,
        proposal_ref=".zf/autoresearch/run-1/replan.json",
        trigger_event_id="evt-trigger",
        feature_id="F-REPLAN",
        candidate_task_map_ref=".zf/artifacts/F-REPLAN/task-map-v2.json",
        old_task_map_ref=".zf/artifacts/F-REPLAN/task-map-v1.json",
        expected_current_task_map_ref=".zf/artifacts/F-REPLAN/task-map-v1.json",
        profile="strict",
    )

    assert request["schema_version"] == "replan-contract-eval-request.v1"
    assert request["proposal_ref"].endswith("replan.json")
    assert request["trigger_event_id"] == "evt-trigger"
    assert request["feature_id"] == "F-REPLAN"
    assert request["candidate_task_map_ref"].endswith("task-map-v2.json")
    assert request["apply_policy"] == "proposal_only"
    assert request["sandbox_required"] is True
    assert request["idempotency_key"].startswith("replan-eval:")


def test_replan_eval_request_dedupes_repeated_supervisor_tick() -> None:
    proposal = ReplanProposal(artifact_id="replan-repeat", run_id="run-1")
    kwargs = {
        "proposal_ref": ".zf/autoresearch/run-1/replan.json",
        "trigger_event_id": "evt-trigger",
        "feature_id": "F-REPLAN",
        "candidate_task_map_ref": ".zf/artifacts/F-REPLAN/task-map-v2.json",
        "expected_current_task_map_ref": ".zf/artifacts/F-REPLAN/task-map-v1.json",
    }

    first = build_replan_contract_eval_request(proposal, **kwargs)
    second = build_replan_contract_eval_request(proposal, **kwargs)

    assert first["request_id"] == second["request_id"]
    assert first["idempotency_key"] == second["idempotency_key"]


def test_autoresearch_loop_request_carries_plan_insight_probe_contract() -> None:
    request = build_loop_request_payload(
        {
            "trigger_id": "arinv-plan-gap",
            "invocation_id": "arinv-plan-gap",
            "source": "autoresearch.invocation.accepted",
            "research_mode": "probe",
            "source_insight_ref": "projection:supervisor/plan-insights.json#pins-1",
            "insight_type": "plan_gap",
            "expected_output": ["research_probe_report", "evidence_refs"],
            "reason": "plan gap requires research",
        },
        source_event_id="evt-accepted",
    )

    assert request["mode"] == "probe"
    assert request["source_insight_ref"].endswith("#pins-1")
    assert request["expected_output"] == ["research_probe_report", "evidence_refs"]
    assert request["proposal_only"] is True
    assert request["direct_mainline_apply"] is False


def test_autoresearch_loop_request_has_default_outputs_for_research_modes() -> None:
    for mode, expected in RESEARCH_MODE_EXPECTED_OUTPUTS.items():
        request = build_loop_request_payload(
            {"trigger_id": f"tr-{mode}", "research_mode": mode},
            source_event_id=f"evt-{mode}",
        )

        assert request["mode"] == mode
        assert request["expected_output"] == expected
        assert request["proposal_only"] is True

    fallback = build_loop_request_payload(
        {"trigger_id": "tr-unknown", "research_mode": "surprise"},
        source_event_id="evt-unknown",
    )
    assert fallback["mode"] == "debug"
    assert fallback["expected_output"] == RESEARCH_MODE_EXPECTED_OUTPUTS["debug"]


def test_replan_proposal_event_requests_contract_eval_without_mutating_tasks(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="replan-bridge"),
        session=SessionConfig(tmux_session="replan-bridge"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    orch = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="replan-bridge", dry_run=True)),
    )
    event = ZfEvent(
        type="replan.proposal.created",
        actor="zf-autoresearch",
        task_id="TASK-1",
        payload={
            "proposal": {"artifact_id": "replan-1", "reason": "better route"},
            "proposal_ref": ".zf/autoresearch/run-1/replan.json",
            "feature_id": "F-1",
            "candidate_task_map_ref": ".zf/artifacts/F-1/task-map-v2.json",
            "expected_current_task_map_ref": ".zf/artifacts/F-1/task-map-v1.json",
        },
    )
    log.append(event)

    first = orch._on_replan_proposal_created(event)
    second = orch._on_replan_proposal_created(event)

    requests = [
        item for item in log.read_all()
        if item.type == "replan.contract_eval.requested"
    ]
    assert first is not None and first.action == "notify"
    assert second is not None and second.action == "skip"
    assert len(requests) == 1
    assert requests[0].payload["proposal_id"] == "replan-1"
    assert requests[0].payload["candidate_task_map_ref"].endswith("task-map-v2.json")
    assert requests[0].payload["apply_policy"] == "proposal_only"
    assert (state_dir / "kanban.json").read_text(encoding="utf-8") == "[]\n"
