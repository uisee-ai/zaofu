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
