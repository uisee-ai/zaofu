from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests import test_product_acceptance as product
from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.candidate_reader_assignments import (
    candidate_acceptance_id, validate_plan_assignment_coverage, validate_reader_assignments,
)
from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, package_event_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import hydrate_task_contract_snapshot
from zf.runtime.task_pipeline_candidate_readers import candidate_reader_stages
from zf.runtime.workflow_operation import load_workflow_operation
from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig


def _assignments():
    return [
        {"role_instance": "reader-a", "acceptance": [{"task_id": "TASK-1", "acceptance_id": "AC-1"}],
         "journey_ids": ["journey-create"], "plan_reads": {"task_map": ["$.value"]}},
        {"role_instance": "reader-b", "acceptance": [{"task_id": "TASK-1", "acceptance_id": "AC-2"}],
         "journey_ids": [], "plan_reads": {"task_map": []}},
    ]


def _scoped_project(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    spec = product._spec()
    spec["candidate_reader_assignments"] = _assignments()
    monkeypatch.setattr(product, "_spec", lambda **kw: spec)
    package, _ = product._package(runtime.state_dir)
    admitted = package_event_payload(hydrate_plan_artifact_package(runtime.state_dir, package), package,
                                     status="admitted")
    runtime.event_log.append(ZfEvent(id="evt-package", type="plan.artifact_package.admitted",
                                     correlation_id=product.RUN_ID, payload=admitted))
    _seed_frozen_candidate(runtime, run_id=product.RUN_ID, generation=product.GENERATION,
                           additional_checks=[("AC-2", "cmd-extra", "pytest tests/test_other.py")],
                           package_fields={"task_id": "FLOW-ANCHOR", "plan_artifact_package_id": package["package_id"],
                                           "plan_artifact_package_ref": package["ref"],
                                           "plan_artifact_package_digest": package["sha256"]})
    stage = WorkflowStageConfig(id="prd-lanes-verify", trigger="candidate.ready", topology="fanout_reader",
                               roles=["reader-a", "reader-b", "reader-idle"],
                               aggregate=FanoutAggregateConfig(success_event="test.passed"))
    discovery = WorkflowStageConfig(id="prd-post-verify-discovery", trigger="flow.discovery.requested",
                                    topology="fanout_reader", roles=["discovery"],
                                    aggregate=FanoutAggregateConfig(success_event="flow.discovery.completed",
                                    child_success_event="flow.discovery.child.completed"))
    runtime.config.workflow.flow_metadata.update({"flow_kind": "prd", "post_verify_discovery": "product_completeness"})
    runtime.config.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"]["workflow-read"] = "blocking"
    runtime.config.workflow.stages = [stage, discovery]
    runtime._fanout_stage_matches_trigger_event = lambda stage, event: True
    runtime._fanout_roles = lambda roles: [SimpleNamespace(instance_id=r) for r in roles]
    return runtime


def test_frozen_dispatch_pins_only_assigned_commands_reads_and_result_identity(tmp_path, monkeypatch):
    runtime = _scoped_project(tmp_path, monkeypatch)
    candidate = runtime.event_log.read_all()[-1]
    stage, = candidate_reader_stages(runtime, candidate)
    assert stage.roles == ["reader-a", "reader-b"]
    for index, role in enumerate(stage.roles, 1):
        payload = {"workflow_run_id": product.RUN_ID, "task_id": "FLOW-ANCHOR", "fanout_id": "fanout-1",
                   "child_id": role, "role_instance": role, "stage_id": stage.id,
                   "output_profile_id": "candidate-verify", "target_commit": candidate.payload["target_commit"],
                   "artifact_package_mode": "shadow", "canonical_success_event": "test.passed",
                   "canonical_failure_event": "test.failed"}
        prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_reader_child",
                               operation_key=role, stage_id=stage.id, task_id="FLOW-ANCHOR",
                               dispatch_id="dispatch-" + role, causation_id=candidate.id)
        op = load_workflow_operation(runtime.event_log, prepared.operation_id)
        request = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
        scope = request["candidate_reader_scope"]
        assert scope["role_instance"] == role
        assert scope["acceptance_ids"] == [candidate_acceptance_id("TASK-1", f"AC-{index}")]
        contract = hydrate_task_contract_snapshot(runtime.state_dir, {
            "ref": payload["contract_snapshot_ref"], "sha256": payload["contract_snapshot_digest"]})
        assert contract["candidate_reader_scope"] == scope
        assert [c["acceptance_id"] for c in contract["acceptance_criteria"]] == scope["acceptance_ids"]
        assert [c["command"] for c in contract["verification_commands"]] == [
            "pytest -q tests/test_pulse.py" if index == 1 else "pytest tests/test_other.py"]
        required = {(r["source_id"], r["json_path"]) for r in payload["required_reads"]}
        assert ("plan-port-task_map", "$") not in required
        assert (("plan-port-task_map", "$.value") in required) == (index == 1)
        assert (("plan-port-product_acceptance_spec", "$.user_journeys[0]") in required) == (index == 1)


@pytest.mark.parametrize("fault", ["missing_ac", "wrong_task", "duplicate", "missing_journey"])
def test_plan_rejects_incomplete_or_duplicate_reader_assignments(fault):
    spec = product._spec()
    spec["candidate_reader_assignments"] = rows = _assignments()
    task_map = {"tasks": [{"task_id": "TASK-1", "acceptance_criteria": [{"id": "AC-1"}, {"id": "AC-2"}]}]}
    if fault == "missing_ac":
        rows.pop()
    elif fault == "wrong_task":
        rows[0]["acceptance"][0]["task_id"] = "foreign"
    elif fault == "duplicate":
        rows[0]["acceptance"] *= 2
    else:
        rows[0]["journey_ids"] = []
    with pytest.raises(ValueError):
        validate_reader_assignments(spec)
        validate_plan_assignment_coverage(spec, task_map)


def test_independent_second_owner_is_allowed_and_discovery_is_required(tmp_path, monkeypatch):
    spec = product._spec()
    spec["candidate_reader_assignments"] = rows = _assignments()
    rows[1]["acceptance"].extend(deepcopy(rows[0]["acceptance"]))
    rows[1]["journey_ids"] = rows[0]["journey_ids"][:]
    validate_reader_assignments(spec)
    validate_plan_assignment_coverage(spec, {"tasks": [
        {"task_id": "TASK-1", "acceptance_criteria": [{"id": "AC-1"}, {"id": "AC-2"}]}]})
    runtime = _scoped_project(tmp_path, monkeypatch)
    runtime.config.workflow.stages.pop()
    trigger = runtime.event_log.read_all()[-1]
    for _ in range(2):
        assert candidate_reader_stages(runtime, trigger) == []
    rejected, = [e for e in runtime.event_log.read_all() if e.type == "task.fanout.rejected"]
    assert rejected.payload["recovery_owner"] == "run_manager"
    assert "product_completeness" in rejected.payload["error"]


def test_redrive_preserves_roster_from_original_freeze(tmp_path, monkeypatch):
    runtime = _scoped_project(tmp_path, monkeypatch)
    native = runtime.event_log.read_all()[-1]
    event = ZfEvent(id="evt-retrigger", type="candidate.ready", origin="kernel",
                    correlation_id=product.RUN_ID, causation_id=native.id,
                    payload={"candidate_snapshot_event_id": native.id, "workflow_run_id": product.RUN_ID})
    runtime.event_log.append(event)
    stage, = candidate_reader_stages(runtime, event)
    assert stage.roles == ["reader-a", "reader-b"]


def test_orchestrator_dispatches_only_approved_seats_with_bounded_briefings(tmp_path, monkeypatch):
    from tests.test_reader_fanout_runtime import _config, _RecordingTransport
    from zf.core.config.schema import RoleConfig
    from zf.runtime.orchestrator import Orchestrator

    seeded = _scoped_project(tmp_path, monkeypatch)
    config = _config()
    config.roles = [RoleConfig(name=role, backend="mock", role_kind="reader") for role in
                    ["reader-a", "reader-b", "reader-idle", "discovery"]]
    config.workflow.stages = seeded.config.workflow.stages
    config.workflow.flow_metadata = seeded.config.workflow.flow_metadata
    stage = config.workflow.stages[0]
    stage.aggregate.child_success_event = "test.child.passed"
    stage.aggregate.child_failure_event = "test.child.failed"
    stage.aggregate.failure_event = "test.failed"
    transport = _RecordingTransport()
    runtime = Orchestrator(seeded.state_dir, config, transport)
    trigger = seeded.event_log.read_all()[-1]
    runtime.run_once(events=[trigger])
    assert {role for role, *_ in transport.sent} == {"reader-a", "reader-b"}
    for role, briefing_path, *_ in transport.sent:
        briefing = briefing_path.read_text()
        assert "Approved Candidate Reader assignment" in briefing
        assigned = candidate_acceptance_id("TASK-1", "AC-1" if role == "reader-a" else "AC-2")
        assert "Acceptance: " + assigned in briefing
        assert ("Product journeys: journey-create" in briefing) == (role == "reader-a")
    runtime.run_once(events=[trigger])
    assert len(transport.sent) == 2


def test_scoped_readers_require_blocking_discovery_admission(tmp_path, monkeypatch):
    runtime = _scoped_project(tmp_path, monkeypatch)
    runtime.config.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"]["workflow-read"] = "shadow"
    assert candidate_reader_stages(runtime, runtime.event_log.read_all()[-1]) == []
    assert "blocking" in runtime.event_log.read_all()[-1].payload["error"]

def test_legacy_frozen_reader_contract_can_replay_its_pre_oa_optional_coverage():
    from zf.runtime.candidate_reader_assignments import scope_reader_contract

    spec = {"candidate_reader_assignments": _assignments()}
    criteria = [{"source_task_id": "TASK-1", "source_acceptance_id": ac,
                 "acceptance_id": candidate_acceptance_id("TASK-1", ac), "mandatory": ac != "legacy-optional"}
                for ac in ("AC-1", "AC-2", "legacy-optional")]
    frozen = {"acceptance_criteria": criteria, "candidate_event_id": "freeze", "plan_artifact_package_digest": "a"*64,
              "contract_revision": "legacy", "verification_commands": []}
    with pytest.raises(ValueError, match="coverage mismatch"):
        scope_reader_contract(dict(frozen), spec, role="reader-a", target="a"*40)
    scope = scope_reader_contract(frozen, spec, role="reader-a", target="a"*40, all_declared=False)
    assert scope["acceptance_ids"] == [candidate_acceptance_id("TASK-1", "AC-1")]
