from __future__ import annotations

import json

from zf.core.events.model import ZfEvent
from zf.runtime.observability_projection import (
    build_delivery_closed_loop,
    build_observability_projection,
    observability_span,
)


def test_observability_span_infers_stable_ids_and_preserves_token_metrics():
    event = ZfEvent(
        type="dev.impl.completed",
        id="evt-1",
        task_id="T-1",
        actor="dev-1",
        payload={
            "backend": "codex",
            "usage": {"input_tokens": 12, "output_tokens": 4},
            "authorization": "Bearer plain-secret",
        },
    )

    span = observability_span(event, seq=7, project_id="repo-a")

    assert span["project_id"] == "repo-a"
    assert span["trace_id"] == "T-1"
    assert span["span_id"] == "evt-1"
    assert span["task_id"] == "T-1"
    assert span["backend"] == "codex"
    assert span["payload"]["usage"]["input_tokens"] == 12
    assert span["payload"]["authorization"] == "[REDACTED_SECRET]"
    assert "trace_id" in span["inferred_fields"]


def test_observability_projection_groups_by_trace_and_redacts_headers():
    events = [
        (1, ZfEvent(type="fanout.started", id="evt-a", payload={
            "trace_id": "tr-1",
            "fanout_id": "fan-1",
            "headers": {"authorization": "Bearer secret"},
        })),
        (2, ZfEvent(type="fanout.aggregate.completed", id="evt-b", payload={
            "trace_id": "tr-1",
            "fanout_id": "fan-1",
            "status": "completed",
        })),
    ]

    projection = build_observability_projection(events, project_id="repo-a")

    assert projection["schema_version"] == "observability-projection.v1"
    assert projection["trace_count"] == 1
    assert projection["traces"][0]["trace_id"] == "tr-1"
    assert projection["traces"][0]["status"] == "completed"
    dumped = json.dumps(projection)
    assert "Bearer secret" not in dumped
    assert "[REDACTED_SECRET]" in dumped


def test_delivery_closed_loop_projects_tasks_fanout_replan_and_ship_edges():
    trace = {
        "trace_id": "trace-F-1",
        "project_id": "repo-a",
        "feature_id": "F-1",
        "status": "in_progress",
        "task_map": {"task_map_ref": "artifacts/F-1/task_map.json"},
        "execution_graph": {
            "nodes": [
                {
                    "task_id": "T1",
                    "title": "auth",
                    "planned": {"owner_role": "dev", "wave": 1},
                    "actual": {
                        "status": "done",
                        "assigned_to": "dev-1",
                        "trace_id": "tr-task",
                        "evidence_events": ["evt-build"],
                        "fanout_ids": ["fan-1"],
                        "changed_files": ["src/auth.ts"],
                    },
                },
                {
                    "task_id": "T2",
                    "title": "gateway",
                    "planned": {"owner_role": "dev", "wave": 2},
                    "actual": {
                        "status": "in_progress",
                        "assigned_to": "dev-2",
                        "evidence_events": [],
                    },
                },
            ],
            "edges": [{"from": "T1", "to": "T2", "kind": "blocked_by", "status": "satisfied"}],
        },
        "workflow_spine": {
            "nodes": [
                {"fanout_id": "fan-1", "task_id": "T1", "status": "completed", "event_id": "evt-fan"}
            ],
        },
        "replan_contract_gate": {
            "status": "ready_to_adopt",
            "latest_eval": {
                "event_id": "evt-gate",
                "decision": "adopt",
                "old_task_map_ref": "task_map-v1.json",
                "new_task_map_ref": "task_map-v2.json",
            },
        },
        "ship": {"status": "blocked", "required_tasks": 2, "done_tasks": 1},
        "source_event_ids": ["evt-build", "evt-fan", "evt-gate"],
    }

    graph = build_delivery_closed_loop(trace)

    node_ids = {node["node_id"] for node in graph["nodes"]}
    edge_keys = {(edge["from"], edge["to"], edge["kind"]) for edge in graph["edges"]}
    assert "delivery:trace-F-1" in node_ids
    assert "task:T1" in node_ids
    assert "fanout:fan-1" in node_ids
    assert "gate:replan:trace-F-1" in node_ids
    assert "ship:trace-F-1" in node_ids
    assert ("task:T1", "task:T2", "blocked_by") in edge_keys
    assert ("fanout:fan-1", "task:T1", "executes_task") in edge_keys
    assert ("gate:replan:trace-F-1", "delivery:trace-F-1", "guards_replan") in edge_keys
    assert graph["node_count"] == len(graph["nodes"])
