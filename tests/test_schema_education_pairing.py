"""FIX-14(bizsim r4 F14):non_empty 合约档位 + briefing 样例与 schema 配对。

r4 实锚:全轮 9 份 verify report 的 requirement_coverage_matrix 全 0 行——
required 只保证键在;briefing 样例不带矩阵字段,agent 全靠 422 试错。
"""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.verification.event_schema import EventSchemaRegistry


def _registry() -> EventSchemaRegistry:
    return EventSchemaRegistry.from_dict({
        "verify.child.completed": {
            "required": ["fanout_id", "report"],
            "nested": {
                "report": {
                    "required": [
                        "summary", "recommendation",
                        "requirement_coverage_matrix", "evidence_refs",
                    ],
                    "non_empty": [
                        "requirement_coverage_matrix", "evidence_refs",
                    ],
                },
            },
        },
    })


def _event(report: dict) -> ZfEvent:
    return ZfEvent(
        type="verify.child.completed",
        actor="verify",
        payload={"fanout_id": "f-1", "report": report},
    )


def test_non_empty_rejects_empty_matrix() -> None:
    violations = _registry().validate(_event({
        "summary": "ok", "recommendation": "approve",
        "requirement_coverage_matrix": [],
        "evidence_refs": ["log.txt"],
    }))
    codes = {(v.field_path, v.code) for v in violations}
    assert (
        "payload.report.requirement_coverage_matrix", "empty_required",
    ) in codes


def test_non_empty_passes_with_rows() -> None:
    violations = _registry().validate(_event({
        "summary": "ok", "recommendation": "approve",
        "requirement_coverage_matrix": [{"requirement_id": "R-1"}],
        "evidence_refs": ["log.txt"],
    }))
    assert not violations


def test_briefing_sample_carries_schema_required_report_fields(tmp_path) -> None:
    """schema 要求的 report 字段必须出现在 briefing 的 emit 样例里。"""
    from zf.core.config.schema import (
        FanoutAggregateConfig, ProjectConfig, RoleConfig, WorkflowConfig,
        WorkflowStageConfig, ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.runtime.orchestrator import Orchestrator

    class _Transport:
        def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
            pass

        def is_alive(self, role_name):  # noqa: ANN001
            return True

        def capture_log(self, role_name, lines=200):  # noqa: ANN001
            return ""

        def poll_events(self):
            return []

    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="verify", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="verify-stage",
                trigger="impl.done",
                topology="fanout_reader",
                roles=["verify"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="verify.passed",
                    failure_event="verify.failed",
                    child_success_event="verify.child.completed",
                    child_failure_event="verify.child.failed",
                ),
            ),
        ]),
    )
    config.workflow.dag.event_schemas = {
        "verify.child.completed": {
            "required": ["fanout_id", "report"],
            "nested": {
                "report": {
                    "required": [
                        "summary", "recommendation",
                        "requirement_coverage_matrix", "evidence_refs",
                    ],
                    "non_empty": ["requirement_coverage_matrix"],
                },
            },
        },
    }
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, config, _Transport())  # type: ignore[arg-type]

    trigger = ZfEvent(
        type="impl.done", actor="zf-cli", correlation_id="t-1",
        payload={"status": "completed"},
    )
    log.append(trigger)
    orch.run_once(events=[trigger])

    briefings = list((state_dir / "briefings").glob("verify-*.md"))
    assert briefings, "verify child 必须已派发并生成 briefing"
    text = briefings[0].read_text(encoding="utf-8")
    assert "requirement_coverage_matrix" in text
    assert "acceptance-id-from-task-contract" in text, "矩阵样例行必须给出"
    assert "evidence_refs" in text
