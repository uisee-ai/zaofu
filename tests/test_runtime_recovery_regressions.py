"""Regression inputs distilled from the loshu implementation handoff incident."""
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_adapters import _normalize_implementation
from zf.runtime.fanout_result_identity import bind_blocking_writer_result_identity
from zf.runtime.rework_triage import classify_rework_trigger


def test_kernel_admission_evidence_precedes_worker_scope_label():
    invalid = ZfEvent(id="evt-invalid", type="workflow.call.result.invalid",
                     origin="kernel", task_id="TASK-1", payload={"reason": "stale_call_result_superseded"})
    blocked = ZfEvent(type="dev.blocked", task_id="TASK-1", payload={
        "failure_class": "task_contract_unsatisfiable", "reason": "result admission rejected",
        "evidence_refs": ["event:evt-invalid"],
    })
    result = classify_rework_trigger(blocked, evidence_events=[invalid])
    assert result.suspected_owner == "harness"
    assert result.recommended_action == "suspend_for_harness_fix"
    assert classify_rework_trigger(blocked).suspected_owner == "planner"
    invalid.task_id = "OTHER"
    assert classify_rework_trigger(blocked, evidence_events=[invalid]).suspected_owner == "planner"


def test_queued_authority_cannot_overwrite_dispatched_result():
    result = bind_blocking_writer_result_identity(
        {"contract_authority_revision": "authority-dispatched",
         "impl_self_check": {"contract_authority_revision": "authority-dispatched"}},
        {"result_protocol_mode": "blocking",
         "payload": {"contract_authority_revision": "authority-queued"}},
        pinned_identity={"contract_authority_revision": "authority-dispatched"},
    )
    assert result["contract_authority_revision"] == "authority-dispatched"
    assert result["impl_self_check"]["contract_authority_revision"] == "authority-dispatched"


def test_explicit_stale_result_remains_visible_to_admission():
    result = bind_blocking_writer_result_identity(
        {"contract_authority_revision": "authority-stale"},
        {"result_protocol_mode": "blocking"},
        pinned_identity={"contract_authority_revision": "authority-current"},
    )
    assert result["contract_authority_revision"] == "authority-stale"


def test_implementation_adapter_preserves_self_check_and_authority():
    check = {"contract_authority_revision": "authority-current", "command_receipts": [{"exit_code": 0}]}
    result, issues = _normalize_implementation(ZfEvent(
        type="dev.build.done", task_id="TASK-1", payload={
            "source_commit": "abc", "contract_authority_revision": "authority-current",
            "impl_self_check": check, "impl_self_check_ref": "checks/abc.json",
            "impl_self_check_digest": "digest", "contract_snapshot_ref": "contracts/abc.json",
        }))
    assert result["self_check"] == check
    assert result["contract_authority_revision"] == "authority-current"
    assert result["impl_self_check_ref"] == "checks/abc.json"
    assert not issues


def test_implementation_adapter_rejects_conflicting_self_checks():
    _, issues = _normalize_implementation(ZfEvent(
        type="dev.build.done", task_id="TASK-1", payload={
            "source_commit": "abc", "self_check": {"status": "passed"},
            "impl_self_check": {"status": "failed"},
        }))
    assert any(issue["code"] == "conflicting_self_check" for issue in issues)


def test_implementation_adapter_rejects_malformed_self_check_without_crashing():
    _, issues = _normalize_implementation(ZfEvent(
        type="dev.build.done", task_id="TASK-1", payload={
            "source_commit": "abc", "impl_self_check": "passed",
        }))
    assert any(issue["code"] == "invalid_self_check" for issue in issues)
