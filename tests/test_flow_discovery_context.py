from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.flow_discovery_context import build_flow_discovery_context


def test_flow_discovery_uses_frozen_candidate_identity() -> None:
    candidate = ZfEvent(
        type="candidate.integration.completed",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "status": "frozen",
            "candidate_ref": "refs/heads/candidate/T1",
            "candidate_head_commit": "abc123",
        },
    )
    trigger = ZfEvent(
        type="verification.passed",
        correlation_id="run-1",
        payload={"workflow_run_id": "run-1"},
    )

    context = build_flow_discovery_context(
        [candidate, trigger],
        event=trigger,
        payload=dict(trigger.payload),
        fallback={},
        metadata={
            "task_pipeline": {
                "candidate": {"rolling_smoke": "required"},
            },
        },
        pdd_id="PDD-1",
        feature_id="FEATURE-1",
        trace_id="run-1",
        flow_kind="prd",
        discovery_profile="default",
    )

    assert context.candidate_ref == "refs/heads/candidate/T1"
    assert context.candidate_head_commit == "abc123"
    assert context.request_payload["candidate_ref"] == context.candidate_ref
    assert context.request_payload["candidate_head_commit"] == "abc123"
    assert context.request_payload["candidate_policy"] == {
        "rolling_smoke": "required",
    }


@pytest.mark.parametrize("mismatch", [None, "workflow_run_id", "task_map_generation", "target_commit", "fanout_id", "unadmitted"])
def test_discovery_receives_only_exact_admitted_shard_results(mismatch: str | None) -> None:
    identity = {
        "workflow_run_id": "run-1", "task_map_generation": "gen-1",
        "candidate_head_commit": "a" * 40, "fanout_id": "verify-1",
        "effective_config_ref": {"ref": "config", "sha256": "c" * 64},
        "effective_config_digest": "c" * 64,
    }
    child_payload = {**identity, "target_commit": "a" * 40}
    if mismatch and mismatch != "unadmitted":
        child_payload[mismatch] = "other"
    child = ZfEvent(type="verify.child.completed", payload=child_payload)
    descriptor = {"ref": "result.json", "sha256": "d" * 64}
    admitted = ZfEvent(type="workflow.call.result.admitted", payload={
        "source_event_id": child.id, "workflow_run_id": "run-1",
        "control_result_schema": "verification-result.v1",
        "control_result_ref": descriptor,
    })
    trigger = ZfEvent(type="test.passed", payload=identity)
    events = [child, trigger] if mismatch == "unadmitted" else [admitted, child, trigger]
    context = build_flow_discovery_context(
        events, event=trigger, payload=identity, fallback={}, metadata={},
        pdd_id="P1", feature_id="F1", trace_id="run-1",
        flow_kind="prd", discovery_profile="product_completeness",
    )
    assert context.request_payload["result_refs"] == ([] if mismatch else [descriptor])
    assert context.request_payload["effective_config_ref"] == identity["effective_config_ref"]


@pytest.mark.parametrize("source", ["payload", "fallback"])
def test_discovery_preserves_requirement_snapshot_for_gap_replanning(source: str) -> None:
    identity = {
        "requirement_spec_ref": "workflow-requests/run-1/requirements/revision-0002.json",
        "requirement_spec_digest": "a" * 64,
    }
    trigger = ZfEvent(type="verify.passed", payload={"workflow_run_id": "run-1"})
    context = build_flow_discovery_context(
        [trigger], event=trigger,
        payload={**trigger.payload, **(identity if source == "payload" else {})},
        fallback=identity if source == "fallback" else {},
        metadata={}, pdd_id="PRD-1", feature_id="PRD-1", trace_id="run-1",
        flow_kind="prd", discovery_profile="product_completeness",
    )
    assert {key: context.request_payload.get(key) for key in identity} == identity
