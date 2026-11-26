from concurrent.futures import ThreadPoolExecutor
from shutil import copytree

import pytest

from tests.test_prd_stage_teams import member, select
from tests.test_reader_fanout_runtime import _state
from tests.test_workflow_registered_controller import _candidate, assembly
from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_proposal import build_workflow_proposal


def test_controlled_plan_extension_binds_current_report_and_resumes_in_original_run(assembly, tmp_path, monkeypatch):
    base, state, _, synthesis, _ = assembly
    initial = ["product-scan", "tech-scan"]
    reserve = "source_researcher"
    plan = select(assembly, initial)
    plan["reserve_roles"] = [reserve]
    synthesis["requested_roles"].append(reserve)
    outcome, request, path = _candidate(assembly)
    config = load_config(path)
    proposal, ref = build_workflow_proposal(state, request=request, base_config_path=base,
        candidate_config_path=path, synthesis_result_ref=outcome.result_ref, flow_kind="prd",
        preflight={"status": "GO", "blockers": []})
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "prd.md").write_text("Product specification and its cross-cutting risks.")
    runtime_state, log, transport, runtime = _state(root)
    copytree(state, runtime_state, dirs_exist_ok=True)
    runtime.config.workflow.flow_metadata = config.workflow.flow_metadata
    runtime.config.workflow.stages = [s for s in config.workflow.stages if s.id in {"prd-scan", "prd-plan"}]
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader")
                            for r in [*initial, reserve, "planner", "plan-critic"]]
    from zf.runtime.result_submit import provision_role_submit_credential
    for role in runtime.config.roles:
        provision_role_submit_credential(runtime_state, role.instance_id)
    writer = runtime.event_writer
    run = "run-reserved-seat"
    def record(kind, payload, *, actor="zf-cli", origin="kernel", cause=None):
        return writer.append(ZfEvent(type=kind, payload=payload, correlation_id=run, actor=actor,
                                     origin=origin, causation_id=cause))
    invoke = record("workflow.invoke.requested", {"workflow_run_id":run, "workflow_proposal_ref":ref,
        "workflow_proposal_digest":proposal["proposal_digest"], "effective_config_ref":proposal["effective_config_ref"]})
    record("workflow.invoke.accepted", {"workflow_run_id":run}, cause=invoke.id)
    trigger = record("prd.requested", {"workflow_run_id":run, "flow_kind":"prd", "pdd_id":"F-ext", "prd_ref":"prd.md"})
    runtime.run_once(events=[trigger])
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    scan = started.payload["fanout_id"]
    def complete(role):
        from tests.test_plan_team_inputs import submit_plan_expert
        dispatch = next(e for e in log.query(type="fanout.child.dispatched") if e.payload["role_instance"] == role)
        submit_plan_expert(runtime, dispatch, {"child_id":dispatch.payload["child_id"], "status":"passed",
            "summary":role+" found a boundary", "findings":[], "recommendation":"approve"})
    complete(initial[0])
    evidence = next(e for e in log.read_all() if e.type == "fanout.child.completed")
    service = ControlledActionService(runtime_state, writer, config=runtime.config, project_root=root, actor="operator")
    contract = member(reserve) | {"selection_reason":"Investigate the reported boundary",
                                 "completion_condition":"Identify ownership and affected consumers"}
    data = {"stage_id":"prd-scan", "workflow_run_id":run, "fanout_id":scan, "plan_member":contract,
            "workflow_proposal_digest":proposal["proposal_digest"], "evidence_event_id":evidence.id}
    def submit(body):
        action_request = record("runtime.action.requested", {"action":"request-fanout"})
        result = service.execute(action="request-fanout", requested_action="request-fanout",
                                 requested=action_request, payload=body)
        assert result["ok"], result
        return next(e for e in log.read_all() if e.id == result["event_id"])
    bad = [data | {"workflow_proposal_digest":"bad"}, data | {"workflow_run_id":"other-run"},
           data | {"evidence_event_id":trigger.id},
           data | {"plan_member":contract | {"input_refs":["undeclared.md"]}},
           data | {"plan_member":contract | {"role":"planner"}}]
    for body in bad:
        request_event = submit(body)
        runtime.run_once(events=[request_event])
        assert any(e.type == "fanout.plan.extension.rejected" and e.causation_id == request_event.id
                   for e in log.read_all())
    forged_payload = {k: v for k, v in data.items() if k != "plan_member"} | {"member": contract}
    fabricated = record("fanout.plan.extension.requested", forged_payload, actor=reserve, origin="worker", cause=trigger.id)
    runtime.run_once(events=[fabricated])
    assert not log.query(type="fanout.plan.member.admitted")
    manifest_path = runtime_state / "fanouts" / scan / "manifest.json"
    pre_admission = manifest_path.read_bytes()
    with ThreadPoolExecutor(max_workers=2) as clients:
        valid, repeated = clients.map(submit, [data, data])
    assert repeated.id == valid.id
    # Interrupt exactly between durable membership admission and dispatch.
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_dispatch_reader_fanout_child", lambda **kw: False)
        runtime.run_once(events=[valid])
    admitted = [e for e in log.read_all() if e.type == "fanout.plan.member.admitted"]
    assert len(admitted) == 1, [(e.type,e.payload.get("reason")) for e in log.read_all() if e.causation_id == valid.id]
    assert not any(role == reserve for role, *_ in transport.sent)
    from zf.runtime.orchestrator import Orchestrator
    manifest_path.write_bytes(pre_admission)
    runtime = Orchestrator(runtime_state, runtime.config, transport)
    runtime._reconcile_reader_fanout_triggers()
    duplicate = submit(data)
    assert duplicate.id == admitted[0].id
    runtime.run_once(events=[valid, duplicate])
    runtime._reconcile_reader_fanout_triggers()
    assert [role for role, *_ in transport.sent].count(reserve) == 1
    assert contract["selection_reason"] in next(p for r,p,*_ in transport.sent if r == reserve).read_text()
    complete(initial[1])
    assert not any(e.type == "prd.scan.completed" for e in log.read_all())
    complete(reserve)
    handoff = next(e for e in log.read_all() if e.type == "prd.scan.completed")
    runtime.run_once(events=[handoff])
    assert {m["obligation_id"] for m in handoff.payload["stage_member_results"]} == {
        "check-" + r for r in [*initial, reserve]}
    assert [role for role, *_ in transport.sent].count("planner") == 1
    late = submit(data | {"plan_member": contract | {"obligation_id": "late-change"}})
    runtime.run_once(events=[late])
    assert any(e.type == "fanout.plan.extension.rejected" and e.causation_id == late.id for e in log.read_all())


@pytest.mark.parametrize("reserve,declared,diagnosis", [
    ("product-scan", True, "distinct"), ("plan-critic", True, "independent"),
    ("imaginary", True, "registered reader"), ("source_researcher", False, "requested_roles"),
])
def test_unapproved_reserve_never_reaches_dispatch(assembly, reserve, declared, diagnosis):
    from zf.runtime.workflow_synthesis import WorkflowSynthesisError

    plan = select(assembly, ["product-scan"])
    plan["reserve_roles"] = [reserve]
    if declared:
        assembly[3]["requested_roles"].append(reserve)
    with pytest.raises((WorkflowSynthesisError, ValueError), match=diagnosis):
        _candidate(assembly)
