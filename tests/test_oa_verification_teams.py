from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_orchestrator_agent_run_plan import _checkpoint, _decision, _runtime, _submit
from tests.test_task_pipeline_reconciler import _operation, _policy
from tests.test_workflow_registered_controller import assembly
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.oa_task_verify_assignment import VERIFY_WAIT, request_verification_corrections
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_operations import prepared_operation_from_checkpoint_event
from zf.runtime.task_contract_snapshot import effective_contract_revision
from zf.runtime.task_pipeline_orchestration import admitted_pipeline_delegations
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler


def test_admitted_group_dispatches_three_and_reowns_only_the_changed_contract(tmp_path, monkeypatch):
    runtime, trigger, loaded, package, package_ref = _runtime(tmp_path)
    readers = ["verify-1", "verify-2", "verify-3"]
    for reader in readers:
        runtime.config.roles.append(RoleConfig(name=reader, instance_id=reader, role_kind="reader", backend="mock"))
    policy = _policy()
    policy.update(mode="blocking", verify_assignment_mode="oa_run_plan")
    policy["pools"]["verify"].update(role_instances=readers, capacity=3)
    runtime.config.workflow.flow_metadata["task_pipeline"] = policy
    runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
    ids = [f"T-{i}" for i in range(8)]
    tasks = [Task(title=key, id=key, contract=TaskContract(behavior="First version", acceptance="succeeds")) for key in ids]
    runtime.task_store.add_many(tasks)
    revision = {t.id: effective_contract_revision(t) for t in tasks}
    _, prepared = _checkpoint(runtime, trigger, loaded)

    def adopt(prepared, subset):
        decision = _decision(prepared, role_ref=readers[0], source=prepared.context.source_manifest["sources"][0])
        unit = decision["run_plan"]["graph"]["work_units"][0]
        unit.update(stage_ids=["verify"], task_ids=subset, verification_scope={
            "acceptance_ids": ["*"], "required_evidence": ["replay the delivered target's acceptance test"]})
        decision["run_plan"]["delegation"][0].update(preferred_role_refs=readers, capability_refs=[])
        if len(subset) == len(ids):
            run_plan = decision["run_plan"]
            other = dict(unit, work_unit_id="other-verify", task_ids=[subset[-1]])
            unit["task_ids"] = subset[:-1]
            run_plan["graph"]["work_units"].append(other)
            run_plan["delegation"].append(dict(run_plan["delegation"][0], work_unit_id="other-verify"))
            first = run_plan["context_routes"][0]["required_sources"][0]
            second = next(s for s in prepared.context.source_manifest["sources"] if s["ref"] != first["ref"])
            run_plan["context_routes"].append(dict(run_plan["context_routes"][0], work_unit_id="other-verify",
                                                   required_sources=[{"ref": second["ref"], "sha256": second["sha256"]}]))
        result = apply_orchestrator_agent_decision(runtime, _submit(runtime, prepared, decision))
        assert result["status"] == "applied", result

    adopt(prepared, ids)
    from zf.runtime.orchestrator_agent_run_plan import admitted_context_route_sources

    def sources(task, stage):
        return [s["source_id"] for s in admitted_context_route_sources(state_dir=runtime.state_dir,
            workflow_run_id=loaded.workflow_run_id, task_map_generation=loaded.task_map_generation,
            plan_artifact_package_ref=loaded.plan_artifact_package_ref, plan_artifact_package_digest=loaded.plan_artifact_package_digest,
            task_id=task, stage_id=stage)]

    admitted = next(e for e in runtime.event_log.read_all() if e.type == "orchestrator.run_plan.admitted")
    runtime.event_writer.append(ZfEvent(type=admitted.type, actor="reader", origin="agent",
        correlation_id=admitted.correlation_id, payload={**admitted.payload, "execution_plan_ref": None}))
    assert sources(ids[0], "verify") == ["oa-route-discovery-1"]
    assert sources(ids[-1], "prd-lanes-verify") == ["oa-route-other-verify-1"]
    assert not sources(ids[0], "impl") and not sources("unassigned-task", "verify")
    generation = runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", origin="kernel",
        correlation_id=loaded.workflow_run_id, payload={"schema_version": "task-pipeline-generation.v1",
        "task_ids": ids, **vars(loaded)}))
    ctx = {**vars(loaded), "flow_kind": "prd", "generation_admitted_event_id": generation.id,
           "task_contract_revisions": revision}
    contexts = {i: ctx for i in ids}
    # Canonical Task contract changes on its own. The seven other signed decisions stay current.
    modified = runtime.task_store.get(ids[3])
    modified.contract.behavior = "Changed version"
    runtime.task_store.update(modified.id, contract=modified.contract)

    def reconcile():
        assignments = admitted_pipeline_delegations(runtime, generation_contexts=contexts, policy=policy)
        return TaskPipelineReconciler().reconcile(policy=policy, tasks=runtime.task_store.list_all(),
            operations=[_operation(i, "impl", "settled") for i in ids[:4]], attempts=[], oa_delegations=assignments)

    initial = reconcile()
    assert {r["task_id"] for r in initial["dispatchable"]["verify"]} == set(ids[:3])
    assert {r["role_instance"] for r in initial["dispatchable"]["verify"]} == set(readers)
    rows = {v["task_id"]: v for v in initial["tasks"]}
    assert rows[ids[3]]["blockers"] == [VERIFY_WAIT]
    events = runtime.event_log.read_all()
    for _ in range(2):
        request_verification_corrections(runtime, initial, contexts, events)
    requests = [e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.checkpoint.requested"]
    assert len(requests) == 2
    repair = prepared_operation_from_checkpoint_event(runtime, requests[-1])
    assert repair.context.input_body["checkpoint_context"]["verify_assignment_task"] == ids[3]
    assert any(s["source_id"] == "current-task-contract" for s in repair.context.source_manifest["sources"])
    # An independent delta Plan becomes current for the Run while the corrected Task retains generation 1.
    from tests.test_orchestrator_agent_run_plan import _revised_candidate
    _revised_candidate(runtime, prior_package=package, prior_ref=package_ref, revision="2", generation="map-2")
    adopt(repair, [ids[3]])
    repaired = reconcile()
    assert next(v for v in repaired["tasks"] if v["task_id"] == ids[3])["stage"] == "verify_ready"
    assignments = admitted_pipeline_delegations(runtime, generation_contexts=contexts, policy=policy)
    assert assignments[ids[3]]["verify"]["execution_plan_ref"] != assignments[ids[0]]["verify"]["execution_plan_ref"]
    assert repaired == reconcile()
    from zf.runtime.orchestrator_agent_reactor import OrchestratorAgentReactorMixin
    correction = next(e for e in reversed(runtime.event_log.read_all())
                      if e.type == "orchestrator.run_plan.admitted" and e.payload.get("verify_assignment_task"))
    runtime._orchestrator_checkpoint_source = lambda event: generation
    wakes = []
    def wake(rt, *, candidate_task_ids):
        wakes.extend(candidate_task_ids)
        return [], set(candidate_task_ids)
    monkeypatch.setattr("zf.runtime.task_pipeline_runtime.prepare_task_pipeline_dispatch", wake)
    OrchestratorAgentReactorMixin._on_orchestrator_run_plan_admitted(runtime, correction)
    assert wakes == [ids[3]]


def test_candidate_collects_only_frozen_oa_roster_and_rejects_other_approval(tmp_path, monkeypatch):
    from tests.test_candidate_reader_assignments import _assignments, _scoped_project
    from zf.runtime.call_result_envelope import canonical_json_sha256
    from zf.runtime.task_pipeline_candidate_readers import candidate_reader_stages

    runtime = _scoped_project(tmp_path, monkeypatch)
    event = runtime.event_log.read_all()[-1]
    meta = runtime.config.workflow.flow_metadata
    meta["stage_teams"] = {"candidate_verify": {"width": 2, "reason": "two independent checks"}}
    seats = ["reader-a", "reader-b", "reader-idle"]
    meta["task_pipeline"] = {"pools": {"verify": {"role_instances": seats}}}
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, role_kind="reader", backend="mock") for r in seats]
    assert candidate_reader_stages(runtime, event) == []
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    ident = {k: event.payload[k] for k in ("workflow_run_id", "task_map_generation", "plan_artifact_package_ref",
                                           "plan_artifact_package_digest")}
    digest = canonical_json_sha256(_assignments())
    plan = write_immutable_json_sidecar(runtime.state_dir,
        {"schema_version": "orchestrator-execution-plan.v1", "identity": ident,
         "candidate_reader_assignments_digest": digest}, root="fixtures/execution-plans",
        kind="orchestrator_execution_plan", schema_version="orchestrator-execution-plan.v1", created_by="test")
    binding = {"candidate_reader_assignments_digest": digest, "execution_plan_ref": plan}
    runtime.event_writer.append(ZfEvent(type="orchestrator.run_plan.admitted", origin="kernel", payload={
        **binding, **ident}))
    frozen = replace(event, payload={**event.payload, "candidate_reader_approval": binding})
    selected, = candidate_reader_stages(runtime, frozen)
    assert selected.roles == seats[:2]
    wrong = deepcopy(binding)
    wrong["execution_plan_ref"]["sha256"] = "b" * 64
    assert not candidate_reader_stages(runtime, replace(frozen, payload={**frozen.payload, "candidate_reader_approval": wrong}))


def test_judge_cannot_close_without_both_mandatory_signed_opinions(tmp_path):
    from tests.test_goal_judge_admissions import closure
    from tests.test_goal_candidate_batch import RUN_ID, GEN, REF, TARGET
    from zf.runtime.goal_judge_admissions import current_judge_admissions, JudgeAdmissionError

    _, events, _ = closure(tmp_path)
    source = next(e for e in events if e.id == "disc-source")
    reqs = tuple({"role": role, "recipient": "judge", "obligation_id": role} for role in ("ux", "api"))
    args = dict(run=RUN_ID, candidate_ref=REF, target=TARGET, generation=GEN, required_specialists=reqs)
    with pytest.raises(JudgeAdmissionError, match="specialist obligation"):
        current_judge_admissions(events, source, **args)
    start = next(e for e in events if e.id == "disc-start")
    before = next(i for i, e in enumerate(events) if e.id == "disc-agg")
    for member in reqs:
        role = member["role"]
        start.payload["expected_children"].append({"child_id": role, "role_instance": role,
                                                  "payload": {"stage_obligation": member}})
        votes = [deepcopy(next(e for e in events if e.id == idx)) for idx in ("disc-vote", "disc-adm", "disc-terminal")]
        for e in votes:
            e.id += "-" + role
            e.payload.update(child_id=role, role_instance=role, operation_id="op-" + role)
        votes[0].payload["semantic_submit_admission_event_id"] = votes[1].id
        votes[1].payload["source_event_id"] = votes[0].id
        votes[2].causation_id = votes[0].id
        events[before:before] = votes
    assert set(current_judge_admissions(events, source, **args)["discovery"]) == {"disc-adm", "disc-adm-ux", "disc-adm-api"}
    start.payload["expected_children"][-1]["payload"]["stage_obligation"] = {"role": "api", "obligation_id": "old"}
    with pytest.raises(JudgeAdmissionError, match="specialist obligation"):
        current_judge_admissions(events, source, **args)


@pytest.mark.parametrize("optional", [False, True])
def test_required_judge_specialists_dispatch_parallel_separately_from_formal_judge(assembly, tmp_path, optional):
    from tests.test_reader_fanout_runtime import _state
    from tests.test_workflow_registered_controller import _candidate
    from zf.core.config.loader import load_config

    members = [{"obligation_id": "check-" + role, "role": role, "question": "Review " + role,
                "input_refs": ["candidate-evidence"], "expected_output": "Signed finding"}
               for role in ("product-scan", "tech-scan")]
    members[0].update(selection_reason="Review the business risk.", completion_condition="Cover each open dispute.")
    if optional:
        members[-1]["required"] = False
    spec = assembly[3]
    spec["short_flow_spec"]["parameters"]["stage_teams"] = {
        "judge": {"author": "judge-prd", "reason": "Resolve both scope disputes", "members": members}}
    spec["requested_roles"] = ["judge-prd", "product-scan", "tech-scan"]
    _, _, path = _candidate(assembly)
    config = load_config(path)
    discovery = next(s for s in config.workflow.stages if s.id == "prd-post-verify-discovery")
    formal = next(s for s in config.workflow.stages if s.trigger == "flow.goal.closed")
    root = tmp_path / "discovery-run"
    root.mkdir()
    _, log, transport, runtime = _state(root)
    advisory = [s for s in config.workflow.stages if s.id == "prd-judge-advisory"]
    runtime.config.workflow.stages = [discovery, formal, *advisory]
    runtime.config.workflow.flow_metadata = config.workflow.flow_metadata
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader")
                            for r in [*discovery.roles, *formal.roles, *(r for s in advisory for r in s.roles)]]
    from tests import test_product_acceptance as product
    package, _ = product._package(runtime.state_dir)
    trigger = runtime.event_writer.append(ZfEvent(type="flow.discovery.requested", origin="kernel",
        correlation_id=product.RUN_ID, payload={"workflow_run_id": product.RUN_ID, "flow_kind": "prd",
        "target_commit": "a" * 40, "candidate_ref": "refs/heads/candidate", "task_map_generation": product.GENERATION,
        "plan_artifact_package_ref": package["ref"], "plan_artifact_package_digest": package["sha256"]}))

    runtime.run_once(events=[trigger])
    runtime.run_once(events=[trigger])
    assert sorted(row[0] for row in transport.sent) == sorted([*discovery.roles, *(r for s in advisory for r in s.roles)])
    assert formal.roles == ["judge-prd"] and "judge-prd" not in discovery.roles
    starts = [e for e in log.read_all() if e.type == "fanout.started"]
    assert {e.payload["trigger_event_id"] for e in starts} == {trigger.id}
    assert len(starts) == 1 + int(optional)
    if optional:
        assert advisory[0].roles == ["tech-scan"] and "tech-scan" not in discovery.roles
        assert not advisory[0].aggregate.success_event and not advisory[0].aggregate.failure_event
    by_role = {c["role_instance"]: c for start in starts for c in start.payload["expected_children"]}
    for member in members:
        assert by_role[member["role"]]["payload"]["result_semantics"] == "artifact_production"
        assert by_role[member["role"]]["payload"]["stage_obligation"] == {**member, "recipient": "judge-prd"}
        text = next(row[1] for row in transport.sent if row[0] == member["role"]).read_text()
        for key in ("selection_reason", "completion_condition"):
            if key in member:
                assert member[key] in text
        assert member["question"] in text and member["expected_output"] in text


@pytest.mark.parametrize("explicit_capacity", [False, True])
def test_two_candidate_seats_from_eight_leave_task_verify_capacity_intact(assembly, explicit_capacity):
    from tests.test_workflow_registered_controller import _candidate
    from zf.core.config.loader import load_config

    spec = assembly[3]["short_flow_spec"]["parameters"]
    if explicit_capacity:
        spec["candidate_verify_width"] = 8
    spec["stage_teams"] = {"candidate_verify": {"reason": "Two integration journeys", "width": 2}}
    _, _, path = _candidate(assembly)
    config = load_config(path)
    policy = config.workflow.flow_metadata["task_pipeline"]
    assert policy["candidate"]["verify_width"] == 2
    assert policy["candidate"]["require_oa_assignments"] is True
    assert policy["pools"]["verify"]["capacity"] == 8
    assert len(set(policy["pools"]["verify"]["role_instances"])) == 8
    plan = next(s for s in config.workflow.stages if s.id == "prd-plan")
    assert any("exactly 2 candidate_reader_assignments" in instruction for instruction in plan.criteria.instructions)


def test_oa_selects_one_authorized_alternate_judge(assembly):
    import yaml
    from zf.core.config.loader import ConfigError, load_config
    from tests.test_workflow_registered_controller import _candidate

    _, _, path = _candidate(assembly)
    docs = list(yaml.safe_load_all(path.read_text()))
    flow = next(doc["spec"] for doc in docs if doc["kind"] == "PrdFlow")
    config = next(doc["spec"] for doc in docs if doc["kind"] == "ZfConfig")
    flow["stageTeams"] = {"judge": {"author": "release-reviewer", "reason": "Inspect delivery evidence", "members": []}}
    role = {"name": "release-reviewer", "instance_id": "release-reviewer", "backend": "mock",
            "role_kind": "reader", "stages": ["judge"]}
    config["roles"].append(role)
    path.write_text(yaml.safe_dump_all(docs))
    compiled = load_config(path)
    assert compiled.workflow.flow_metadata["registered_judge_role"] == "judge-prd"
    assert next(s for s in compiled.workflow.stages if s.trigger == "flow.goal.closed").roles == ["release-reviewer"]
    role["stages"] = ["planning"]
    path.write_text(yaml.safe_dump_all(docs))
    with pytest.raises(ConfigError, match="Judge|judge"):
        load_config(path)


@pytest.mark.parametrize("approve", [False, True])
def test_pre_impl_requires_exact_candidate_roster_decision(tmp_path, monkeypatch, approve):
    from tests import test_product_acceptance as product
    from tests.test_candidate_reader_assignments import _assignments
    from zf.runtime.call_result_envelope import canonical_json_sha256
    from zf.runtime.orchestrator_agent_briefing import build_orchestrator_agent_operation_briefing
    from zf.runtime.orchestrator_agent_run_plan import current_run_plan_admission

    runtime, trigger, loaded, _, _ = _runtime(tmp_path)
    from tests import test_orchestrator_agent_run_plan as pre_impl
    loaded.workflow_run_id = product.RUN_ID
    loaded.task_map_generation = product.GENERATION
    monkeypatch.setattr(pre_impl, "RUN_ID", product.RUN_ID)
    trigger.correlation_id = product.RUN_ID
    trigger.payload["workflow_run_id"] = product.RUN_ID
    spec = product._spec()
    spec["candidate_reader_assignments"] = _assignments()
    monkeypatch.setattr(product, "_spec", lambda **kwargs: spec)
    package, _ = product._package(runtime.state_dir)
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, package_event_payload
    runtime.event_writer.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel",
        correlation_id=loaded.workflow_run_id, payload=package_event_payload(
            hydrate_plan_artifact_package(runtime.state_dir, package), package, status="admitted")))
    loaded.plan_artifact_package_id = package["package_id"]
    loaded.plan_artifact_package_ref = package["ref"]
    loaded.plan_artifact_package_digest = package["sha256"]
    runtime.config.workflow.flow_metadata["task_pipeline"] = {"candidate": {"require_oa_assignments": True}}
    _, prepared = _checkpoint(runtime, trigger, loaded)
    digest = canonical_json_sha256(_assignments())
    assert digest in build_orchestrator_agent_operation_briefing(state_dir=runtime.state_dir, prepared=prepared)
    decision = _decision(prepared)
    decision["run_plan"]["candidate_reader_assignments_digest"] = digest if approve else "b" * 64
    if not approve:
        from zf.runtime.result_submit import ResultSubmitError
        with pytest.raises(ResultSubmitError, match="Candidate Reader assignments digest"):
            _submit(runtime, prepared, decision)
        # The same lease remains usable after protocol correction.
        decision["run_plan"]["candidate_reader_assignments_digest"] = digest
    result = apply_orchestrator_agent_decision(runtime, _submit(runtime, prepared, decision))
    assert result["status"] == "applied"
    admitted = current_run_plan_admission(runtime.event_log.read_all(), workflow_run_id=loaded.workflow_run_id,
        task_map_generation=loaded.task_map_generation, plan_artifact_package_ref=package["ref"],
        plan_artifact_package_digest=package["sha256"])
    assert admitted.get("candidate_reader_assignments_digest") == digest


def _specialist_correction_runtime(tmp_path):
    from tests.test_task_pipeline_orchestration import _admitted
    from tests.test_read_only_dynamic_continuation import _config
    from zf.runtime.orchestrator_agent_run_plan import current_run_plan_admission

    runtime, policy, contexts, _ = _admitted(tmp_path)
    task_id = "TASK-1"
    context = contexts[task_id]
    run = context["workflow_run_id"]
    policy["verify_assignment_mode"] = "oa_run_plan"
    runtime.config.workflow.flow_metadata["task_pipeline"] = policy
    signer = RoleConfig(name="verify-1", role_kind="reader", backend="mock")
    runtime.config.roles.extend([signer, RoleConfig(name="researcher", role_kind="reader", backend="mock")])
    runtime.config.workflow.stages.append(_config().workflow.stages[0])
    runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
    runtime.task_store.add(Task(id=task_id, title="Verify target"))
    identity = {key: context[key] for key in ("workflow_run_id", "plan_artifact_package_ref",
                                              "plan_artifact_package_digest", "task_map_generation")}
    generation = runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", origin="kernel",
        correlation_id=run, payload={"schema_version": "task-pipeline-generation.v1", "task_ids": [task_id], **identity}))
    context["generation_admitted_event_id"] = generation.id
    prior = current_run_plan_admission(runtime.event_log.read_all(), **identity)["execution_plan_ref"]
    return runtime, policy, contexts, task_id, signer, prior


def test_specialist_incident_wakes_one_scoped_oa_with_reusable_opinion(tmp_path):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.call_result_runtime import workflow_operation_service
    from zf.runtime.oa_task_verify_assignment import request_specialist_correction
    from zf.runtime.result_submit import ResultSubmitError

    runtime, policy, contexts, task_id, signer, prior = _specialist_correction_runtime(tmp_path)
    context = contexts[task_id]
    run = context["workflow_run_id"]
    service = workflow_operation_service(runtime)
    def new_specialist(op_id):
        return service.ensure_operation(workflow_run_id=run, task_id=task_id, operation_id=op_id,
            operation_type="task_verify_specialist", request={"task_id": task_id, "purpose": op_id})
    failed = new_specialist("wop-failed-specialist")
    good = new_specialist("wop-successful-specialist")
    source = service.cancel(operation_id=failed.operation_id, request_hash=failed.request_hash,
                            workflow_run_id=run, task_id=task_id, reason="transport interrupted")
    result_ref = write_immutable_json_sidecar(runtime.state_dir, {"summary": "Independent API opinion"},
        root="test-results", kind="result", schema_version="result.v1", created_by="test")
    service.settle(operation_id=good.operation_id, request_hash=good.request_hash, workflow_run_id=run,
                   task_id=task_id, admitted_call_result_ref=result_ref)
    incident = {"operation_id": failed.operation_id, "source_event_id": source.id, "reason": "cancelled",
                "prior_execution_plan_ref": prior, "available_opinions": [
                    {"obligation_id": "api-review", "operation_id": good.operation_id}]}
    def ask():
        return request_specialist_correction(runtime, task_id=task_id, context=context, incident=incident,
                                             events=runtime.event_log.read_all())
    checkpoint = ask()
    assert checkpoint.operation_id == ask().operation_id
    assert len(runtime.event_log.query(type="orchestrator.semantic.checkpoint.requested")) == 2
    sources = {row["source_id"]: row for row in checkpoint.context.source_manifest["sources"]}
    assert sources["prior-opinion-api-review"]["sha256"] == result_ref["sha256"]
    assert {"current-task-contract", "specialist-incident"} <= sources.keys()
    decision = _decision(checkpoint, role_ref=signer.instance_id, source=sources["prior-opinion-api-review"])
    member = {"obligation_id": "api-review", "role": "researcher", "question": "Check public API",
              "input_refs": ["contract"], "expected_output": "Verification report", "reuse_operation_id": "wop-other"}
    decision["run_plan"]["graph"]["work_units"][0].update(task_ids=[task_id], stage_ids=["verify"],
        verification_scope={"acceptance_ids": ["*"], "required_evidence": ["API evidence"]}, specialists=[member])
    decision["run_plan"]["delegation"][0]["capability_refs"] = []
    with pytest.raises(ResultSubmitError, match="currently offered obligation"):
        _submit(runtime, checkpoint, decision)
    member["reuse_operation_id"] = good.operation_id
    assert apply_orchestrator_agent_decision(runtime, _submit(runtime, checkpoint, decision))["status"] == "applied"
    assert ask() is None
    actual = admitted_pipeline_delegations(runtime, generation_contexts=contexts, policy=policy)
    assert actual[task_id]["verify"]["specialists"] == [member]


def test_missing_specialist_slice_requests_one_oa_correction_for_the_bound_task(tmp_path):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting
    from zf.runtime.task_verify_specialists import specialist_inputs

    runtime, _, contexts, task_id, signer, prior = _specialist_correction_runtime(tmp_path)
    context = contexts[task_id]
    snapshot = write_immutable_json_sidecar(runtime.state_dir, {"task_id": task_id}, root="test-inputs",
        kind="contract", schema_version="task-contract.v1", created_by="kernel")
    member = {"obligation_id": "audit", "role": "researcher", "input_refs": ["contract:$.nonexistent"],
              "question": "Review the selected contract field", "expected_output": "finding"}
    assignment = {"specialists": [member], "preferred_roles": [signer.instance_id], "work_unit_id": "checks",
                  "contract_revision": effective_contract_revision(runtime.task_store.get(task_id)), "execution_plan_ref": prior}
    args = dict(context=context, task_id=task_id, target_commit="a" * 40, signer=signer, contract_ref=snapshot, generation=1)
    before = len(runtime.event_log.query(type="orchestrator.semantic.checkpoint.requested"))
    for _ in range(2):
        with pytest.raises(TaskPipelineWaiting) as waiting:
            specialist_inputs(runtime, assignment, **args)
        assert waiting.value.reason == "waiting_for_oa_specialist_correction"
    blocked, = runtime.event_log.query(type="workflow.operation.blocked")
    assert blocked.payload["failure_class"] == "specialist_contract_selection_absent"
    assert blocked.payload["contract_digest"] == snapshot["sha256"] and blocked.task_id == task_id
    checkpoints = runtime.event_log.query(type="orchestrator.semantic.checkpoint.requested")
    assert len(checkpoints) == before + 1
    checkpoint = prepared_operation_from_checkpoint_event(runtime, checkpoints[-1])
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    descriptor = next(ref for ref in checkpoint.context.source_manifest["sources"] if ref["source_id"] == "specialist-incident")
    incident = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    assert incident["source_event_id"] == blocked.id and "nonexistent" in incident["reason"]
    assert not runtime.event_log.query(type="workflow.invoke.requested")
    member["input_refs"] = ["contract:$.task_id"]
    with pytest.raises(TaskPipelineWaiting) as waiting:
        specialist_inputs(runtime, assignment, **args)
    assert waiting.value.reason == "waiting_for_specialist_evidence"
    assert len(runtime.event_log.query(type="workflow.invoke.requested")) == 1
