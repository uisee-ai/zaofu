from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import (
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_shadow_diff import compare_static_gate_shadow_events


ROOT = Path(__file__).resolve().parents[1]


def _cfg(
    *,
    graph_static_gate_action: bool,
    static_enabled: bool = True,
    required_checks: list[str] | None = None,
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="p7-shadow-diff"),
        session=SessionConfig(tmux_session="p7-shadow-diff"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done"],
            ),
            RoleConfig(
                name="review",
                backend="mock",
                role_kind="reader",
                triggers=["static_gate.passed"],
                publishes=["review.approved"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_static_gate_action=graph_static_gate_action,
            ),
        ),
        quality_gates={
            "static": QualityGateConfig(
                enabled=static_enabled,
                required_checks=required_checks or [],
            ),
        },
    )


def _make_orchestrator(
    root: Path,
    cfg: ZfConfig,
    *,
    state_dir_name: str,
) -> Orchestrator:
    state_dir = root / state_dir_name
    state_dir.mkdir(parents=True)
    (state_dir / "events.jsonl").touch()
    (state_dir / "session.yaml").write_text("runtime_state: running\n")
    transport = TmuxTransport(TmuxSession(session_name="p7-shadow-diff", dry_run=True))
    return Orchestrator(state_dir, cfg, transport)


def _run_static_gate(
    root: Path,
    *,
    state_dir_name: str,
    graph_static_gate_action: bool,
    static_enabled: bool,
    required_checks: list[str],
    marker_exists: bool,
    task_override: dict | None,
    dispatch: ZfEvent,
    trigger: ZfEvent,
) -> ZfEvent:
    root.mkdir(parents=True, exist_ok=True)
    if marker_exists:
        (root / "marker.txt").write_text("ok\n", encoding="utf-8")
    orch = _make_orchestrator(
        root,
        _cfg(
            graph_static_gate_action=graph_static_gate_action,
            static_enabled=static_enabled,
            required_checks=required_checks,
        ),
        state_dir_name=state_dir_name,
    )
    orch.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="in_progress",
        active_dispatch_id="d1",
        contract=TaskContract(
            quality_gates_override=task_override or {},
        ),
    ))
    orch.event_writer.append(dispatch)
    orch.event_writer.append(trigger)

    orch._maybe_run_static_gate(trigger)

    gate_events = [
        event for event in orch.event_log.read_all()
        if event.type.startswith("static_gate.")
    ]
    assert len(gate_events) == 1
    return gate_events[0]


@pytest.mark.parametrize(
    (
        "case_name",
        "static_enabled",
        "required_checks",
        "marker_exists",
        "task_override",
        "expected_type",
    ),
    [
        (
            "passed",
            True,
            ["test -f marker.txt"],
            True,
            None,
            "static_gate.passed",
        ),
        (
            "failed",
            True,
            ["test -f marker.txt"],
            False,
            None,
            "static_gate.failed",
        ),
        (
            "skipped_by_static_disabled",
            False,
            ["test -f marker.txt"],
            False,
            None,
            "static_gate.skipped",
        ),
        (
            "skipped_by_task_override",
            True,
            ["test -f marker.txt"],
            False,
            {"static": {"enabled": False}},
            "static_gate.skipped",
        ),
    ],
)
def test_static_gate_graph_action_shadow_diff_matches_legacy(
    tmp_path: Path,
    case_name: str,
    static_enabled: bool,
    required_checks: list[str],
    marker_exists: bool,
    task_override: dict | None,
    expected_type: str,
) -> None:
    dispatch = ZfEvent(
        type="task.dispatched",
        task_id="TASK-1",
        payload={"dispatch_id": "d1"},
        correlation_id=f"corr-{case_name}",
    )
    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"dispatch_id": "d1"},
        correlation_id=f"corr-{case_name}",
    )

    legacy_event = _run_static_gate(
        tmp_path / case_name,
        state_dir_name=".zf-legacy",
        graph_static_gate_action=False,
        static_enabled=static_enabled,
        required_checks=required_checks,
        marker_exists=marker_exists,
        task_override=task_override,
        dispatch=dispatch,
        trigger=trigger,
    )
    graph_event = _run_static_gate(
        tmp_path / case_name,
        state_dir_name=".zf-graph",
        graph_static_gate_action=True,
        static_enabled=static_enabled,
        required_checks=required_checks,
        marker_exists=marker_exists,
        task_override=task_override,
        dispatch=dispatch,
        trigger=trigger,
    )

    assert legacy_event.type == expected_type
    assert graph_event.type == expected_type
    assert legacy_event.actor == "zf-cli"
    assert graph_event.actor == "workflow_graph"
    diff = compare_static_gate_shadow_events(
        expected=legacy_event,
        actual=graph_event,
    )
    assert diff.matches, diff.to_dict()


def test_current_zf_yaml_uses_controller_static_gate_contract() -> None:
    cfg = load_config(ROOT / "zf.yaml")

    assert cfg.workflow.dag.schema_profile == "canonical-dag/v8"
    assert cfg.workflow.dag.graph_static_gate_action is False
    assert cfg.workflow.dag.graph_review_test_judge_reconcile is False
    assert cfg.quality_gates["static"].enabled is True
