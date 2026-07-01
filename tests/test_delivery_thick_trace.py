from __future__ import annotations

import json

from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.events.model import ZfEvent
from zf.runtime.delivery_thick_trace import build_delivery_thick_trace, export_otlp_json


def _trace() -> dict:
    return {
        "feature_id": "F-1",
        "project_id": "proj",
        "trace_id": "trace-F-1",
        "status": "in_progress",
        "workflow_archetype": "feature",
        "execution_graph": {
            "nodes": [
                {
                    "task_id": "T1",
                    "title": "build api",
                    "actual": {"status": "in_progress"},
                    "source_ref": "tasks/plan.md#task-1",
                },
                {"task_id": "T2", "actual": {"status": "backlog"}},
            ],
            "edges": [{"from": "T1", "to": "T2", "kind": "blocked_by", "status": "waiting"}],
        },
        "run_chain": {
            "stages": [{"stage": "impl", "status": "active", "task_ids": ["T1"]}],
        },
        "trace": {
            "spans": [
                {
                    "trace_id": "trace-F-1",
                    "span_id": "run:FX",
                    "status": "running",
                    "started_at": "2026-06-15T00:00:00+00:00",
                    "ended_at": "2026-06-15T00:00:03+00:00",
                    "duration_ms": 3000,
                    "cost_usd": "0.012",
                    "raw_event_refs": ["evt-fanout"],
                },
                {
                    "trace_id": "trace-F-1",
                    "span_id": "child:FX:dev",
                    "parent_span_id": "run:FX",
                    "task_id": "T1",
                    "run_id": "dev",
                    "fanout_id": "FX",
                    "status": "failed",
                    "started_at": "2026-06-15T00:00:01+00:00",
                    "ended_at": "",
                    "total_cost_usd": "bad-number",
                    "raw_event_refs": ["evt-child"],
                },
            ],
        },
        "current_bundle": {"current_task_map_ref": ".zf/artifacts/F-1/task_map.json"},
        "cursor": {"last_event_id": "evt-gate", "last_seq": 4},
    }


def _events() -> list[tuple[int, ZfEvent]]:
    events = [
        ZfEvent(
            type="task.rework.triage.completed",
            id="evt-rework",
            task_id="T1",
            actor="contract-d",
            payload={"classification": "evidence_payload_gap", "reason": "missing evidence"},
        ),
        ZfEvent(type="worker.stuck", id="evt-stuck", task_id="T1", actor="watchdog"),
        ZfEvent(type="discriminator.failed", id="evt-gate", task_id="T1", actor="functional-d"),
        ZfEvent(
            type="behavior.source_coverage_gap.detected",
            id="evt-source",
            task_id="T2",
            actor="source-coverage",
            payload={"reason": "source refs missing"},
        ),
        ZfEvent(
            type="eval.contract_completeness.completed",
            id="evt-contract",
            task_id="T2",
            actor="contract-eval",
            payload={"status": "failed", "score": 0.4, "detail": "verification missing"},
        ),
        ZfEvent(type="task.retry_requested", id="evt-retry", task_id="T1"),
        ZfEvent(
            type="worker.stuck",
            id="evt-unrelated-stuck",
            task_id="T99",
            actor="watchdog",
            payload={"feature_id": "F-OTHER"},
        ),
        ZfEvent(
            type="eval.contract_completeness.completed",
            id="evt-unrelated-eval",
            task_id="T99",
            actor="contract-eval",
            payload={"feature_id": "F-OTHER", "status": "failed", "score": 0.1},
            causation_id="evt-unrelated-stuck",
        ),
    ]
    return list(enumerate(events))


def test_build_delivery_thick_trace_graph_overlay_and_spans() -> None:
    thick = build_delivery_thick_trace(
        trace=_trace(),
        events=_events(),
        generated_at="2026-06-15T00:00:10+00:00",
        project_id="proj",
    )

    assert thick["schema_version"] == "delivery-thick-trace.v1"
    assert thick["target"]["id"] == "F-1"
    node_ids = {node["id"] for node in thick["graph"]["nodes"]}
    assert {"task:T1", "stage:impl", "span:run:FX", "span:child:FX:dev"} <= node_ids
    assert any(node["kind"] == "artifact" for node in thick["graph"]["nodes"])
    edge_kinds = {edge["kind"] for edge in thick["graph"]["edges"]}
    assert {"validated_by", "failed_by", "contains", "produced", "caused_by"} <= edge_kinds

    behavior_kinds = {item["kind"] for item in thick["behaviors"]}
    assert {"missing_evidence", "worker_stuck", "source_coverage_gap"} <= behavior_kinds
    assert "retry_loop" not in behavior_kinds
    assert all("T99" not in item.get("task_ids", []) for item in thick["behaviors"])
    eval_kinds = {item["kind"] for item in thick["evals"]}
    assert {"functional_check", "contract_completeness"} <= eval_kinds
    assert "replan_drift" not in eval_kinds
    assert all("T99" not in item.get("task_ids", []) for item in thick["evals"])

    child = next(span for span in thick["spans"] if span["span_id"] == "child:FX:dev")
    assert child["kind"] == "agent_run"
    assert child["cost_usd"] == 0.0
    assert child["degraded"] is True
    assert thick["improvement_candidates"]
    assert any(item["kind"] == "thick_trace_event_scope" for item in thick["diagnostics"])
    assert "sk-thisshouldberedacted" not in json.dumps(thick, ensure_ascii=False)


def test_delivery_thick_trace_scopes_project_wide_causation_edges() -> None:
    events = _events()
    for index in range(200):
        events.append((
            len(events),
            ZfEvent(
                type="worker.stuck",
                id=f"evt-noise-{index}",
                task_id=f"T-noise-{index}",
                payload={"feature_id": "F-OTHER"},
                causation_id=f"evt-noise-{index - 1}" if index else None,
            ),
        ))

    thick = build_delivery_thick_trace(
        trace=_trace(),
        events=events,
        generated_at="2026-06-15T00:00:10+00:00",
        project_id="proj",
    )

    assert thick["graph"]["edge_count"] < 40
    assert all(
        "evt-noise-" not in ",".join(edge.get("event_ids", []))
        for edge in thick["graph"]["edges"]
    )


def test_otlp_export_is_read_only_projection_shape() -> None:
    thick = build_delivery_thick_trace(
        trace=_trace(),
        events=[],
        generated_at="2026-06-15T00:00:10+00:00",
        project_id="proj",
    )
    otlp = export_otlp_json(thick)
    spans = otlp["resource_spans"][0]["scope_spans"][0]["spans"]

    assert spans[0]["trace_id"] == "trace-F-1"
    assert spans[0]["start_time_unix_nano"] > 0
    assert spans[1]["attributes"]["zaofu.task_id"] == "T1"
    assert spans[1]["attributes"]["zaofu.degraded"] is True


def test_doc94_detector_event_types_are_registered() -> None:
    assert "behavior.source_coverage_gap.detected" in KNOWN_EVENT_TYPES
    assert "eval.contract_completeness.completed" in KNOWN_EVENT_TYPES
    assert "eval.evidence_sufficiency.completed" in KNOWN_EVENT_TYPES
