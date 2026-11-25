import pytest
import yaml

from tests.test_workflow_registered_controller import _admit, _candidate, assembly
from zf.core.config.loader import ConfigError, load_config
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_synthesis import WorkflowSynthesisError


def member(role):
    return {"obligation_id": f"check-{role}", "role": role, "question": f"Investigate {role}.",
            "input_refs": ["prd.md"], "expected_output": f"Findings from {role}"}


def select(assembly, roles, author="planner"):
    plan = {"author": author, "members": [member(role) for role in roles], "reason": "Bound the plan risk."}
    synthesis = assembly[3]
    synthesis["short_flow_spec"]["parameters"]["stage_teams"] = {"plan": plan}
    synthesis["requested_roles"] = [author, *roles]
    return plan


@pytest.mark.parametrize("experts", [[], ["product-scan"], ["product-scan", "tech-scan", "source_researcher"]])
def test_plan_dispatches_only_the_selected_readers_and_one_author(assembly, experts):
    plan = select(assembly, experts)
    _, _, path = _candidate(assembly)
    config = load_config(path)
    assert config.workflow.flow_metadata["stage_teams"] == {"plan": plan}
    stages = {stage.id: stage for stage in config.workflow.stages}
    assert stages["prd-plan"].roles == ["planner"]
    assert stages["prd-plan"].aggregate.synth_role == "plan-critic"
    assert ("prd-scan" in stages) == bool(experts)
    assert config.workflow.kind_routes["prd"].pattern_id == ("prd-scan" if experts else "prd-plan")
    if experts:
        assert stages["prd-scan"].roles == experts
        assert stages["prd-plan"].trigger == "prd.scan.completed"
    else:
        assert stages["prd-plan"].trigger == "prd.requested"

    # Use the compiled stages and the actual Orchestrator. Only provider I/O
    # and provider-specific workdir activation are replaced with mock roles.
    from tests.test_reader_fanout_runtime import _state
    from zf.core.config.schema import RoleConfig
    root = path.parent / "runtime"
    root.mkdir()
    state_dir, log, transport, runtime = _state(root)
    runtime.config.workflow.stages = [s for s in config.workflow.stages if s.id in {"prd-scan", "prd-plan"}]
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader")
                            for r in [*experts, "planner", "plan-critic"]]
    trigger = EventWriter(log).append(ZfEvent(type="prd.requested", correlation_id="run-plan",
        payload={"workflow_run_id": "run-plan", "flow_kind": "prd", "pdd_id": "F-plan", "prd_ref": "prd.md"}))
    runtime.run_once(events=[trigger])
    assert sorted(sent[0] for sent in transport.sent) == sorted(experts or ["planner"])
    for role, briefing, _, _ in transport.sent:
        text = briefing.read_text()
        if experts:
            assert f"Investigate {role}." in text and f"Findings from {role} to planner" in text
        else:
            assert "plan-author" in text and "Plan/Task Map" in text
    runtime.run_once(events=[trigger])
    assert len(transport.sent) == len(experts or ["planner"])
    copy = EventWriter(log).append(ZfEvent(type=trigger.type, correlation_id="run-plan", payload=dict(trigger.payload)))
    runtime.run_once(events=[copy])
    assert len(transport.sent) == len(experts or ["planner"])

    if experts:
        from tests.test_reader_fanout_runtime import _manifest
        start = next(e for e in log.read_all() if e.type == "fanout.started")
        for child in _manifest(state_dir, start.payload["fanout_id"])["children"]:
            role = child["role_instance"]
            result = EventWriter(log).append(ZfEvent(type="prd.scan.child.completed", actor=role,
                correlation_id="run-plan", payload={
                    "fanout_id": start.payload["fanout_id"], "child_id": child["child_id"],
                    "role_instance": role, "run_id": child["run_id"], "status": "completed",
                    "report": {"child_id": child["child_id"], "status": "passed", "summary": f"{role} findings",
                               "findings": [], "recommendation": "approve"}}))
            runtime.run_once(events=[result])
        handoff = next(e for e in log.read_all() if e.type == "prd.scan.completed")
        runtime.run_once(events=[handoff])
        assert [r for r, *_ in transport.sent].count("planner") == 1
        results = handoff.payload["stage_member_results"]
        assert {r["obligation_id"] for r in results} == {"check-" + role for role in experts}
        assert all(r["result_event_id"] and r["recipient"] == "planner" for r in results)
        assert all(r["result_event_id"] in transport.sent[-1][1].read_text() for r in results)


@pytest.mark.parametrize("roles,error", [
    (["imaginary-reader"], "registered reader"),
    (["dev-lane-0"], "registered reader"),
    (["product-scan", "product-scan"], "distinct"),
])
def test_rejected_synthesis_cannot_dispatch_unregistered_writer_or_duplicate(assembly, roles, error):
    select(assembly, roles)
    with pytest.raises(WorkflowSynthesisError, match=error):
        _admit(assembly)


def test_author_may_be_oa_but_cannot_review_its_own_plan(assembly):
    select(assembly, [], author="orchestrator")
    _, _, path = _candidate(assembly)
    assert next(s for s in load_config(path).workflow.stages if s.id == "prd-plan").roles == ["orchestrator"]
    docs = list(yaml.safe_load_all(path.read_text()))
    flow = next(doc["spec"] for doc in docs if doc["kind"] == "PrdFlow")
    flow["stageTeams"]["plan"]["author"] = "plan-critic"
    path.write_text(yaml.safe_dump_all(docs))
    with pytest.raises(ConfigError, match="independent"):
        load_config(path)


def test_team_requires_each_role_be_explicitly_requested(assembly):
    select(assembly, ["tech-scan"])
    assembly[3]["requested_roles"] = ["planner"]
    with pytest.raises(WorkflowSynthesisError, match="requested_roles"):
        _admit(assembly)


def test_registered_task_verification_selects_an_existing_blocking_oa_checkpoint(assembly):
    assembly[3]["short_flow_spec"]["parameters"]["stage_teams"] = {
        "task_verify": {"mode": "oa_run_plan", "reason": "Bind independent verifiers to the admitted tasks."}}
    _, _, path = _candidate(assembly)
    config = load_config(path)
    assert config.workflow.orchestration.flow_policies["prd"].checkpoint_policies["pre_impl"] == "blocking"
    assert config.workflow.flow_metadata["task_pipeline"]["verify_assignment_mode"] == "oa_run_plan"
    docs = list(yaml.safe_load_all(path.read_text()))
    project = next(doc["spec"] for doc in docs if doc["kind"] == "ZfConfig")
    project["workflow"]["orchestration"]["flow_policies"]["prd"]["checkpoint_policies"]["pre_impl"] = "shadow"
    path.write_text(yaml.safe_dump_all(docs))
    with pytest.raises(ConfigError, match="blocking pre_impl"):
        load_config(path)


def test_multi_kind_preserves_shared_reader_and_namespaces_generated_planner(tmp_path):
    from zf.cli.flow import draft_multi_kind_project_spec

    docs = draft_multi_kind_project_spec(backend="mock", project_name="multi", project_root=tmp_path)
    prd = next(doc["spec"] for doc in docs if doc.get("kind") == "PrdFlow")
    project = next(doc["spec"] for doc in docs if doc.get("kind") == "ZfConfig")
    project.setdefault("roles", []).append({"name": "external-reader", "role_kind": "reader", "backend": "mock"})
    prd.update(topology="fanout", stageTeams={"plan": {"author": "planner", "reason": "Cover two risks.",
               "members": [member("product-scan"), member("external-reader")]}})
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all(docs))
    config = load_config(path)
    roster = config.workflow.flow_metadata_by_kind["prd"]["stage_teams"]["plan"]
    stage = next(s for s in config.workflow.stages if s.id == "prd-scan")
    assert stage.roles == ["prd-product-scan", "external-reader"]
    assert [m["role"] for m in roster["members"]] == stage.roles
    assert all(c.payload["stage_obligation"]["recipient"] == "prd-planner" for c in stage.children)
    assert roster["author"] == "prd-planner"
    assert not config.workflow.flow_metadata_by_kind["issue"].get("stage_teams")


def test_proposal_freezes_assignments_before_dispatch_and_rejects_new_config(assembly):
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_config_consumer import approved_config_consumer_error
    from zf.runtime.workflow_proposal import build_workflow_proposal

    base, state, _, _, writer = assembly
    plan = select(assembly, ["source_researcher"])
    outcome, request, path = _candidate(assembly)
    candidate_config = load_config(path)
    proposal, ref = build_workflow_proposal(state, request=request, base_config_path=base,
        candidate_config_path=path, synthesis_result_ref=outcome.result_ref, flow_kind="prd",
        writer=writer, preflight={"status": "GO", "blockers": []})
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    frozen = hydrate_sidecar_ref(state, proposal["effective_config_ref"]).payload
    assert frozen["request_revision"] == request["revision"]
    assert frozen["compiled_graph"]["stage_teams"]["plan"] == plan
    bound = {"workflow_proposal_ref": ref, "effective_config_ref": proposal["effective_config_ref"]}
    assert approved_config_consumer_error(state, candidate_config, bound) == ""
    assert "consumer_config_stale" in approved_config_consumer_error(state, load_config(base), bound)


def test_optional_coverage_transfer_requires_an_approved_predecessor_and_new_admission(assembly):
    import json
    from zf.runtime.workflow_requests import revise_workflow_request
    from zf.runtime.workflow_proposal import WorkflowProposalError, build_workflow_proposal

    base, state, _, _, writer = assembly
    plan = select(assembly, ["tech-scan"])
    plan["members"][0].update(required=False, coverage_owner="planner")
    outcome, request, old_path = _candidate(assembly)
    docs = list(yaml.safe_load_all(old_path.read_text()))
    original, old_ref = build_workflow_proposal(state, request=request, base_config_path=base,
        candidate_config_path=old_path, synthesis_result_ref=outcome.result_ref, flow_kind="prd",
        writer=writer, preflight={"status": "GO", "blockers": []})
    flow = next(doc["spec"] for doc in docs if doc["kind"] == "PrdFlow")
    transferred = flow["stageTeams"]["plan"]["members"][0]
    transferred["disposition"] = {"status": "reassigned", "reason": "Planner covers the documented interface.",
        "previous_run_id": "prior-run", "previous_proposal_digest": original["proposal_digest"]}
    successor = base.parent / "next.yaml"
    successor.write_text(yaml.safe_dump_all(docs))
    manifest = base.parent / "workflow-input-manifest.json"
    manifest.write_text(json.dumps({"schema_version": "workflow.input_manifest.v1", "request_id": request["request_id"],
        "kind": "prd", "project_id": "demo", "workflow_dir": str(base.parent),
        "workflow_input_manifest_ref": str(manifest), "artifact_refs": []}))
    fresh = revise_workflow_request(state, manifest, actor="operator", expected_revision=request["revision"],
        expected_requirement_digest=request["requirement_spec_digest"], confirm=True,
        constraints=["Planner assumes documented interface coverage."], acceptance=["Pass product verification."])
    compile_args = dict(request=fresh, base_config_path=successor, flow_kind="prd",
                        preflight={"status": "GO", "blockers": []})
    with pytest.raises(WorkflowProposalError, match="bound approved Run"):
        build_workflow_proposal(state, **compile_args)
    old_invoke = writer.append(ZfEvent(type="workflow.invoke.requested", origin="kernel", actor="zf-cli",
        correlation_id="prior-run", payload={"workflow_run_id": "prior-run",
            "workflow_proposal_ref": old_ref, "workflow_proposal_digest": original["proposal_digest"],
            "effective_config_ref": original["effective_config_ref"]}))
    writer.append(ZfEvent(type="workflow.invoke.accepted", origin="kernel", actor="zf-cli", causation_id=old_invoke.id,
        correlation_id="prior-run", payload={"workflow_run_id": "prior-run"}))
    proposal, new_ref = build_workflow_proposal(state, **compile_args)
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    config = load_config(successor)
    stages = {s.id: s for s in config.workflow.stages}
    assert "prd-scan" not in stages
    assert stages["prd-plan"].children[0].payload["transferred_obligations"] == [transferred]
    from types import SimpleNamespace
    from zf.runtime.plan_team_transfer import approved_current_transfers
    runtime = SimpleNamespace(state_dir=state, config=config, event_log=writer.event_log, event_writer=writer)
    trigger = ZfEvent(type="prd.requested", correlation_id="successor", payload={"workflow_run_id": "successor"})
    assert not approved_current_transfers(runtime, stages["prd-plan"], trigger)
    new_invoke = writer.append(ZfEvent(type="workflow.invoke.requested", origin="kernel", actor="zf-cli",
        correlation_id="successor", payload={"workflow_run_id": "successor", "workflow_proposal_ref": new_ref,
            "workflow_proposal_digest": proposal["proposal_digest"], "effective_config_ref": proposal["effective_config_ref"]}))
    writer.append(ZfEvent(type="workflow.invoke.accepted", origin="kernel", actor="zf-cli", causation_id=new_invoke.id,
        correlation_id="successor", payload={"workflow_run_id": "successor"}))
    assert approved_current_transfers(runtime, stages["prd-plan"], trigger)


@pytest.mark.parametrize("ref", ["../../outside", "/etc/example", "https://example.test/x", "file:secret"])
def test_source_refs_do_not_expand_access(assembly, ref):
    plan = select(assembly, ["tech-scan"])
    plan["members"][0]["input_refs"] = [ref]
    with pytest.raises(WorkflowSynthesisError, match="project-relative"):
        _admit(assembly)


def test_project_symlink_is_not_a_new_read_scope(assembly, tmp_path_factory):
    base = assembly[0]
    (base.parent / "outside").symlink_to(tmp_path_factory.mktemp("external"))
    plan = select(assembly, ["tech-scan"])
    plan["members"][0]["input_refs"] = ["outside/source.md"]
    from zf.runtime.workflow_proposal import WorkflowProposalError
    with pytest.raises(WorkflowProposalError, match="resolves outside the project"):
        _candidate(assembly)


@pytest.mark.parametrize("binding", ["valid", "missing_projection", "missing", "changed", "changed_path", "other_run"])
def test_admission_replan_reuses_only_its_bound_scan_or_dispatches_the_approved_team(assembly, binding):
    from tests.test_reader_fanout_runtime import _manifest, _state
    from zf.core.config.schema import RoleConfig

    select(assembly, ["tech-scan"])
    _, _, path = _candidate(assembly)
    config = load_config(path)
    root = path.parent / "team-replan"
    root.mkdir()
    state, log, transport, runtime = _state(root)
    runtime.config.workflow.stages = [s for s in config.workflow.stages if s.id in {"prd-scan", "prd-plan"}]
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader")
                            for r in ["tech-scan", "planner", "plan-critic"]]
    writer = runtime.event_writer
    run = "team-replan"
    identity = {"workflow_run_id": run, "flow_kind": "prd", "pdd_id": "F-plan",
                "prd_ref": "prd.md", "request_revision": 1, "requirement_spec_digest": "a" * 64}
    original = writer.emit("prd.requested", correlation_id=run, payload=identity)
    runtime.run_once(events=[original])

    def deliver_scan():
        scan = log.query(type="fanout.started")[-1]
        child, = _manifest(state, scan.payload["fanout_id"])["children"]
        result = writer.emit("prd.scan.child.completed", actor="tech-scan", correlation_id=run, payload={
            "fanout_id": scan.payload["fanout_id"], "child_id": child["child_id"], "role_instance": "tech-scan",
            "run_id": child["run_id"], "status": "completed",
            "report": {"child_id": child["child_id"], "status": "passed", "summary": "checked contract",
                       "findings": [], "recommendation": "approve"}})
        runtime.run_once(events=[result])
        handoff = log.query(type="prd.scan.completed")[-1]
        runtime.run_once(events=[handoff])
        return handoff

    original_scan = deliver_scan()
    plan = log.query(type="fanout.started")[-1]
    assert plan.payload["stage_id"] == "prd-plan"
    child, = _manifest(state, plan.payload["fanout_id"])["children"]
    submitted = writer.emit("prd.plan.child.completed", actor="planner", correlation_id=run, payload={
        "fanout_id": plan.payload["fanout_id"], "child_id": child["child_id"], "role_instance": "planner",
        "run_id": child["run_id"], "status": "completed",
        "report": {"child_id": child["child_id"], "status": "passed", "summary": "draft plan",
                   "findings": [], "recommendation": "approve"}})
    runtime.run_once(events=[submitted])
    candidate = dict(identity)
    if binding != "missing":
        candidate["fanout_id"] = plan.payload["fanout_id"]
    if binding == "missing_projection":
        (state / "fanouts" / original_scan.payload["fanout_id"] / "manifest.json").unlink()
    if binding == "changed":
        candidate["requirement_spec_digest"] = "b" * 64
    if binding == "changed_path":
        candidate["prd_ref"] = "other-prd.md"
    if binding == "other_run":
        writer.emit("fanout.started", correlation_id="other", payload={
            "stage_id": "prd-plan", "fanout_id": "foreign", "workflow_run_id": "other",
            "trigger_event_id": original_scan.id})
        candidate["fanout_id"] = "foreign"
    rejected = writer.emit("task_map.ready", correlation_id=run, payload={
        **candidate, "task_map_digest": "c" * 64, "task_map_ref": "rejected.json"})
    failure = writer.emit("prd.plan.failed", correlation_id=run, causation_id=rejected.id, payload={
        "workflow_run_id": run, "stage_id": "prd-plan", "failure_scope": "plan_admission",
        "trigger_event_id": rejected.id, "plan_admission_incident_id": "admission-rejected",
        "reason": "task map violates scope bounds"})
    assert runtime._on_reader_stage_failure_replan(failure).action == "dispatch"
    retry, = [e for e in log.read_all() if e.causation_id == failure.id]
    assert retry.payload["rework_of"] == rejected.id and retry.payload["rework_attempt"] == 1
    assert retry.payload["plan_admission_incident_id"] == "admission-rejected"
    assert runtime._on_reader_stage_failure_replan(failure) is None
    runtime.run_once(events=[retry])
    reuse = binding in {"valid", "missing_projection"}
    if reuse:
        assert retry.type == "prd.scan.completed"
        assert retry.payload["stage_member_results"] == original_scan.payload["stage_member_results"]
    else:
        assert retry.type == "prd.requested"
        assert "stage_member_results" not in retry.payload and "fanout_id" not in retry.payload
        assert transport.sent[-1][0] == "tech-scan"
        new_scan = deliver_scan()
        assert new_scan.payload["stage_member_results"] != original_scan.payload["stage_member_results"]
        assert new_scan.payload["rework_of"] == rejected.id
    runtime.run_once(events=[retry])
    assert [r for r, *_ in transport.sent].count("planner") == 2
    assert [r for r, *_ in transport.sent].count("tech-scan") == (1 if reuse else 2)
    assert not log.query(type="task.fanout.rejected")
