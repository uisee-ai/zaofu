from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutChildConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _orchestrator(tmp_path: Path, config: ZfConfig) -> tuple[Orchestrator, _RecordingTransport]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    transport = _RecordingTransport()
    return Orchestrator(state_dir, config, transport), transport  # type: ignore[arg-type]


@pytest.mark.parametrize("cli_command", ["zf", "uv --project /fixture run zf"])
def test_refactor_review_briefing_treats_high_findings_as_plan_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_command: str,
):
    monkeypatch.setenv("ZF_CLI_CMD", cli_command)
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="review-a", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review",
                trigger="zaofu.refactor.review.requested",
                topology="fanout_reader",
                roles=["review-a"],
                target_ref="${target_ref}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="zaofu.refactor.review.ready",
                    failure_event="zaofu.refactor.review.blocked",
                ),
            ),
        ]),
    )
    orch, transport = _orchestrator(tmp_path, config)

    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.review.requested",
        actor="human",
        payload={"target_ref": "dev"},
    )])

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "finding severity describes planning risk" in briefing
    assert "even if findings include `high` or `critical`" in briefing
    assert "report.recommendation` as `approve`" in briefing
    assert "Do not invent custom recommendation values" in briefing
    assert "must stay as top-level payload fields" in briefing
    assert "zf emit workflow.child.completed" in briefing
    assert "Aggregate success event: `zaofu.refactor.review.ready`" in briefing
    assert "Do not emit the aggregate success/failure event directly" in briefing
    assert "coverage_matrix" in briefing
    assert "evidence_refs" in briefing
    failure_command = next(
        line for line in briefing.splitlines()
        if " emit workflow.child.failed " in line
    )
    args = shlex.split(failure_command)
    prefix = shlex.split(cli_command)
    assert args[:len(prefix)] == prefix
    assert "--payload" not in args
    failure_payload = json.loads(
        Path(args[args.index("--payload-file") + 1]).read_text(encoding="utf-8")
    )
    assert failure_payload["reason"] == (
        "Unable to produce a coverage/evidence-backed review report."
    )
    assert failure_payload["report"]["coverage_matrix"] == []
    assert failure_payload["report"]["uncovered"] == [
        "Unable to inspect assigned scope."
    ]


def test_refactor_review_profiled_briefing_uses_flat_semantic_result(
    tmp_path: Path,
):
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(
                name="review-a",
                backend="codex",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="review",
                    trigger="zaofu.refactor.review.requested",
                    topology="fanout_reader",
                    roles=["review-a"],
                    target_ref="${target_ref}",
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="zaofu.refactor.review.ready",
                        failure_event="zaofu.refactor.review.blocked",
                    ),
                ),
            ],
            flow_metadata={
                "result_protocol": {
                    "mode": "blocking",
                    "semantic_submit_profiles": {
                        "workflow-read": "blocking",
                    },
                },
            },
        ),
    )
    orch, transport = _orchestrator(tmp_path, config)
    provision_role_submit_credential(orch.state_dir, "review-a")

    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.review.requested",
        actor="human",
        payload={"target_ref": "dev"},
    )])

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    dispatched = next(
        event for event in orch.event_log.read_all()
        if event.type == "fanout.child.dispatched"
    )
    scratch = (
        orch.state_dir
        / dispatched.payload["payload"]["result_scratch_ref"]
    )
    semantic_template = json.loads(scratch.read_text(encoding="utf-8"))

    assert "Do not wrap it under `report`" in briefing
    assert "`status` as `passed` and `recommendation` as `approve`" in briefing
    assert "`report.status`" not in briefing
    assert "must stay as top-level payload fields" not in briefing
    assert "report" not in semantic_template
    assert "fanout_id" not in semantic_template
    assert "coverage_matrix" in semantic_template
    assert "evidence_refs" in semantic_template


def test_refactor_plan_briefing_includes_required_artifact_fields(tmp_path: Path):
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="refactor-plan-author", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="plan",
                trigger="zaofu.refactor.plan.requested",
                topology="fanout_reader",
                roles=[],
                target_ref="${target_ref}",
                children=[FanoutChildConfig(
                    role_instance="refactor-plan-author",
                    payload={
                        "refactor_contract": {
                            "schema_version": "refactor-plan-contract.v1",
                            "lane_count": 1,
                            "assembly_policy": "declared_task",
                            "assembly_task_id": "ASM-001",
                        },
                    },
                )],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="zaofu.refactor.plan.ready",
                    failure_event="zaofu.refactor.plan.blocked",
                ),
            ),
        ]),
    )
    orch, transport = _orchestrator(tmp_path, config)

    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.plan.requested",
        actor="human",
        payload={
            "target_ref": "dev",
            "review_artifact_ref": "/tmp/review.md",
            "plan_intent": "Generate P0/P1 plan.",
        },
    )])

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "/tmp/review.md" in briefing
    assert "Generate P0/P1 plan." in briefing
    assert "## Compiled planning execution facts" in briefing
    assert '"effective_policy_digest"' in briefing
    assert '"plan_approval_required"' in briefing
    assert "refactor_plan_md" in briefing
    assert "task_map" in briefing
    assert "gates" in briefing
    assert "scan_quality_audit_ref" in briefing
    assert "refactor_contract" in briefing
    assert "assembly_policy" in briefing
    assert "declared_task" in briefing
    assert "ASM-001" in briefing
    assert "do not emit success unless" in briefing
    assert "report.recommendation` as `approve`" in briefing
    assert "zf emit workflow.child.completed" in briefing
    assert "Aggregate success event: `zaofu.refactor.plan.ready`" in briefing
