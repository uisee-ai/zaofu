import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_reader_replan_plan_binding import _dispatched_replan


def test_unadmitted_reader_scratch_cannot_supply_aggregate_gaps(tmp_path):
    state, log, orch, child, _, _ = _dispatched_replan(tmp_path)
    fanout_id = child["fanout_id"]
    # A rejected provider result is retained for diagnosis before admission.
    scratch = (
        state / "fanouts" / fanout_id / "children" / child["child_id"] / "result.json"
    )
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(
        json.dumps(
            {
                "payload": {
                    "gap_tasks": [{"id": "UNADMITTED"}],
                    "report": {"gap_tasks": [{"id": "UNADMITTED"}]},
                }
            }
        )
    )
    payloads = orch._fanout_child_payloads(orch._fanout_manifest(fanout_id))
    assert not any(
        p.get("gap_tasks") or p.get("report", {}).get("gap_tasks") for p in payloads
    )


def test_exhausted_reader_operation_converges_on_recovery(tmp_path):
    state, log, orch, child, _, _ = _dispatched_replan(tmp_path)
    failed = ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        origin="kernel",
        payload={
            "fanout_id": child["fanout_id"],
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "failure_class": "worker_noop_or_terminal_missing",
            "reason": "provider_turn_closed_without_child_result_recovery_exhausted",
        },
    )
    log.append(failed)
    orch.run_once(events=[failed])
    orch._recover_unrecorded_reader_fanout_results()
    ops = reduce_workflow_operations(log.read_all())
    assert ops[child["operation_id"]]["status"] == "blocked"
    orch._recover_unrecorded_reader_fanout_results()
    terminal = [
        e
        for e in log.read_all()
        if e.type == "workflow.operation.blocked"
        and e.payload.get("operation_id") == child["operation_id"]
    ]
    assert len(terminal) == 1
    assert terminal[0].causation_id == failed.id


def test_admitted_semantic_report_cannot_be_replaced_by_later_scratch(tmp_path):
    from zf.runtime.call_result_runtime import hydrate_admitted_control_result

    state, log, orch, child, _, _ = _dispatched_replan(tmp_path)
    source = ZfEvent(
        type="flow.discovery.child.failed",
        actor="discovery",
        payload={
            **child,
            "status": "failed",
            "report": {
                "workflow_run_id": "workflow-1",
                "summary": "missing slice",
                "recommendation": "reject",
                "findings": [
                    {"severity": "high", "path": "app/missing.py", "message": "missing"}
                ],
                "requirement_coverage_matrix": [
                    {
                        "acceptance_id": "GAP-CURRENT-AC1",
                        "status": "failed",
                        "findings": [
                            {
                                "severity": "high",
                                "path": "app/missing.py",
                                "message": "missing",
                            }
                        ],
                        "evidence_refs": ["git:partial456"],
                    }
                ],
            },
        },
    )
    orch.run_once(events=[source])
    manifest = orch._fanout_manifest(child["fanout_id"])
    admitted_child = manifest["children"][0]
    descriptor = admitted_child["admitted_call_result_ref"]
    expected = hydrate_admitted_control_result(state, descriptor)
    assert expected["verdict"] == "rejected"
    scratch = (
        state
        / "fanouts"
        / child["fanout_id"]
        / "children"
        / child["child_id"]
        / "result.json"
    )
    scratch.write_text(
        json.dumps(
            {
                "payload": {
                    "report": {"summary": "POISON"},
                    "gap_tasks": [{"id": "POISON"}],
                }
            }
        )
    )
    payloads = orch._fanout_child_payloads(manifest)
    assert payloads[0]["report"] == expected
    assert not payloads[0].get("gap_tasks")


import pytest


@pytest.mark.parametrize(
    "change",
    [
        "new_dispatch",
        "new_operation_event",
        "settled",
        "writer",
        "foreign_run",
        "untrusted",
    ],
)
def test_terminal_recovery_does_not_block_unrelated_or_newer_attempt(tmp_path, change):
    from zf.runtime.call_result_runtime import workflow_operation_service

    state, log, orch, child, _, _ = _dispatched_replan(tmp_path)
    failed = ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        origin="kernel",
        payload={
            "fanout_id": child["fanout_id"],
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "failure_class": "worker_noop_or_terminal_missing",
            "reason": "provider_turn_closed_without_child_result_recovery_exhausted",
        },
    )
    if change == "foreign_run":
        failed.payload["run_id"] = "other-run"
    if change == "untrusted":
        failed.origin = "agent"
    log.append(failed)
    if change == "new_dispatch":
        log.append(ZfEvent(type="fanout.child.dispatched", payload=dict(child)))
    elif change == "new_operation_event":
        log.append(
            ZfEvent(
                type="workflow.operation.retry_started",
                payload={
                    "operation_id": child["operation_id"],
                    "request_hash": child["request_hash"],
                    "retry_attempt": 1,
                },
            )
        )
    elif change == "settled":
        log.append(
            ZfEvent(
                type="workflow.operation.settled",
                payload={"operation_id": child["operation_id"]},
            )
        )
    elif change == "writer":
        # A different operation family cannot be terminalized by reader evidence.
        log.append(
            ZfEvent(
                type="workflow.operation.requested",
                payload={
                    "operation_id": child["operation_id"],
                    "operation_type": "task_pipeline_impl",
                },
            )
        )
    before = reduce_workflow_operations(log.read_all())[child["operation_id"]]["status"]
    orch._recover_unrecorded_reader_fanout_results()
    assert (
        reduce_workflow_operations(log.read_all())[child["operation_id"]]["status"]
        == before
    )
    assert not any(e.type == "workflow.operation.blocked" for e in log.read_all())
