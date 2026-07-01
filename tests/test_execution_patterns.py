from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutChildConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.execution_patterns import (
    project_execution_patterns,
    resolve_execution_pattern,
)


def _config() -> ZfConfig:
    return ZfConfig(
        roles=[
            RoleConfig(name="review-security", role_kind="reader"),
            RoleConfig(name="review-arch", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-wave",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-security", "review-arch"],
                target_ref="candidate/${task_id}",
                children=[
                    FanoutChildConfig(
                        role_instance="review-security",
                        scope="security",
                        payload={"expected_output": "security report"},
                    ),
                    FanoutChildConfig(
                        role_instance="review-arch",
                        scope="architecture",
                        payload={"expected_output": "architecture report"},
                    ),
                ],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="review.approved",
                    failure_event="review.rejected",
                ),
            ),
        ]),
    )


def test_execution_pattern_projection_from_workflow_stages(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")

    projection = project_execution_patterns(_config(), state_dir=state_dir)

    assert projection["schema_version"] == "execution-patterns.v1"
    assert projection["counts"]["patterns"] == 1
    pattern = projection["patterns"][0]
    assert pattern["pattern_id"] == "review-wave"
    assert pattern["kind"] == "fanout_reader"
    assert pattern["source"]["path"] == "workflow.stages"
    assert pattern["barrier"]["mode"] == "wait_for_all"
    assert pattern["barrier"]["required_children"] == [
        "review-security",
        "review-arch",
    ]
    assert pattern["children"][0]["expected_output"] == "security report"


def test_execution_pattern_runs_are_derived_from_fanout_events(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    events = [
        ZfEvent(
            type="fanout.started",
            task_id="TASK-1",
            payload={
                "fanout_id": "fanout-review-wave-1",
                "stage_id": "review-wave",
                "topology": "fanout_reader",
            },
        ),
        ZfEvent(
            type="fanout.child.dispatched",
            task_id="TASK-1",
            payload={
                "fanout_id": "fanout-review-wave-1",
                "child_id": "review-security",
            },
        ),
    ]

    projection = project_execution_patterns(_config(), state_dir=state_dir, events=events)

    assert projection["counts"]["active_runs"] == 1
    assert projection["runs"][0]["pattern_id"] == "review-wave"
    assert projection["runs"][0]["children"] == ["review-security"]


def test_resolve_execution_pattern_by_id() -> None:
    pattern = resolve_execution_pattern(_config(), "review-wave")

    assert pattern is not None
    assert pattern.kind == "fanout_reader"
