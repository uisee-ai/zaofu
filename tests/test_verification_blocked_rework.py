"""Evidence blockers do not authorize product implementation rework."""
from __future__ import annotations
import pytest
from zf.core.events.model import ZfEvent
from zf.runtime.candidate_rework import plan_candidate_rework
from zf.runtime.failure_kind import aggregate_failure_kind, failure_kind_from_payload


def _blocked() -> dict:
    return {"control_result_schema": "verification-result.v1", "semantic_verdict": "blocked"}


@pytest.mark.parametrize("verdict", ["blocked", "abstain", "abstained"])
def test_typed_verification_without_product_verdict_is_infra(verdict: str) -> None:
    assert failure_kind_from_payload({**_blocked(), "semantic_verdict": verdict}) == "infra"


def test_product_rejection_and_nonverification_block_remain_distinct() -> None:
    assert failure_kind_from_payload({**_blocked(), "semantic_verdict": "rejected"}) == ""
    assert failure_kind_from_payload({**_blocked(), "control_result_schema": "other.v1"}) == ""
    assert aggregate_failure_kind([_blocked(), {"semantic_verdict": "rejected"}]) == "mixed"


def test_evidence_block_and_missing_provider_result_do_not_reimplement_candidate() -> None:
    events = [
        ZfEvent(type="fanout.child.failed", id="child-blocked", origin="kernel", payload={
            "fanout_id": "f1", "child_id": "verify-0", **_blocked(),
        }),
        ZfEvent(type="fanout.child.failed", id="child-missing", origin="kernel", payload={
            "fanout_id": "f1", "child_id": "verify-1",
            "failure_class": "worker_noop_or_terminal_missing",
        }),
        ZfEvent(type="test.failed", id="aggregate", origin="kernel", payload={
            "fanout_id": "f1", "pdd_id": "P1", "target_ref": "candidate/P1",
            "trace_id": "run-1", "failed_children": ["verify-0", "verify-1"],
        }),
    ]
    assert plan_candidate_rework(events) == []


def test_queued_product_rework_rejected_after_infra_classification(tmp_path) -> None:
    from tests.test_control_actions_recovery import _service
    svc = _service(tmp_path)
    svc.writer.append(ZfEvent(type="fanout.child.failed", origin="kernel", payload={
        "fanout_id": "f1", "child_id": "verify-0", **_blocked(),
    }))
    source = svc.writer.append(ZfEvent(type="test.failed", origin="kernel", payload={
        "fanout_id": "f1", "pdd_id": "P1", "target_ref": "candidate/P1",
    }))
    requested = svc.writer.emit("control.action.requested", actor="operator", payload={})
    result = svc.execute(action="candidate-rework-apply", requested_action="candidate-rework-apply",
        requested=requested, payload={"pdd_id": "P1", "source_event_id": source.id,
                                     "candidate_rework_action": "retrigger"})
    assert not result["ok"]
    assert "runtime verification blocker" in result["reason"]
    assert not any(e.type == "task_map.ready" for e in svc.writer.event_log.read_all())
