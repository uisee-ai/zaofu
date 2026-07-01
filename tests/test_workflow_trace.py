from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.workflow_trace import build_workflow_trace

ROOT = Path(__file__).resolve().parents[1]


def test_workflow_trace_contains_pending_yaml_stages_without_events() -> None:
    cfg = load_config(ROOT / "examples" / "hermes-codex.yaml")

    trace = build_workflow_trace(config=cfg, events=[], feature_id="F-1")

    assert trace["schema_version"] == "workflow-trace.v1"
    stage_ids = {run["stage_id"] for run in trace["stage_runs"]}
    assert "cj-min-refactor-scan" in stage_ids
    assert "cj-min-refactor-scan:aggregate" in stage_ids
    scan = next(run for run in trace["stage_runs"] if run["stage_id"] == "cj-min-refactor-scan")
    assert scan["status"] == "pending"
    assert scan["kind"] == "fanout"
    assert scan["operator_kind"] == "synthesize"


def test_workflow_trace_projects_fanout_children_and_aggregate_status() -> None:
    cfg = load_config(ROOT / "examples" / "hermes-codex.yaml")
    events = [
        ZfEvent(
            id="evt-start",
            type="fanout.started",
            ts="2026-06-09T00:00:00+00:00",
            payload={
                "feature_id": "F-1",
                "fanout_id": "FX",
                "stage_id": "cj-min-refactor-scan",
                "expected_children": [
                    {"child_id": "scan-contract", "role_instance": "scan-contract", "task_id": "T1"},
                    {"child_id": "scan-runtime", "role_instance": "scan-runtime", "task_id": "T2"},
                ],
            },
        ),
        ZfEvent(
            id="evt-c1-dispatch",
            type="fanout.child.dispatched",
            ts="2026-06-09T00:01:00+00:00",
            payload={"feature_id": "F-1", "fanout_id": "FX", "stage_id": "cj-min-refactor-scan", "child_id": "scan-contract"},
        ),
        ZfEvent(
            id="evt-c1-complete",
            type="fanout.child.completed",
            ts="2026-06-09T00:04:00+00:00",
            payload={"feature_id": "F-1", "fanout_id": "FX", "stage_id": "cj-min-refactor-scan", "child_id": "scan-contract"},
        ),
        ZfEvent(
            id="evt-c2-dispatch",
            type="fanout.child.dispatched",
            ts="2026-06-09T00:02:00+00:00",
            payload={"feature_id": "F-1", "fanout_id": "FX", "stage_id": "cj-min-refactor-scan", "child_id": "scan-runtime"},
        ),
        ZfEvent(
            id="evt-c2-fail",
            type="fanout.child.failed",
            ts="2026-06-09T00:05:00+00:00",
            payload={
                "feature_id": "F-1",
                "fanout_id": "FX",
                "stage_id": "cj-min-refactor-scan",
                "child_id": "scan-runtime",
                "reason": "coverage gap",
            },
        ),
        ZfEvent(
            id="evt-agg",
            type="fanout.aggregate.started",
            ts="2026-06-09T00:06:00+00:00",
            payload={"feature_id": "F-1", "fanout_id": "FX", "stage_id": "cj-min-refactor-scan"},
        ),
    ]
    tasks = {
        "T1": Task(id="T1", title="contract", contract=TaskContract(feature_id="F-1", owner_role="scan-contract")),
        "T2": Task(id="T2", title="runtime", contract=TaskContract(feature_id="F-1", owner_role="scan-runtime")),
    }

    trace = build_workflow_trace(config=cfg, events=events, tasks=tasks, feature_id="F-1")

    scan = next(run for run in trace["stage_runs"] if run["stage_id"] == "cj-min-refactor-scan")
    aggregate = next(run for run in trace["stage_runs"] if run["stage_id"] == "cj-min-refactor-scan:aggregate")
    assert scan["status"] == "aggregating"
    assert aggregate["status"] == "aggregating"
    assert scan["metrics"]["children_total"] == 2
    assert scan["metrics"]["children_passed"] == 1
    assert scan["metrics"]["children_failed"] == 1
    assert scan["fanout_child_runs"][1]["error"]["message"] == "coverage gap"
    assert "cj-min-refactor-scan" in trace["active_stage_ids"]
    assert trace["metrics"]["fanout_total"] == 1
    assert trace["fanout_runs"][0]["child_runs"][1]["failure_reason"] == "coverage gap"
    json.dumps(trace)


def test_workflow_trace_projects_queue_aggregate_and_critical_metrics() -> None:
    cfg = load_config(ROOT / "examples" / "hermes-codex.yaml")
    events = [
        ZfEvent(
            id="evt-request",
            type="fanout.requested",
            ts="2026-06-09T00:00:00+00:00",
            payload={
                "feature_id": "F-1",
                "fanout_id": "FX",
                "stage_id": "cj-min-refactor-scan",
                "status": "requested",
            },
        ),
        ZfEvent(
            id="evt-start",
            type="fanout.started",
            ts="2026-06-09T00:01:00+00:00",
            payload={
                "feature_id": "F-1",
                "fanout_id": "FX",
                "stage_id": "cj-min-refactor-scan",
                "expected_children": [{"child_id": "scan-contract", "task_id": "T1"}],
            },
        ),
        ZfEvent(
            id="evt-c1-complete",
            type="fanout.child.completed",
            ts="2026-06-09T00:03:00+00:00",
            payload={
                "feature_id": "F-1",
                "fanout_id": "FX",
                "stage_id": "cj-min-refactor-scan",
                "child_id": "scan-contract",
                "task_id": "T1",
            },
        ),
        ZfEvent(
            id="evt-agg-complete",
            type="fanout.aggregate.completed",
            ts="2026-06-09T00:06:00+00:00",
            payload={
                "feature_id": "F-1",
                "fanout_id": "FX",
                "stage_id": "cj-min-refactor-scan",
            },
        ),
    ]
    tasks = {
        "T1": Task(id="T1", title="contract", contract=TaskContract(feature_id="F-1", owner_role="scan-contract")),
    }

    trace = build_workflow_trace(config=cfg, events=events, tasks=tasks, feature_id="F-1")

    scan = next(run for run in trace["stage_runs"] if run["stage_id"] == "cj-min-refactor-scan")
    assert scan["queue_wait_ms"] == 60_000
    assert scan["metrics"]["aggregate_wait_ms"] == 180_000
    assert trace["fanout_runs"][0]["metrics"]["aggregate_wait_ms"] == 180_000
    assert trace["metrics"]["critical_path_ms"] >= 360_000


def test_workflow_trace_degrades_without_config() -> None:
    trace = build_workflow_trace(config=None, events=[], feature_id="F-1")

    assert trace["schema_version"] == "workflow-trace.v1"
    assert trace["stage_runs"] == []
    assert {item["kind"] for item in trace["diagnostics"]} == {"workflow_config_missing"}
