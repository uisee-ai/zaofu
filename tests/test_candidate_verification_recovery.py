"""Mixed results may only reverify under a fully bound, separately authorized decision."""
from copy import deepcopy

import pytest

from tests.test_candidate_aggregate_review import _mixed
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig, RoleConfig
from zf.core.events import ZfEvent
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint


def _case(tmp_path, *, stage_fault="", historical_retry=False, width=2):
    runtime, failure, _ = _mixed(tmp_path, width)
    runtime.config.workflow.orchestration.autonomous_actions = ["rebind"]
    roles = ["verify-0", "verify-peer", *[f"verify-peer-{i}" for i in range(2, width)]]
    runtime.config.roles.extend(RoleConfig(name="verify", instance_id=r, role_kind="reader") for r in roles[1:])
    runtime.config.workflow.stages = [WorkflowStageConfig(id="candidate-verify", trigger="candidate.ready",
        topology="fanout_reader", attempt_domain="verify", roles=roles,
        aggregate=FanoutAggregateConfig(success_event="test.passed", failure_event="test.failed"))]
    if stage_fault == "children":
        from zf.core.config.schema import FanoutChildConfig
        runtime.config.workflow.stages[0].children = [FanoutChildConfig(role_instance="verify-0")]
    elif stage_fault == "affinity":
        runtime.config.workflow.stages[0].assignment.strategy = "affinity_stage_slots"
    if historical_retry:
        from dataclasses import replace
        events = runtime.event_log.read_all()
        runtime.event_log.path.write_text("")
        for row in events:
            if row.type == "candidate.ready":
                row = replace(row, payload={**row.payload, "rework_of": "older-failure",
                    "rework_attempt": 1, "rework_feedback": ["older finding"], "reason": "older retry"})
            runtime.event_writer.append(row)
    runtime.event_writer.default_origin = "kernel"
    runtime.event_writer.emit("run.admission.admitted", payload={"workflow_run_id": "run-semantic-1"})
    request = runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.failure.requested", origin="kernel",
        task_id="TASK-1", correlation_id="run-semantic-1", payload={"problem_class": "semantic",
            "workflow_run_id": "run-semantic-1", "flow_kind": "prd", "recovery_scope": "candidate",
            "failure_event_ids": [failure.id]}))
    prepared = request_semantic_failure_checkpoint(runtime, request)
    bound = aggregate_candidate_binding(runtime.state_dir, runtime.event_log.read_all(),
        failure_id=failure.id, run_id="run-semantic-1", task_id="TASK-1")
    recovery = {"classification": "verification_recovery", "contract_changed": False, "max_replays": 1,
        "failure_event_id": failure.id, "candidate_snapshot_event_id": bound["candidate_event_id"],
        "rationale": "Review newly bound evidence and the exact requirement; no finding is waived.",
        "member_reviews": [{**{k: row[k] for k in ("child_id", "operation_id", "control_result_ref")},
            "disposition": "reverify", "rationale": "Retain this finding for independent evaluation."}
            for row in bound["members"]]}
    return runtime, failure, prepared, recovery


@pytest.mark.parametrize("width", [2, 8])
def test_admitted_mixed_rebind_preserves_native_freeze_and_replays_once(tmp_path, width):
    runtime, failure, prepared, recovery = _case(tmp_path, width=width)
    event = _submit_rework(runtime, prepared, action="rebind",
                           directive_fields={"candidate_verification_recovery": recovery})
    outcome = apply_orchestrator_agent_decision(runtime, event)
    assert outcome["applied"], outcome
    assert apply_orchestrator_agent_decision(runtime, event) == outcome
    candidates = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
    assert len(candidates) == 2
    assert candidates[-1].payload["candidate_snapshot_event_id"] == candidates[0].id
    assert candidates[-1].payload.get("schema_version") != "candidate-freeze-receipt.v1"
    assert candidates[-1].payload["repaired_failure_event_id"] == failure.id
    assert next(e for e in runtime.event_log.read_all() if e.id == failure.id).payload["failure_kind"] == "mixed"
    assert not any(e.type in {"test.passed", "task_map.ready", "orchestrator.semantic.rework.requested"}
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize("fault", ["member", "missing_member", "contract", "candidate", "failure", "cap",
                                  "authority", "paused", "human_gate", "shadow", "stage_members", "stage_config"])
def test_recovery_never_changes_authority_or_drops_members(tmp_path, fault):
    runtime, _, prepared, recovery = _case(tmp_path)
    if fault == "member": recovery["member_reviews"][0]["operation_id"] = "foreign"
    elif fault == "missing_member": recovery["member_reviews"].pop()
    elif fault == "contract": recovery["contract_changed"] = True
    elif fault == "candidate": recovery["candidate_snapshot_event_id"] = "other-freeze"
    elif fault == "failure": recovery["failure_event_id"] = "other-failure"
    elif fault == "cap": recovery["max_replays"] = 2
    elif fault == "authority": runtime.config.workflow.orchestration.autonomous_actions = ["rework"]
    elif fault == "stage_members": runtime.config.workflow.stages[0].roles = ["verify-0"]
    elif fault == "stage_config": runtime.config.workflow.stages[0].timeout_seconds = 7
    elif fault == "paused":
        runtime.event_writer.emit("run.paused", payload={"workflow_run_id": "run-semantic-1"})
    elif fault == "human_gate":
        runtime.event_writer.emit("human.escalate", correlation_id="run-semantic-1",
            payload={"blocking_scope": "verification", "reason": "unresolved host effect"})
    else:
        # Rebuild the checkpoint under shadow policy; the decision cannot execute.
        runtime.config.workflow.orchestration.checkpoint_policies["semantic_failure"] = "shadow"
        request = next(e for e in reversed(runtime.event_log.read_all())
                       if e.type == "orchestrator.semantic.failure.requested")
        request = deepcopy(request); request.id += "-shadow"
        prepared = request_semantic_failure_checkpoint(runtime, request)
    event = _submit_rework(runtime, prepared, action="rebind",
                           directive_fields={"candidate_verification_recovery": recovery})
    result = apply_orchestrator_agent_decision(runtime, event)
    assert not result.get("applied"), result
    assert len([e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]) == 1


@pytest.mark.parametrize("historical_retry", [False, True])
def test_publication_crash_acknowledges_without_second_candidate(tmp_path, monkeypatch, historical_retry):
    runtime, _, prepared, recovery = _case(tmp_path, historical_retry=historical_retry)
    event = _submit_rework(runtime, prepared, action="rebind",
                           directive_fields={"candidate_verification_recovery": recovery})
    append = runtime.event_writer.append
    def crash(row):
        result = append(row)
        if row.type == "candidate.ready":
            raise RuntimeError("simulated death after publish")
        return result
    monkeypatch.setattr(runtime.event_writer, "append", crash)
    with pytest.raises(RuntimeError, match="simulated death"):
        apply_orchestrator_agent_decision(runtime, event)
    monkeypatch.setattr(runtime.event_writer, "append", append)
    result = apply_orchestrator_agent_decision(runtime, event)
    assert result["applied"] and result["publication_replayed"]
    assert len([e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]) == 2


@pytest.mark.parametrize("stage_fault", ["children", "affinity"])
def test_recovery_rejects_unproven_dynamic_roster_even_when_checkpoint_matches(tmp_path, stage_fault):
    runtime, _, prepared, recovery = _case(tmp_path, stage_fault=stage_fault)
    event = _submit_rework(runtime, prepared, action="rebind",
                          directive_fields={"candidate_verification_recovery": recovery})
    result = apply_orchestrator_agent_decision(runtime, event)
    assert not result.get("applied"), result
    assert len([e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]) == 1
