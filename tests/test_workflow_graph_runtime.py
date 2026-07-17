from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from zf.core.config.loader import load_config, validate_config
from zf.core.config.schema import (
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.workflow.graph import compile_workflow_graph
from zf.core.workflow.topology import WorkflowEventSets
from zf.runtime.stage_actions import StageActionContext, StageActionRunner
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.workflow_conditions import (
    WorkflowConditionEvaluator,
    WorkflowEvaluationContext,
)
from zf.runtime.workflow_node_projection import build_workflow_node_projection
from zf.runtime.workflow_reconciler import WorkflowGraphReconciler
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.web.server import create_app
from zf.web.projections.workflow_graph import (
    _workflow_judge_configured,
    _workflow_terminal_success_event,
)


ROOT = Path(__file__).resolve().parents[1]


def test_controller_profiles_project_thin_judge_and_goal_terminal() -> None:
    for name in (
        "issue-fanout-v3.yaml",
        "prd-fanout-v3.yaml",
        "refactor-lane-v3.yaml",
    ):
        config = load_config(ROOT / "examples" / "prod" / "controller" / name)

        assert _workflow_judge_configured(config) is True, name
        assert _workflow_terminal_success_event(config) == "run.goal.completed", name


def test_all_examples_compile_to_workflow_graph() -> None:
    for path in sorted((ROOT / "examples").glob("*.yaml")):
        cfg = load_config(path)
        graph = compile_workflow_graph(cfg)

        assert graph.nodes, path.name
        assert graph.event_sets.stage_progress_events, path.name


def test_standard_codex_workflows_validate() -> None:
    for name in (
        "workflow-product-fanout-standard-codex.yaml",
        "workflow-product-standard-codex.yaml",
        "workflow-refactor-standard-codex.yaml",
    ):
        assert validate_config(ROOT / "examples" / name) == []


def test_graph_compiles_fanout_static_gate_and_derived_events() -> None:
    cfg = load_config(ROOT / "examples" / "hermes-codex.yaml")
    graph = compile_workflow_graph(cfg)

    assert graph.nodes_by_type("fanout_stage")
    assert graph.nodes_by_type("aggregate_stage")
    assert "zaofu.refactor.plan.ready" in graph.event_sets.handoff_success_events
    assert "integration.failed" in graph.event_sets.rework_trigger_events

    cfg2 = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph2 = compile_workflow_graph(cfg2)
    assert graph2.node("gate:impl_exit_gate") is not None
    assert "static_gate.passed" in graph2.event_sets.handoff_success_events
    role_edges = {(edge.from_node, edge.to_node, edge.event) for edge in graph2.edges}
    assert ("gate:impl_exit_gate", "role:review", "static_gate.passed") in role_edges
    assert any(
        edge.event == "review.approved"
        and edge.from_node.startswith("role:review")
        and edge.to_node.startswith("role:test")
        for edge in graph2.edges
    )
    assert any(
        edge.event == "test.passed"
        and edge.from_node.startswith("role:test")
        and edge.to_node.startswith("role:judge")
        for edge in graph2.edges
    )
    assert graph2.node("rework:static_gate.failed") is not None
    assert any(
        edge.event == "static_gate.skipped"
        and edge.from_node == "gate:impl_exit_gate"
        and edge.to_node.startswith("role:review")
        and edge.condition == "skipped_as_fulfilled"
        for edge in graph2.edges
    )

    baseline = WorkflowEventSets.baseline()
    assert baseline.handoff_success_events <= graph2.event_sets.handoff_success_events
    assert baseline.rework_trigger_events <= graph2.event_sets.rework_trigger_events


def test_graph_diagnostics_cover_invalid_routes_and_fanout_aggregate_gaps() -> None:
    cfg = load_config(ROOT / "examples" / "hermes-codex.yaml")
    cfg.workflow.rework_routing["integration.failed"] = "missing-role"
    cfg.workflow.stages[0].aggregate.success_event = ""
    cfg.workflow.stages[0].aggregate.failure_event = ""

    graph = compile_workflow_graph(cfg)
    diagnostics = graph.diagnostics
    kinds = {item["kind"] for item in diagnostics}

    assert "invalid_rework_target" in kinds
    assert "missing_aggregate_success_event" in kinds
    assert "missing_aggregate_failure_event" in kinds


def test_condition_evaluator_blocks_stale_dispatch() -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    node = graph.node("gate:impl_exit_gate")
    assert node is not None
    task = Task(id="TASK-1", title="demo", status="in_progress")
    events = [
        ZfEvent(
            type="task.dispatched",
            task_id="TASK-1",
            payload={"dispatch_id": "newer"},
        ),
        ZfEvent(
            type="dev.build.done",
            task_id="TASK-1",
            payload={"dispatch_id": "older"},
        ),
    ]

    evaluation = WorkflowConditionEvaluator().evaluate_node(
        node,
        WorkflowEvaluationContext(
            events=events,
            task=task,
            trigger_event=events[-1],
        ),
    )

    assert evaluation.ready is False
    failed = {condition.type for condition in evaluation.conditions if not condition.passed}
    assert "latest_dispatch_matches" in failed
    latest = next(condition for condition in evaluation.conditions if condition.type == "latest_dispatch_matches")
    assert "older" in latest.reason
    assert "newer" in latest.reason


def test_condition_evaluator_explains_missing_gate_and_terminal_evidence_without_writes(tmp_path: Path) -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    review_node = graph.node("role:review")
    terminal_node = graph.node("terminal:done")
    assert review_node is not None
    assert terminal_node is not None
    event_log = EventLog(tmp_path / "events.jsonl")
    task_store = TaskStore(tmp_path / "kanban.json")
    task_store.add(Task(id="TASK-1", title="demo", status="review"))
    early_review = ZfEvent(type="review.approved", task_id="TASK-1")
    event_log.append(early_review)
    before_events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    before_kanban = (tmp_path / "kanban.json").read_text(encoding="utf-8")
    events = event_log.read_all()
    task = task_store.get("TASK-1")
    assert task is not None

    review_eval = WorkflowConditionEvaluator().evaluate_node(
        review_node,
        WorkflowEvaluationContext(events=events, task=task),
        conditions=("event_seen",),
    )
    terminal_eval = WorkflowConditionEvaluator().evaluate_node(
        terminal_node,
        WorkflowEvaluationContext(events=events, task=task),
    )

    assert review_eval.ready is False
    assert "missing upstream event" in review_eval.conditions[0].reason
    missing_evidence = [
        condition for condition in terminal_eval.conditions
        if condition.type == "evidence_present"
    ][0]
    assert missing_evidence.passed is False
    assert "missing evidence event" in missing_evidence.reason
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8") == before_events
    assert (tmp_path / "kanban.json").read_text(encoding="utf-8") == before_kanban


def test_condition_evaluator_shadow_match_reports_legacy_comparison() -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    node = graph.node("gate:impl_exit_gate")
    assert node is not None
    task = Task(id="TASK-1", title="demo", status="in_progress")
    trigger = ZfEvent(type="dev.build.done", task_id="TASK-1")

    evaluation = WorkflowConditionEvaluator().evaluate_node(
        node,
        WorkflowEvaluationContext(
            events=[trigger],
            task=task,
            trigger_event=trigger,
            shadow_expected_ready=True,
        ),
    )

    assert evaluation.ready is True
    assert evaluation.shadow_matches is True


def test_workflow_node_projection_distinguishes_static_gate_skipped() -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    task = Task(id="TASK-1", title="demo", status="in_progress")
    events = [
        ZfEvent(type="dev.build.done", task_id="TASK-1"),
        ZfEvent(type="static_gate.skipped", task_id="TASK-1", payload={"passed": True, "skipped": True}),
    ]

    projection = build_workflow_node_projection(
        graph=graph,
        events=events,
        tasks=[task],
    )
    runs = {
        run["node_id"]: run
        for run in projection["runs"]
    }

    assert runs["gate:impl_exit_gate"]["phase"] == "skipped"
    assert runs["gate:impl_exit_gate"]["phase"] != "succeeded"


def test_workflow_node_projection_contains_blocking_reasons_source_ids_and_action_decisions() -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    task = Task(id="TASK-1", title="demo", status="review")
    early_review = ZfEvent(type="review.approved", task_id="TASK-1")
    gate_action = ZfEvent(
        type="static_gate.skipped",
        task_id="TASK-1",
        payload={
            "stage_id": "impl_exit_gate",
            "action_type": "run_gate",
            "decision": "committed",
            "trigger_event_id": "evt-dev",
        },
    )

    projection = build_workflow_node_projection(
        graph=graph,
        events=[early_review, gate_action],
        tasks=[task],
    )
    runs = {run["node_id"]: run for run in projection["runs"]}

    assert "missing upstream event" in "; ".join(runs["role:review"]["blocking_reasons"])
    test_run = next(run for key, run in runs.items() if key.startswith("role:test"))
    assert early_review.id in test_run["source_event_ids"]
    assert runs["gate:impl_exit_gate"]["action_decisions"][0]["action_type"] == "run_gate"


def test_stage_action_runner_plan_is_pure_and_commit_uses_kernel_helpers(tmp_path: Path) -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    node = graph.node("terminal:done")
    assert node is not None
    event_log = EventLog(tmp_path / "events.jsonl")
    task_store = TaskStore(tmp_path / "kanban.json")
    task_store.add(Task(id="TASK-1", title="demo", status="in_progress"))
    runner = StageActionRunner()

    plan = runner.plan(node=node, action_type="complete_task", task_id="TASK-1")

    assert task_store.get("TASK-1").status == "in_progress"
    result = runner.commit(
        plan,
        StageActionContext(
            event_writer=EventWriter(event_log),
            task_store=task_store,
        ),
    )

    assert result.decision == "committed"
    assert task_store.get("TASK-1").status == "done"
    deduped = runner.commit(
        plan,
        StageActionContext(
            event_writer=EventWriter(event_log),
            task_store=task_store,
        ),
    )
    assert deduped.decision == "deduped"


def test_stage_action_runner_static_gate_and_rework_are_replay_idempotent(tmp_path: Path) -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    gate = graph.node("gate:impl_exit_gate")
    rework = graph.node("rework:static_gate.failed")
    assert gate is not None
    assert rework is not None
    event_log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(event_log)
    source = writer.append(ZfEvent(type="dev.build.done", task_id="TASK-1"))
    gate_plan = StageActionRunner().plan(
        node=gate,
        action_type="run_gate",
        task_id="TASK-1",
        source_events=[source],
    )

    result = StageActionRunner().commit(
        gate_plan,
        StageActionContext(
            event_writer=writer,
            source_event=source,
            config=cfg,
            project_root=str(tmp_path),
        ),
    )
    replay = StageActionRunner().commit(
        gate_plan,
        StageActionContext(
            event_writer=writer,
            source_event=source,
            config=cfg,
            project_root=str(tmp_path),
        ),
    )
    assert result.decision == "committed"
    assert replay.decision == "deduped"

    failed = writer.append(ZfEvent(type="static_gate.failed", task_id="TASK-1"))
    rework_plan = StageActionRunner().plan(
        node=rework,
        action_type="route_rework",
        task_id="TASK-1",
        source_events=[failed],
        payload={"target_role": "dev"},
    )
    rework_result = StageActionRunner().commit(
        rework_plan,
        StageActionContext(event_writer=writer, source_event=failed),
    )
    rework_replay = StageActionRunner().commit(
        rework_plan,
        StageActionContext(event_writer=writer, source_event=failed),
    )
    assert rework_result.decision == "committed"
    assert rework_replay.decision == "deduped"


def test_stage_action_runner_unknown_action_fails_closed() -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    node = compile_workflow_graph(cfg).node("terminal:done")
    assert node is not None

    plan = StageActionRunner().plan(node=node, action_type="unknown_action")

    assert plan.decision == "blocked"
    assert "unsupported stage action" in plan.reason


def test_workflow_reconciler_resync_reports_ready_nodes() -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    task = Task(id="TASK-1", title="demo", status="in_progress")
    events = [ZfEvent(type="dev.build.done", task_id="TASK-1")]

    decisions = WorkflowGraphReconciler().resync(
        graph=graph,
        events=events,
        tasks=[task],
    )

    assert "TASK-1" in decisions
    assert any(item["node_id"] == "gate:impl_exit_gate" for item in decisions["TASK-1"])


def test_workflow_reconciler_does_not_replan_gate_from_gate_terminal_event() -> None:
    cfg = ZfConfig(
        project=ProjectConfig(name="graph-gate-terminal"),
        session=SessionConfig(tmux_session="graph-gate-terminal"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_static_gate_action=True,
            ),
        ),
        quality_gates={
            "static": QualityGateConfig(enabled=True, required_checks=["true"]),
        },
    )
    graph = compile_workflow_graph(cfg)
    task = Task(id="TASK-1", title="demo", status="in_progress")
    gate = ZfEvent(
        type="static_gate.passed",
        task_id="TASK-1",
        payload={"trigger_event_id": "evt-dev"},
        causation_id="evt-dev",
    )

    decisions = WorkflowGraphReconciler().plan(
        graph=graph,
        events=[gate],
        task=task,
        trigger_event=gate,
    )

    assert decisions == []


def test_workflow_reconciler_plans_review_dispatch_terminal_and_rework_routes() -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    graph = compile_workflow_graph(cfg)
    task = Task(id="TASK-1", title="demo", status="testing")
    approved = ZfEvent(type="review.approved", task_id="TASK-1")
    judge_passed = ZfEvent(type="judge.passed", task_id="TASK-1")
    judge_failed = ZfEvent(type="judge.failed", task_id="TASK-1")
    reconciler = WorkflowGraphReconciler()

    review_decisions = reconciler.plan(
        graph=graph,
        events=[approved],
        task=task,
        trigger_event=approved,
    )
    terminal_decisions = reconciler.plan(
        graph=graph,
        events=[judge_passed],
        task=task,
        trigger_event=judge_passed,
    )
    rework_decisions = reconciler.plan(
        graph=graph,
        events=[judge_failed],
        task=task,
        trigger_event=judge_failed,
    )

    assert any(
        decision.node_id.startswith("role:test")
        and decision.action_plan is not None
        and decision.action_plan.action_type == "dispatch_role"
        for decision in review_decisions
    )
    assert any(
        decision.node_id == "terminal:done"
        and decision.action_plan is not None
        and decision.action_plan.action_type == "complete_task"
        for decision in terminal_decisions
    )
    assert any(
        decision.node_id == "rework:judge.failed"
        and decision.action_plan is not None
        and decision.action_plan.action_type == "route_rework"
        for decision in rework_decisions
    )


def test_custom_verify_terminal_compiles_without_python_handler() -> None:
    cfg = ZfConfig()
    cfg.roles = [
        RoleConfig(
            name="dev",
            triggers=["task.assigned"],
            publishes=["dev.build.done"],
        ),
        RoleConfig(
            name="verify",
            triggers=["dev.build.done"],
            publishes=["verify.passed", "verify.failed"],
        ),
    ]
    cfg.workflow.rework_routing = {"verify.failed": "dev"}
    graph = compile_workflow_graph(cfg)
    assert graph.terminal_policy.success_events == frozenset({"verify.passed"})
    passed = ZfEvent(type="verify.passed", task_id="TASK-1")
    failed = ZfEvent(type="verify.failed", task_id="TASK-1")
    task = Task(id="TASK-1", title="demo", status="in_progress")

    terminal_decisions = WorkflowGraphReconciler().plan(
        graph=graph,
        events=[passed],
        task=task,
        trigger_event=passed,
    )
    rework_decisions = WorkflowGraphReconciler().plan(
        graph=graph,
        events=[failed],
        task=task,
        trigger_event=failed,
    )

    assert any(
        decision.node_id == "terminal:done"
        and decision.action_plan is not None
        and decision.action_plan.action_type == "complete_task"
        for decision in terminal_decisions
    )
    assert any(
        decision.node_id == "rework:verify.failed"
        and decision.action_plan is not None
        and decision.action_plan.payload["target_role"] == "dev"
        for decision in rework_decisions
    )


class _ShadowReactor(EventReactorMixin):
    def __init__(self, *, cfg: ZfConfig, state_dir: Path) -> None:
        self.config = cfg
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.task_store = TaskStore(state_dir / "kanban.json")
        self.project_root = state_dir
        self._event_writer = EventWriter(self.event_log)


class _GraphBridgeReactor(_ShadowReactor):
    def _move_task(
        self,
        task_id: str,
        to_status: str,
        *,
        trigger_event: str = "",
    ) -> bool:
        task = self.task_store.get(task_id)
        if task is None:
            return False
        self.task_store.update(task_id, status=to_status)
        self.event_writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "from": task.status,
                "to": to_status,
                "source": "test_graph_bridge",
                "trigger_event": trigger_event,
            },
        ))
        return True

    def _route_rework_trigger(
        self,
        task: Task,
        trigger_event: ZfEvent,
        *,
        reason: str,
    ) -> OrchestratorDecision:
        self.task_store.update(task.id, status="in_progress", assigned_to="dev")
        self.event_writer.append(ZfEvent(
            type="task.rework.requested",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "role": "dev",
                "assignee": "dev",
                "reason": reason,
                "trigger_event_id": trigger_event.id,
                "trigger_event_type": trigger_event.type,
            },
            causation_id=trigger_event.id,
            correlation_id=trigger_event.correlation_id,
        ))
        return OrchestratorDecision(
            action="dispatch",
            task_id=task.id,
            role="dev",
            reason=reason,
        )

    def _evaluate_terminal_done(self, event: ZfEvent, task: Task) -> bool:
        return True

    def _record_terminal_accepted(self, event: ZfEvent, task: Task) -> None:
        self.event_writer.append(ZfEvent(
            type="task.done.evidence",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "trigger_event": event.type,
                "trigger_event_id": event.id,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))

    def _clear_evidence_reissue(self, task_id: str) -> None:
        return None

    def _settle_task_chain_workers_idle(
        self,
        task_id: str,
        *,
        fallback_assignee: str = "",
        reason: str,
    ) -> None:
        return None

    def _emit_spec_promote_decision(self, event: ZfEvent, task: Task) -> None:
        return None


def _static_gate_graph_action_config(*, enabled: bool = True) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="graph-static-gate"),
        session=SessionConfig(tmux_session="graph-static-gate"),
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
                graph_static_gate_action=enabled,
            ),
        ),
        quality_gates={
            "static": QualityGateConfig(
                enabled=True,
                required_checks=["test -f marker.txt"],
            ),
        },
    )


def _graph_review_test_judge_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="graph-review-test-judge"),
        session=SessionConfig(tmux_session="graph-review-test-judge"),
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
                publishes=["review.approved", "review.rejected"],
            ),
            RoleConfig(
                name="test",
                backend="mock",
                role_kind="reader",
                triggers=["review.approved"],
                publishes=["test.passed", "test.failed"],
            ),
            RoleConfig(
                name="judge",
                backend="mock",
                role_kind="reader",
                triggers=["test.passed"],
                publishes=["judge.passed", "judge.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
            rework_routing={
                "review.rejected": "dev",
                "test.failed": "dev",
                "judge.failed": "dev",
            },
        ),
    )


def test_reactor_registers_workflow_graph_shadow_handler(tmp_path: Path) -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    reactor = _ShadowReactor(cfg=cfg, state_dir=tmp_path)
    reactor.task_store.add(Task(id="TASK-1", title="demo", status="in_progress"))
    registry = reactor._build_event_registry()
    event = ZfEvent(type="dev.build.done", task_id="TASK-1")
    reactor.event_log.append(event)

    shadow = [
        entry for entry in registry.resolve("dev.build.done")
        if entry.source == "workflow_graph_shadow"
    ][0]
    shadow.handler(event)

    assert event.id in reactor._workflow_graph_shadow_last
    assert any(
        item["node_id"] == "gate:impl_exit_gate"
        for item in reactor._workflow_graph_shadow_last[event.id]
    )


def test_workflow_graph_static_gate_action_commits_when_enabled(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("ok\n")
    reactor = _ShadowReactor(
        cfg=_static_gate_graph_action_config(enabled=True),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(id="TASK-1", title="demo", status="in_progress"))
    reactor.event_writer.append(ZfEvent(
        type="task.dispatched",
        task_id="TASK-1",
        payload={"dispatch_id": "d1"},
    ))
    trigger = reactor.event_writer.append(ZfEvent(
        type="dev.build.done",
        task_id="TASK-1",
        payload={"dispatch_id": "d1"},
    ))

    handled = reactor._run_workflow_graph_static_gate(trigger)

    assert handled is True
    gate_events = [
        event for event in reactor.event_log.read_all()
        if event.type.startswith("static_gate.")
    ]
    assert len(gate_events) == 1
    assert gate_events[0].type == "static_gate.passed"
    assert gate_events[0].actor == "workflow_graph"
    assert gate_events[0].payload["stage_id"] == "impl_exit_gate"
    assert gate_events[0].payload["workdir"] == str(tmp_path)
    assert trigger.id in reactor._workflow_graph_action_last
    signature = reactor._workflow_graph_action_last[trigger.id][0][
        "static_gate_signature"
    ]
    assert signature["type"] == "static_gate.passed"
    assert signature["payload"]["workdir"] == str(tmp_path)


def test_workflow_graph_static_gate_action_blocks_stale_dispatch(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("ok\n")
    reactor = _ShadowReactor(
        cfg=_static_gate_graph_action_config(enabled=True),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="in_progress",
        active_dispatch_id="newer",
    ))
    reactor.event_writer.append(ZfEvent(
        type="task.dispatched",
        task_id="TASK-1",
        payload={"dispatch_id": "newer"},
    ))
    trigger = reactor.event_writer.append(ZfEvent(
        type="dev.build.done",
        task_id="TASK-1",
        payload={"dispatch_id": "older"},
    ))

    handled = reactor._run_workflow_graph_static_gate(trigger)

    assert handled is True
    assert [
        event.type for event in reactor.event_log.read_all()
        if event.type.startswith("static_gate.")
    ] == []
    latest = reactor._workflow_graph_action_last[trigger.id][0]["decision"]
    assert latest["ready"] is False
    assert "older" in latest["reason"]
    assert "newer" in latest["reason"]


def test_workflow_graph_static_gate_action_flag_can_fall_back_to_legacy(tmp_path: Path) -> None:
    reactor = _ShadowReactor(
        cfg=_static_gate_graph_action_config(enabled=False),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(id="TASK-1", title="demo", status="in_progress"))
    trigger = ZfEvent(type="dev.build.done", task_id="TASK-1")

    assert reactor._run_workflow_graph_static_gate(trigger) is False


def test_workflow_graph_bridge_dispatches_review_to_test(tmp_path: Path) -> None:
    reactor = _GraphBridgeReactor(
        cfg=_graph_review_test_judge_config(),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="review",
        assigned_to="review",
    ))
    event = reactor.event_writer.append(ZfEvent(
        type="review.approved",
        task_id="TASK-1",
    ))

    decision = reactor._on_review_approved(event)

    task = reactor.task_store.get("TASK-1")
    assert decision is not None
    assert decision.action == "assign"
    assert decision.role == "test"
    assert task is not None
    assert task.status == "testing"
    assert task.assigned_to == "test"
    assigned = [
        item for item in reactor.event_log.read_all()
        if item.type == "task.assigned"
    ]
    assert len(assigned) == 1
    assert assigned[0].payload["source"] == "workflow_graph_event_handler"
    assert assigned[0].payload["trigger_event_id"] == event.id


def test_workflow_graph_bridge_dispatches_test_to_judge(tmp_path: Path) -> None:
    reactor = _GraphBridgeReactor(
        cfg=_graph_review_test_judge_config(),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="testing",
        assigned_to="test",
    ))
    event = reactor.event_writer.append(ZfEvent(
        type="test.passed",
        task_id="TASK-1",
    ))

    decision = reactor._on_test_passed(event)

    task = reactor.task_store.get("TASK-1")
    assert decision is not None
    assert decision.action == "assign"
    assert decision.role == "judge"
    assert task is not None
    assert task.status == "testing"
    assert task.assigned_to == "judge"


def test_workflow_graph_resync_commits_static_gate_action(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("ok\n")
    cfg = _static_gate_graph_action_config(enabled=False)
    cfg.workflow.dag.graph_review_test_judge_reconcile = True
    reactor = _GraphBridgeReactor(cfg=cfg, state_dir=tmp_path)
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="in_progress",
        active_dispatch_id="d1",
    ))
    reactor.event_writer.append(ZfEvent(
        type="task.dispatched",
        task_id="TASK-1",
        payload={"dispatch_id": "d1"},
    ))
    trigger = reactor.event_writer.append(ZfEvent(
        type="dev.build.done",
        task_id="TASK-1",
        payload={"dispatch_id": "d1"},
    ))

    decision = reactor._workflow_graph_reconcile_bridge(trigger, source="resync")

    assert decision is not None
    assert decision.action == "gate"
    gate_events = [
        event for event in reactor.event_log.read_all()
        if event.type == "static_gate.passed"
    ]
    assert len(gate_events) == 1
    assert gate_events[0].payload["trigger_event_id"] == trigger.id
    assert reactor._workflow_graph_reconcile_bridge(trigger, source="resync") is None


def test_workflow_graph_bridge_completes_judge_terminal(tmp_path: Path) -> None:
    reactor = _GraphBridgeReactor(
        cfg=_graph_review_test_judge_config(),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="testing",
        assigned_to="judge",
    ))
    event = reactor.event_writer.append(ZfEvent(
        type="judge.passed",
        task_id="TASK-1",
    ))

    decision = reactor._on_judge_passed(event)

    assert decision is not None
    assert decision.action == "move"
    assert reactor.task_store.get("TASK-1").status == "done"
    done_evidence = [
        item for item in reactor.event_log.read_all()
        if item.type == "task.done.evidence"
    ]
    assert len(done_evidence) == 1
    assert done_evidence[0].payload["trigger_event_id"] == event.id


def test_workflow_graph_bridge_routes_judge_failed_rework(tmp_path: Path) -> None:
    reactor = _GraphBridgeReactor(
        cfg=_graph_review_test_judge_config(),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="testing",
        assigned_to="judge",
    ))
    event = reactor.event_writer.append(ZfEvent(
        type="judge.failed",
        task_id="TASK-1",
    ))

    decision = reactor._on_judge_failed(event)

    task = reactor.task_store.get("TASK-1")
    assert decision is not None
    assert decision.action == "dispatch"
    assert decision.role == "dev"
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "dev"
    rework = [
        item for item in reactor.event_log.read_all()
        if item.type == "task.rework.requested"
    ]
    assert len(rework) == 1
    assert rework[0].payload["trigger_event_id"] == event.id


def test_workflow_graph_bridge_replay_does_not_double_dispatch(tmp_path: Path) -> None:
    reactor = _GraphBridgeReactor(
        cfg=_graph_review_test_judge_config(),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="review",
        assigned_to="review",
    ))
    event = reactor.event_writer.append(ZfEvent(
        type="review.approved",
        task_id="TASK-1",
    ))

    first = reactor._on_review_approved(event)
    second = reactor._on_review_approved(event)

    assert first is not None and first.action == "assign"
    assert second is not None and second.action == "noop"
    assert len([
        item for item in reactor.event_log.read_all()
        if item.type == "task.assigned"
    ]) == 1


def test_workflow_graph_resync_recovers_missing_dispatch(tmp_path: Path) -> None:
    reactor = _GraphBridgeReactor(
        cfg=_graph_review_test_judge_config(),
        state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1",
        title="demo",
        status="review",
        assigned_to="review",
    ))
    event = reactor.event_writer.append(ZfEvent(
        type="review.approved",
        task_id="TASK-1",
    ))

    decisions = reactor._workflow_graph_resync_reconcile(
        reactor.event_log.read_all(),
    )

    task = reactor.task_store.get("TASK-1")
    assert len(decisions) == 1
    assert decisions[0].role == "test"
    assert task is not None
    assert task.assigned_to == "test"
    assert reactor._workflow_graph_reconcile_last[event.id][0]["node_id"] == "role:test"


def test_workflow_graph_web_projection_adds_compiled_graph(tmp_path: Path) -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    event_log = EventLog(tmp_path / "events.jsonl")
    task_store = TaskStore(tmp_path / "kanban.json")
    task_store.add(Task(id="TASK-1", title="demo", status="in_progress"))
    event_log.append(ZfEvent(type="dev.build.done", task_id="TASK-1"))
    app = create_app(state_dir=tmp_path, config=cfg)
    client = TestClient(app)

    data = client.get("/api/workflow/graph").json()

    assert data["compiled_graph"]["schema_version"] == "workflow-graph.v1"
    assert data["workflow_node_runs"]["schema_version"] == "workflow-node-run.v1"


def test_workflow_graph_web_projection_uses_read_model_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = load_config(ROOT / "examples" / "zf-codex.yaml")
    event_log = EventLog(tmp_path / "events.jsonl")
    TaskStore(tmp_path / "kanban.json").add(
        Task(id="TASK-1", title="demo", status="in_progress")
    )
    event_log.append(ZfEvent(type="dev.build.done", task_id="TASK-1"))

    def fail_read_all(self):  # noqa: ANN001
        raise AssertionError("workflow graph must not scan EventLog.read_all")

    monkeypatch.setattr("zf.core.events.log.EventLog.read_all", fail_read_all)
    client = TestClient(create_app(state_dir=tmp_path, config=cfg))

    first = client.get("/api/workflow/graph").json()
    event_log.append(ZfEvent(
        type="fanout.started",
        actor="orchestrator",
        payload={"fanout_id": "fo-cache"},
    ))
    second = client.get("/api/workflow/graph").json()

    assert first["projection"]["source"] == "read_model.sqlite"
    assert first["projection"]["source_seq"] == 1
    # Serve-stale-with-lag (RF-2/RF-5, F0-A fix): a small append is surfaced as
    # lag on the cached graph instead of an immediate multi-MB recompute per
    # request. The read model DID advance — projection_lag = current_seq(2) -
    # cached_seq(1) = 1 — so the graph serves the cached row marked stale and
    # refreshes in the background. The exact-seq immediacy asserted here before
    # was precisely the per-append recompute the cache intentionally replaced.
    assert second["projection"]["source_seq"] == 1
    assert second["projection"]["projection_lag"] == 1
    assert second["projection"]["stale"] is True
