"""ZF-E2E-PRDCTL-P0-1: budget-blocked dispatch failures must route to owner
escalation, never to paid quality rework.

Live incident (2026-07-12 csvstats round): judge dispatch was blocked by the
budget gate, the aggregate judge.failed carried the cause only as reason
text, and plan_candidate_rework planned candidate_retrigger for it.
"""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.failure_kind import (
    FAILURE_KIND_BUDGET,
    aggregate_failure_kind,
    classify_dispatch_exception,
    failure_kind_from_payload,
    is_budget_reason,
)
from zf.runtime.candidate_rework import plan_candidate_rework
from zf.runtime.orchestrator import BudgetExceededError


def _ev(etype, payload=None, task_id=None, eid="", corr=""):
    return SimpleNamespace(
        type=etype, payload=payload or {}, task_id=task_id, id=eid, correlation_id=corr
    )


def test_classify_dispatch_exception_budget():
    exc = BudgetExceededError("dispatch to judge-prd blocked: budget exceeded")
    assert classify_dispatch_exception(exc) == FAILURE_KIND_BUDGET


def test_classify_dispatch_exception_other_unclassified():
    assert classify_dispatch_exception(RuntimeError("pane vanished")) == ""


def test_failure_kind_from_payload_field_wins_over_text():
    assert failure_kind_from_payload({"failure_kind": "infra"}) == "infra"
    assert failure_kind_from_payload(
        {"reason": "dispatch to judge-prd blocked: budget exceeded"}
    ) == FAILURE_KIND_BUDGET
    assert failure_kind_from_payload({"reason": "tests failed"}) == ""


def test_aggregate_failure_kind_uniform_and_mixed():
    assert aggregate_failure_kind(
        [{"failure_kind": "budget"}, {"reason": "blocked: budget exceeded"}]
    ) == FAILURE_KIND_BUDGET
    assert aggregate_failure_kind(
        [{"failure_kind": "budget"}, {"reason": "assert failed"}]
    ) == "mixed"
    assert aggregate_failure_kind([{"reason": "assert failed"}]) == ""


def test_budget_blocked_judge_failed_escalates_not_retrigger():
    # Payload shape copied from the live 2026-07-12 archive (evt at 02:02:12).
    events = [
        _ev("judge.failed", {
            "fanout_id": "fanout-prd-lanes-final-evt-fbece14e",
            "trace_id": "evt-6a5c3773553a",
            "stage_id": "prd-lanes-final",
            "pdd_id": "prd-csvstats",
            "feature_id": "csvstats-cli",
            "status": "failed",
            "target_ref": "candidate/prd-csvstats",
            "child_count": 1,
            "findings": [{
                "finding_id": "judge-prd-reason",
                "severity": "high",
                "category": "runtime_failure",
                "child_id": "judge-prd",
                "message": "dispatch to judge-prd blocked: budget exceeded",
            }],
            "failed_children": ["judge-prd"],
            "reason": "1/1 children failed: dispatch to judge-prd blocked: budget exceeded",
        }, eid="jf1", corr="evt-6a5c3773553a"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "escalate"
    assert plan.classification == "budget_blocked"


def test_budget_kind_field_on_child_failed_escalates():
    events = [
        _ev("verify.child.failed", {
            "fanout_id": "f1",
            "child_id": "verify-lane-0",
            "reason": "dispatch to verify-lane-0 blocked: budget exceeded",
            "failure_kind": "budget",
            "trace_id": "t1",
        }, eid="vc1", corr="t1"),
        _ev("verify.failed", {
            "fanout_id": "f1",
            "target_ref": "cand/PDD-X",
            "trace_id": "t1",
        }, eid="vf1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "escalate"
    assert plans[0].classification == "budget_blocked"


def test_budget_failure_does_not_consume_rework_attempts():
    events = [
        _ev("judge.failed", {
            "fanout_id": "f1",
            "target_ref": "cand/PDD-X",
            "trace_id": "t1",
            "reason": "1/1 children failed: dispatch blocked: budget exceeded",
        }, eid="jf1", corr="t1"),
        # Owner raised budget; the retrigger marker references the budget
        # failure — it must not burn the quality rework budget.
        _ev("task_map.ready", {
            "pdd_id": "PDD-X",
            "rework_of": "jf1",
            "trace_id": "t1",
        }, eid="tm1", corr="t1"),
        _ev("verify.failed", {
            "fanout_id": "f2",
            "target_ref": "cand/PDD-X",
            "trace_id": "t1",
            "reason": "coverage gap: missing error-path test",
        }, eid="vf1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    by_source = {p.source_event_id: p for p in plans}
    assert by_source["vf1"].action == "retrigger"
    assert by_source["vf1"].attempt == 1


def test_quality_failure_regression_still_retriggers():
    events = [
        _ev("verify.failed", {
            "fanout_id": "f1",
            "target_ref": "cand/PDD-Y",
            "trace_id": "t1",
            "reason": "2 pytest failures in csvstats error paths",
        }, eid="vf1", corr="t1"),
    ]
    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    assert plans[0].action == "retrigger"


def test_is_budget_reason_markers():
    assert is_budget_reason("Dispatch to X blocked: BUDGET EXCEEDED")
    assert not is_budget_reason("worker_transport_not_alive")
