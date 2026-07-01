from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.delivery_run_trace import build_delivery_run_projection
from zf.runtime.workflow_trace import build_workflow_trace

ROOT = Path(__file__).resolve().parents[1]


def _dynamic_config():
    return load_config(ROOT / "examples" / "workflow-task-flow-dynamic-codex.yaml")


def test_delivery_task_flow_uses_dynamic_yaml_stage_order() -> None:
    cfg = _dynamic_config()
    tasks = {
        "T-plan": Task(
            id="T-plan",
            title="plan task map",
            status="done",
            contract=TaskContract(feature_id="F-1", phase="plan", owner_role="planner"),
        ),
        "T-impl": Task(
            id="T-impl",
            title="implement auth",
            status="in_progress",
            contract=TaskContract(feature_id="F-1", phase="impl", owner_role="dev-core"),
        ),
        "T-verify": Task(
            id="T-verify",
            title="verify candidate",
            status="backlog",
            contract=TaskContract(feature_id="F-1", phase="verify", owner_role="verify-code"),
        ),
    }
    events = [
        ZfEvent(id="evt-task-map", type="task_map.ready", task_id="T-plan", payload={"feature_id": "F-1", "stage_id": "plan"}),
        ZfEvent(id="evt-fanout", type="fanout.started", payload={
            "feature_id": "F-1",
            "fanout_id": "FX",
            "stage_id": "impl",
            "expected_children": [{"child_id": "dev-core", "task_id": "T-impl"}],
        }),
        ZfEvent(id="evt-child", type="fanout.child.dispatched", payload={
            "feature_id": "F-1", "fanout_id": "FX", "stage_id": "impl", "child_id": "dev-core", "task_id": "T-impl",
        }),
    ]

    workflow = build_workflow_trace(config=cfg, events=events, tasks=tasks, feature_id="F-1")
    projection = build_delivery_run_projection(
        config=cfg,
        events=list(enumerate(events)),
        tasks=tasks,
        workflow_trace=workflow,
        execution_graph={"nodes": []},
    )

    task_flow = projection["task_flow"]
    assert task_flow["schema_version"] == "delivery-task-flow.v1"
    assert task_flow["stage_order"] == ["plan", "impl", "verify", "done"]
    assert [stage["stage_id"] for stage in task_flow["stages"]] == ["plan", "impl", "verify", "done"]
    impl = next(stage for stage in task_flow["stages"] if stage["stage_id"] == "impl")
    assert impl["status"] == "running"
    assert impl["tasks_running"] == 1
    assert impl["tasks"][0]["task_id"] == "T-impl"


def test_delivery_run_groups_and_spans_project_fanout_children() -> None:
    cfg = _dynamic_config()
    tasks = {
        "T-impl": Task(
            id="T-impl",
            title="implement auth",
            status="done",
            contract=TaskContract(feature_id="F-1", phase="impl", owner_role="dev-core"),
        ),
    }
    events = [
        ZfEvent(id="evt-fanout", type="fanout.started", payload={
            "feature_id": "F-1",
            "fanout_id": "FX",
            "stage_id": "impl",
            "expected_children": [{"child_id": "dev-core", "task_id": "T-impl"}],
        }),
        ZfEvent(id="evt-child-dispatched", type="fanout.child.dispatched", payload={
            "feature_id": "F-1", "fanout_id": "FX", "stage_id": "impl", "child_id": "dev-core", "task_id": "T-impl",
        }),
        ZfEvent(id="evt-child-done", type="fanout.child.completed", payload={
            "feature_id": "F-1",
            "fanout_id": "FX",
            "stage_id": "impl",
            "child_id": "dev-core",
            "task_id": "T-impl",
            "backend": "codex",
            "input_tokens": 11,
            "output_tokens": 7,
            "api_key": "sk-thisshouldberedacted1234567890",
        }),
        ZfEvent(id="evt-verify-fail", type="verify.failed", task_id="T-impl", payload={
            "feature_id": "F-1",
            "stage_id": "verify",
            "reason": "OPENAI_API_KEY=sk-thisshouldberedacted1234567890",
        }),
    ]

    workflow = build_workflow_trace(config=cfg, events=events, tasks=tasks, feature_id="F-1")
    projection = build_delivery_run_projection(
        config=cfg,
        events=list(enumerate(events)),
        tasks=tasks,
        workflow_trace=workflow,
        execution_graph={"nodes": []},
    )

    groups = projection["run_groups"]
    assert any(group["group_id"] == "FX" for group in groups)
    fx = next(group for group in groups if group["group_id"] == "FX")
    assert fx["children"][0]["child_id"] == "dev-core"
    assert fx["steps"][0]["event_type"] == "fanout.started"
    spans = projection["trace"]["spans"]
    assert any(span["span_id"] == "child:FX:dev-core" for span in spans)
    assert projection["trace"]["usage_summary"]["input_tokens"] == 11
    assert "sk-thisshouldberedacted" not in json.dumps(projection)
    assert "[REDACTED_SECRET]" in json.dumps(projection)


def test_autoresearch_trace_projects_ab_and_bugfix_graphs() -> None:
    cfg = _dynamic_config()
    tasks = {
        "T-bug": Task(
            id="T-bug",
            title="repair dispatch regression",
            status="in_progress",
            contract=TaskContract(feature_id="F-1", phase="impl", owner_role="dev-core"),
        ),
    }
    events = [
        ZfEvent(id="ar-trigger", type="autoresearch.trigger.accepted", task_id="T-bug", correlation_id="ar-ab"),
        ZfEvent(id="ar-base", type="autoresearch.baseline.scored", task_id="T-bug", correlation_id="ar-ab",
                payload={"baseline_score": 0.42}),
        ZfEvent(id="ar-candidate", type="autoresearch.candidate.scored", task_id="T-bug", correlation_id="ar-ab",
                payload={"candidate_score": 0.86}),
        ZfEvent(id="ar-ab", type="autoresearch.ab.completed", task_id="T-bug", correlation_id="ar-ab",
                payload={"baseline_score": 0.42, "candidate_score": 0.86, "score_delta": 0.44, "winner": "candidate"}),
        ZfEvent(id="bug-candidate", type="autoresearch.bug_candidate.created", task_id="T-bug", correlation_id="ar-bug"),
        ZfEvent(id="repair-prepared", type="autoresearch.repair.prepared", task_id="T-bug", correlation_id="ar-bug"),
        ZfEvent(id="repair-dispatch", type="autoresearch.repair.dispatch_requested", task_id="T-bug", correlation_id="ar-bug"),
        ZfEvent(id="validation-pass", type="autoresearch.validation.passed", task_id="T-bug", correlation_id="ar-bug"),
    ]
    workflow = build_workflow_trace(config=cfg, events=events, tasks=tasks, feature_id="F-1")

    projection = build_delivery_run_projection(
        config=cfg,
        events=list(enumerate(events)),
        tasks=tasks,
        workflow_trace=workflow,
        execution_graph={"nodes": []},
    )

    graphs = {graph["graph_id"]: graph for graph in projection["trace"]["autoresearch_graphs"]}
    assert graphs["ar-ab"]["comparison_mode"] == "ab"
    assert {node["kind"] for node in graphs["ar-ab"]["nodes"]} >= {"trigger", "baseline", "candidate", "ab_eval"}
    assert graphs["ar-bug"]["comparison_mode"] == "single_candidate"
    assert {node["kind"] for node in graphs["ar-bug"]["nodes"]} >= {"candidate", "repair", "validation"}
