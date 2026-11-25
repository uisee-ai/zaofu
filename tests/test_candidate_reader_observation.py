from types import SimpleNamespace

import pytest

from tests.test_candidate_plan_recovery import _incident
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_contracts import CHECKPOINT_ACTIONS
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


@pytest.mark.parametrize("fault", ["", "unmapped", "blocked", "disabled"])
def test_partial_reader_evaluation_only_dispatches_one_shadow_bound_counterexample(tmp_path, fault):
    def inspect_before_barrier(runtime, candidate, failure):
        if fault == "disabled":
            runtime.config.workflow.orchestration.mode = "exception_advisor"
        manifest = {"topology": "fanout_reader", "trigger_event_id": candidate.id, "children": [
            {"child_id": "verify-0", "status": "failed", "last_event_id": failure.id}, {"child_id": "verify-1", "status": "running"}],
            "aggregate_config": {"mode": "wait_for_all"}}
        runtime._fanout_manifest = lambda _: manifest
        runtime._cancel_candidate_superseded_replan_fanout = lambda _: False
        runtime._fanout_identity_stale_reason = lambda _: ("", "")
        runtime._evaluate_reader_fanout = lambda fanout: Orchestrator._evaluate_reader_fanout(runtime, fanout)
        snapshot = SimpleNamespace(manifests=[("verify-fanout", manifest)])
        # Reconstruct a process after the child result reached disk but before its evaluation.
        recovered = Orchestrator._recover_incomplete_reader_fanout_aggregates(runtime, runtime.event_log.read_all(),
                                                                             recovery_snapshot=snapshot)
        assert recovered == (not fault)
        again = Orchestrator._recover_incomplete_reader_fanout_aggregates(runtime, runtime.event_log.read_all(),
                                                                         recovery_snapshot=snapshot)
        assert not again
        runtime._evaluate_reader_fanout("verify-fanout")
        events = runtime.event_log.read_all()
        assert not any(e.type in {"fanout.aggregate.completed", "test.failed", "test.passed",
                                  "orchestrator.replan_requested"} for e in events)
        requests = [e for e in events if e.type == "orchestrator.semantic.checkpoint.requested"]
        if fault:
            assert not requests
            return
        assert len(requests) == 1
        payload = requests[0].payload
        assert payload["source_event_id"] == failure.id
        assert (payload["checkpoint"], payload["checkpoint_policy"]) == ("candidate_reader_observation", "shadow")
        assert CHECKPOINT_ACTIONS[payload["checkpoint"]] == {"continue"}
        op = load_workflow_operation(runtime.event_log, payload["operation_id"])
        request = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
        inputs = hydrate_sidecar_ref(runtime.state_dir, request["checkpoint_input_ref"]).payload
        assert not inputs.get("execution_context_ref")
        counterexample, = [r for r in inputs["sources"] if r["source_id"] == "candidate-reader-counterexample"]
        assert counterexample["sha256"] == failure.payload["control_result_ref"]["sha256"]
        policy = hydrate_sidecar_ref(runtime.state_dir, request["input_consumption_policy_ref"]).payload
        assert {r["source_id"] for r in policy["required_reads"]} == {"checkpoint-input", "candidate-reader-counterexample"}

        from zf.runtime.artifact_read_ledger import read_attempt_artifact
        from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
        from zf.runtime.candidate_reader_observation import admitted_reader_observation
        from zf.runtime.orchestrator_agent_briefing import build_orchestrator_agent_operation_briefing
        from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
        from zf.runtime.orchestrator_agent_operations import (
            activate_orchestrator_agent_operation, prepared_operation_from_checkpoint_event,
        )
        from zf.runtime.result_submit import SemanticResultSubmitService

        prepared = prepared_operation_from_checkpoint_event(runtime, requests[0])
        briefing = build_orchestrator_agent_operation_briefing(state_dir=runtime.state_dir, prepared=prepared)
        assert "Other Readers and the definitive aggregate" in briefing
        activate_orchestrator_agent_operation(runtime, prepared, dispatch_id="dispatch-shadow", causation_id=failure.id)
        for row in policy["required_reads"]:
            read_attempt_artifact(runtime.state_dir, manifest=prepared.context.source_manifest, source_id=row["source_id"],
                                  artifact_id=row["artifact_id"], actor="orchestrator", role="orchestrator", provider="mock")
        identity = {k: request[k] for k in ("operation_id", "workflow_run_id", "checkpoint", "effective_config_digest",
                                              "plan_artifact_package_ref", "plan_artifact_package_digest", "task_map_generation")}
        identity["input_digest"] = request["checkpoint_input_digest"]
        submitted = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
            event_writer=runtime.event_writer).submit(operation_id=prepared.operation_id, role_instance="orchestrator",
            credential=(runtime.state_dir / "private/result-submit/roles/orchestrator.token").read_text().strip(),
            semantic_result={"schema_version": "orchestration-decision.v1", "execution_status": "completed",
                "identity": identity, "decision": "continue", "reason_codes": ["observed_failed_ac"],
                "summary": "One confirmed counterexample for TASK-2/AC-5. Other Readers remain pending.",
                "affected_work_units": ["TASK-2"], "expected_outcome": "Collect remaining Reader decisions",
                "required_followup": "Await the aggregate", "confidence": 0.9})
        event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
        outcome = apply_orchestrator_agent_decision(runtime, hydrate_profiled_control_result_event(runtime.state_dir, event))
        assert (outcome["status"], outcome["applied"]) == ("shadowed", False)
        assert admitted_reader_observation(runtime.state_dir, runtime.event_log.read_all(), {failure.id})["sha256"] == outcome["decision_ref"]["sha256"]
        assert not admitted_reader_observation(runtime.state_dir, runtime.event_log.read_all(), {"foreign-reader"})

    def record_admitted_package(runtime, run):
        package = runtime.event_log.read_all()[-1].payload
        runtime.event_writer.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel",
                                            correlation_id=run, payload=package))

    runtime, *_ = _incident(tmp_path, before_candidate=record_admitted_package, blocked=fault == "blocked", unknown_ac=fault == "unmapped",
                            on_reader_failed=inspect_before_barrier)
    assert any(e.type == "fanout.aggregate.completed" for e in runtime.event_log.read_all())
