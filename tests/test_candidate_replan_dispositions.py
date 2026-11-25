"""An OA return to planning preserves diagnosis without granting writer work."""
import pytest

from tests.test_candidate_verification_recovery import _case
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.writer_fanout_admission import select_writer_task_items


def replan_case(tmp_path, *, product=False, fault=None):
    runtime, failure, prepared, _ = _case(tmp_path)
    bound = aggregate_candidate_binding(runtime.state_dir, runtime.event_log.read_all(),
        failure_id=failure.id, run_id=failure.correlation_id, task_id="TASK-1")
    rows = []
    for finding in bound["findings"]:
        classification = "execution_unknown"
        if finding["product_repair_eligible"]:
            classification = "product_defect" if product else "contract_question"
        rows.append({"finding_id": finding["finding_id"], "classification": classification,
                     "owner": "runtime_manager" if classification == "execution_unknown" else "planner",
                     "next_action": "Review the affected requirement or recover the missing receipt.",
                     "rationale": "Bound to the same freeze and original Reader finding."})
    if fault == "duplicate":
        rows.append(rows[0])
    if fault == "partial":
        rows.pop()
    event = _submit_rework(runtime, prepared, action="return_to_plan",
        directive_fields={} if fault == "absent" else {"finding_dispositions": rows})
    return runtime, event, bound, rows


@pytest.mark.parametrize("product", [False, True])
def test_replan_dispatch_only_bound_product_findings(tmp_path, product):
    runtime, decision, bound, rows = replan_case(tmp_path, product=product)
    outcome = apply_orchestrator_agent_decision(runtime, decision)
    assert outcome["applied"], outcome
    events = runtime.event_log.read_all()
    marker, = [e for e in events if e.type == "orchestrator.replan_requested"]
    plan, = [e for e in events if e.type == "prd.plan.requested" and e.payload.get("rework_of")]
    for record in (marker, plan):
        payload = record.payload
        expected = ["TASK-2"] if product else []
        assert payload.get("task_ids", []) == expected
        assert payload.get("failed_task_ids", []) == expected
        obligations = payload["rework_summary"]["candidate_obligations"]
        assert [o["disposition"] for o in obligations] == rows
        assert all(o["operation_id"] and o["control_result_ref"] for o in obligations)
        items = [{"task_id": "TASK-2"}, {"task_id": "UNRELATED"}]
        selected, _, _, _ = select_writer_task_items({"tasks": items}, items, payload)
        assert [item["task_id"] for item in selected] == expected
    assert bound["failed_task_ids"]  # the original AC has a non-empty source Task
    assert apply_orchestrator_agent_decision(runtime, decision) == outcome


@pytest.mark.parametrize("fault", ["duplicate", "partial", "absent"])
def test_aggregate_replan_requires_exact_finding_coverage(tmp_path, fault):
    runtime, event, _, _ = replan_case(tmp_path, fault=fault)
    result = apply_orchestrator_agent_decision(runtime, event)
    assert result["applied"] is False
    assert not any(e.type == "orchestrator.replan_requested" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("crash_after", ["orchestrator.replan_requested", "prd.plan.requested"])
def test_restart_acknowledges_published_replan_once(tmp_path, monkeypatch, crash_after):
    from copy import copy
    from zf.core.events import EventLog, EventWriter

    runtime, decision, _, _ = replan_case(tmp_path)
    append = runtime.event_writer.append

    def interrupted(event):
        stored = append(event)
        if stored.type == crash_after and stored.payload.get("rework_of"):
            raise RuntimeError("controller exited after durable publication")
        return stored

    monkeypatch.setattr(runtime.event_writer, "append", interrupted)
    with pytest.raises(RuntimeError, match="controller exited"):
        apply_orchestrator_agent_decision(runtime, decision)
    restarted = copy(runtime)
    restarted.event_log = EventLog(runtime.event_log.path)
    restarted.event_writer = EventWriter(restarted.event_log)
    result = apply_orchestrator_agent_decision(restarted, decision)
    assert result["applied"], result
    assert apply_orchestrator_agent_decision(restarted, decision) == result
    events = restarted.event_log.read_all()
    assert len([e for e in events if e.type == "orchestrator.replan_requested"]) == 1
    assert len([e for e in events if e.type == "prd.plan.requested" and e.payload.get("rework_of")]) == 1


def test_unrelated_run_cannot_exhaust_the_semantic_replan_budget(tmp_path):
    from zf.core.events import ZfEvent

    runtime, decision, _, _ = replan_case(tmp_path)
    for _ in range(runtime.config.workflow.orchestration.max_plan_revisions):
        runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.decision.applied",
            origin="kernel", correlation_id="another-run",
            payload={"workflow_run_id": "another-run", "decision": "return_to_plan", "applied": True}))
    assert apply_orchestrator_agent_decision(runtime, decision)["applied"]
