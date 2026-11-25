"""The public snapshot keeps proposed, admitted, and observed work distinct."""

from argparse import Namespace
from io import BytesIO
import json
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from tests.test_prd_stage_teams import select
from tests.test_workflow_registered_controller import assembly, _candidate
from zf.cli import workflow_team
from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_proposal import build_workflow_proposal
from zf.runtime.workflow_requests import mark_workflow_request
from zf.web.server import create_app


def plan_case(assembly):
    base, state, _, _, writer = assembly
    (state / "kanban.json").write_text("[]")
    select(assembly, ["product-scan", "tech-scan"])
    outcome, req, candidate = _candidate(assembly)
    proposal, _ = build_workflow_proposal(state, request=req, base_config_path=base,
        candidate_config_path=candidate, synthesis_result_ref=outcome.result_ref, flow_kind="prd",
        writer=writer, preflight={"status": "GO", "blockers": []})
    assert proposal["approval_status"] == "approvable"
    return req["request_id"]


def approve(assembly, request):
    mark_workflow_request(assembly[1], request, status="approved", actor="owner", writer=assembly[4],
                          run_id=request, event_type="workflow.request.approved")


def get_team(graph):
    return next(n["data"]["team"] for n in graph["nodes"] if n["id"] == "workflow:stage:prd-plan")


def test_real_proposal_web_cli_and_historical_prefix(assembly, monkeypatch):
    base, state, _, _, writer = assembly
    req = plan_case(assembly)
    client = TestClient(create_app(state, project_root=base.parent, config=load_config(base), read_only=True))
    path = f"/api/projects/default/workflow-requests/{req}/graph"
    proposed = client.get(path + "/structure").json()
    members = get_team(proposed)
    assert members["required_total"] is None
    assert [m["role"] for m in members["members"]] == ["product-scan", "tech-scan", "planner"]
    assert {m["status"] for m in members["members"]} == {"proposed"}
    approve(assembly, req)
    current = client.get(path + "/structure").json()
    actual = get_team(current)
    assert actual["required_total"] == 3 and actual["admitted_count"] == 0
    assert [m["status"] for m in actual["members"]] == ["ready", "ready", "waiting_inputs"]
    assert all(not m["instance"] for m in actual["members"])
    def get(request, timeout):
        url = urlsplit(request.full_url)
        response = client.get(url.path + "?" + url.query, headers=dict(request.header_items()))
        assert response.status_code == 200, response.text
        return BytesIO(response.content)
    monkeypatch.setattr(workflow_team, "urlopen", get)
    args = Namespace(base_url="http://testserver", project="default", request=req, as_of=0,
                     stage="prd-plan", generation=current["generation"], task="", member="")
    result = workflow_team.read_stage_teams(args)
    assert result["scope"] == current["scope"]
    assert current["scope"]["project_id"] == "default"
    from zf.runtime.stage_team_projection import team_summary
    selected, = result["stages"]
    assert selected["stage"] == "prd-plan" and team_summary({k:v for k,v in selected.items() if k not in {"stage","stage_status"}}) == actual
    before = client.get(path + "/structure", params={"as_of": proposed["scope"]["source_seq"]}).json()
    assert get_team(before) == members
    assert client.get(path + "/execution", params={"as_of": 1},
                      headers={"X-ZF-Structure-Generation": result["generation"]}).status_code == 409
    unpublished = client.get(path + "/structure?as_of=1").json()
    assert not get_team(unpublished)["members"]
    assert not any(e.type == "fanout.child.dispatched" for e in writer.event_log.read_all())


def test_two_contributors_share_one_business_task_and_keep_failed_attempts_separate():
    from zf.runtime.stage_team_projection import project_stage_teams
    events = []
    def emit(kind, **payload):
        event = ZfEvent(type=kind, origin="kernel", payload=payload, id=f"e{len(events)}")
        events.append(event)
        return event
    scope = {"request_id":"q", "run_id":"r", "revision":1, "proposal_digest":"digest"}
    emit("workflow.request.proposed", request_id="q", revision=1, proposal_digest="digest")
    emit("workflow.request.approved", request_id="q", revision=1)
    plan = emit("orchestrator.run_plan.admitted", workflow_run_id="r", task_map_generation="g")
    ref = {"ref":"plan", "sha256":"1"}
    members = [{"role":role, "obligation_id":role, "pipeline_stage":"verify", "task_ids":["T"]} for role in ["a","b"]]
    source = {"compiled":{}, "scope":scope, "stage_team_inputs":{"pipeline":{
        "execution_plan_ref":ref, "members":members, "event_id":plan.id, "task_map_generation":"g"}}}
    ops = {}
    for role, status in [("a","settled"),("b","running")]:
        ops[role] = {"workflow_run_id":"r", "task_map_generation":"g", "task_id":"T", "role_instance":role,
                     "operation_id":role, "request_hash":"hash", "active_attempt_id":"now", "started_event_id":"start",
                     "task_pipeline_stage":"verify", "status":status, "compiled_stage_id":"pipeline",
                     "orchestration_assignment":{"execution_plan_ref":ref, "work_unit_id":role, "selected_role":role}}
    for role, attempt, run in [("b","old","r"), ("b","now","foreign"), ("a","now","r")]:
        emit("workflow.call.result.admitted", workflow_run_id=run, operation_id=role, request_hash="hash",
             attempt_id=attempt, control_result_ref={"ref":"v","sha256":"d"}, semantic_verdict="rejected")
    sender = {"operation_id":"a", "attempt_id":"now", "instance_id":"a", "workflow_run_id":"r"}
    receiver = {**sender, "operation_id":"b", "instance_id":"b"}
    post = emit("workflow.board.posted", workflow_run_id="r", sender=sender, audience={"b":receiver},
                body_ref={"ref":"private.json"}, text="private body")
    consumed = emit("workflow.board.seen", receiver=receiver)
    post.actor = consumed.actor = "workflow-board"
    consumed.causation_id = post.id
    source["events"] = list(enumerate(events, 1))
    nodes = {"workflow:stage:pipeline":{"id":"workflow:stage:pipeline","data":{"stage_id":"pipeline","definition":{}}}}
    project_stage_teams(nodes, source, ops)
    team = nodes["workflow:stage:pipeline"]["data"]["team"]
    assert (team["admitted_count"], team["required_total"]) == (1,2)
    assert team["verdicts"] == {"rejected":1}
    assert team["task_progress"] == {"verify":{"admitted":0,"total":1}}
    assert [m["status"] for m in team["members"]] == ["admitted","running"]
    assert team["members"][0]["messages"][0]["seen"] == consumed.ts
    assert team["members"][1]["messages"][0]["consumed"] == ""
    assert "private" not in json.dumps(team)


def test_first_real_dispatch_only_updates_bound_plan_member(assembly, tmp_path):
    from zf.core.config.schema import RoleConfig
    from zf.core.events.writer import EventWriter
    from zf.runtime.sdlc_graph import build_sdlc_graph
    from zf.runtime.workflow_graph_snapshot import compile_proposal_graph
    from tests.test_reader_fanout_runtime import _state
    select(assembly, ["product-scan"])
    config = load_config(_candidate(assembly)[2])
    root = tmp_path / "worker"
    root.mkdir()
    (root / "prd.md").write_text("Confirmed requirement.\n")
    _, log, _, runtime = _state(root)
    runtime.config.workflow.stages = [s for s in config.workflow.stages if s.id in {"prd-scan", "prd-plan"}]
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader")
                            for r in ("product-scan", "planner", "plan-critic")]
    trigger = EventWriter(log).append(ZfEvent(type="prd.requested", origin="kernel", correlation_id="r",
        payload={"workflow_run_id":"r","flow_kind":"prd","pdd_id":"F","prd_ref":"prd.md"}))
    runtime.run_once(events=[trigger])
    scoped = [ZfEvent(type=kind, origin="kernel", payload={"request_id":"q","revision":1,"proposal_digest":"digest"})
              for kind in ("workflow.request.proposed","workflow.request.approved")]
    scoped.extend(log.read_all())
    source = {"scope":{"request_id":"q","run_id":"r","revision":1,"proposal_digest":"digest",
                       "historical":False,"execution_started":True}, "request":{}, "requirement":{},
              "compiled":compile_proposal_graph(config, "prd"), "events":list(enumerate(scoped,1)),
              "diagnostics":[], "versions":[], "plans":[], "tasks":{}}
    graph = build_sdlc_graph(source, execution_only=True)
    members = get_team(graph)["members"]
    assert [(m["role"], m["instance"], m["status"]) for m in members] == [
        ("product-scan","product-scan","running"),("planner","","waiting_inputs")]
    receipt = next(e for e in scoped if e.type == "workflow.operation.started")
    assert members[0]["source_event_id"] == receipt.id and members[0]["attempt_count"] == 1
    full = get_team(build_sdlc_graph(source))
    assert full["members"][0]["operation_ids"] == [receipt.payload["operation_id"]]
    assert full["members"][0]["attempt_ids"] == [receipt.payload["active_attempt_id"]]


def test_frozen_readers_are_visible_before_dispatch_and_keep_task_slices(tmp_path, monkeypatch):
    from tests.test_candidate_reader_assignments import _scoped_project
    from zf.web.projections.workflow_team_sources import read_stage_team_inputs
    from zf.runtime.stage_team_projection import project_stage_teams
    runtime = _scoped_project(tmp_path, monkeypatch)
    events = runtime.event_log.read_all()
    freeze = events[-1]
    scope = {"request_id":"q", "revision":1, "proposal_digest":"q", "run_id":freeze.payload["workflow_run_id"]}
    stage = {"stage_id":"candidate", "node_id":"stage:candidate", "trigger":"candidate.ready",
             "success_event":"test.passed"}
    source = {"events":list(enumerate(events, 1)), "scope":scope, "compiled":{"nodes":[stage]}}
    source["stage_team_inputs"], dependencies = read_stage_team_inputs(runtime.state_dir, source)
    assert any(d.get("ref") == freeze.payload["plan_artifact_package_ref"] for d in dependencies)
    nodes = {"workflow:stage:candidate":{"id":"workflow:stage:candidate", "data":{"stage_id":"candidate", "definition":stage}}}
    project_stage_teams(nodes, source, {})
    team = nodes["workflow:stage:candidate"]["data"]["team"]
    assert team["required_total"] == 2 and team["admitted_count"] == 0
    assert team["freeze_id"] == freeze.payload["freeze_id"]
    assert {m["role"] for m in team["members"]} == {"reader-a","reader-b"}
    assert all(m["acceptance"] and m["status"] == "ready" and not m["instance"] for m in team["members"])


def test_real_verify_specialist_invocations_bind_one_task_to_two_independent_children(tmp_path, monkeypatch, capsys):
    import pytest
    from tests.test_task_verify_specialists import fixture
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting
    from zf.runtime.task_verify_specialists import specialist_inputs
    from zf.runtime.stage_team_projection import project_stage_teams
    from zf.runtime.workflow_operation import reduce_workflow_operations
    runtime, assignment, args = fixture(tmp_path)
    with pytest.raises(TaskPipelineWaiting):
        specialist_inputs(runtime, assignment, **args)
    for event in runtime.event_log.query(type="workflow.invoke.requested"):
        runtime.run_once(events=[event])
    for event in runtime.event_log.query(type="task.fanout.requested"):
        runtime.run_once(events=[event])
    events = runtime.event_log.read_all()
    approval = next(e for e in events if e.id == "E1")
    binding = {"event_id":approval.id, "execution_plan_ref":assignment["execution_plan_ref"], "task_map_generation":"G1"}
    members = [{**m, "work_unit_id":"U1","task_ids":["T-1"],"pipeline_stage":"verify","contract_revision":
                assignment["contract_revision"], "specialist":True} for m in assignment["specialists"]]
    inputs = {"events":list(enumerate(events,1)), "scope":{"run_id":"RUN","request_id":"req","revision":1},
              "compiled":{}, "stage_team_inputs":{"pipeline":{**binding, "members":members}}}
    nodes = {"workflow:stage:pipeline":{"id":"workflow:stage:pipeline","data":{"stage_id":"pipeline","definition":{}}}}
    project_stage_teams(nodes, inputs, reduce_workflow_operations(events, workflow_run_id="RUN"))
    team = nodes["workflow:stage:pipeline"]["data"]["team"]
    assert team["required_total"] == 2 and team["task_progress"]["verify"] == {"total":1,"admitted":0}
    assert {m["instance"] for m in team["members"]} == {"researcher","inspector"}
    assert all(m["operation_ids"] and m["status"] == "running" for m in team["members"])


    # A corrected assignment can reuse precisely one admitted call and retain the original attempt.
    from tests.e2e.test_agent_owned_verification_mock_e2e import _record_required_reads, _success_payload
    from pathlib import Path
    dispatch = runtime.event_log.query(type="fanout.child.dispatched")[0]
    payload = dispatch.payload["payload"]
    _record_required_reads(runtime.state_dir, payload)
    opinion = _success_payload(Path(dispatch.payload["briefing_path"]))
    opinion["report"] = {"status":"passed","summary":"Checked the contract","recommendation":"approve","findings":[]}
    submitted = runtime.event_writer.append(ZfEvent(type=payload["canonical_success_event"],
        actor=dispatch.payload["role_instance"], task_id="T-1", payload=opinion, correlation_id="RUN"))
    runtime.run_once(events=[submitted])
    invocation = next(e for e in events if e.type == "workflow.invoke.requested"
                      and e.payload["verify_specialist_obligation"]["role"] == dispatch.payload["role_instance"])
    reused = {**next(m for m in assignment["specialists"] if m["role"] == dispatch.payload["role_instance"]),
              "reuse_operation_id":invocation.payload["workflow_operation_id"]}
    successor = {**assignment, "work_unit_id":"replacement", "execution_plan_ref":{"ref":"p2","sha256":"2"*64},
                 "specialists":[reused]}
    corrected = runtime.event_writer.append(ZfEvent(type="orchestrator.run_plan.admitted", actor="orchestrator",
        origin="kernel", payload={"workflow_run_id":"RUN","task_map_generation":"G1","verify_assignment_task":"T-1",
                                  "execution_plan_ref":successor["execution_plan_ref"]}))
    assert len(specialist_inputs(runtime, successor, **args)) == 1
    current = runtime.event_log.read_all()
    inputs["events"] = list(enumerate(current,1))
    inputs["stage_team_inputs"]["pipeline"] = {**binding, "event_id":corrected.id,
        "execution_plan_ref":successor["execution_plan_ref"],
        "members":[{**members[0], **reused, "work_unit_id":successor["work_unit_id"]}]}
    project_stage_teams(nodes, inputs, reduce_workflow_operations(current, workflow_run_id="RUN"))
    updated = nodes["workflow:stage:pipeline"]["data"]["team"]
    adopted, = updated["members"]
    assert updated["admitted_count"] == 1 and adopted["reused"]
    assert adopted["attempt_ids"] == [dispatch.payload["attempt_id"]]
    assert adopted["original_work_unit_id"] == assignment["work_unit_id"] and adopted["source_operation_id"] == reused["reuse_operation_id"]
    assert updated["task_progress"]["verify"] == {"total":1,"admitted":0}
    monkeypatch.setattr(workflow_team, "read_stage_teams", lambda _: {"scope":inputs["scope"], "generation":"snapshot",
        "stages":[{**updated, "stage":"pipeline", "stage_status":"running"}]})
    assert workflow_team.run(Namespace(request="req", format="text", stage="pipeline", member="", task="T-1")) == 0
    output = capsys.readouterr().out
    assert "admitted (reused)" in output and adopted["source_result_event_id"] in output
    assert dispatch.payload["attempt_id"] in output and "tasks 0/1" in output


def test_withdrawn_specialist_keeps_reason_and_coverage_without_affecting_denominator():
    from zf.runtime.stage_team_projection import project_stage_teams
    scope = {"request_id":"q", "revision":2, "proposal_digest":"v2", "run_id":"current"}
    removed = {"role":"optional", "required":False, "obligation_id":"review-risk", "coverage_owner":"planner",
               "disposition":{"status":"reassigned","reason":"Plan author owns this check",
                              "previous_run_id":"previous","previous_proposal_digest":"v1"}}
    author = {"role":"planner", "obligation_id":"plan-author"}
    source = {"scope":scope, "compiled":{"stage_teams":{"plan":{"members":[removed],"author":"planner"}}},
              "events":[(i,ZfEvent(type=t, origin="kernel", payload={"revision":2,"request_id":"q","proposal_digest":"v2"}))
                        for i,t in enumerate(("workflow.request.proposed","workflow.request.approved"))]}
    nodes = {"workflow:stage:prd-plan":{"id":"workflow:stage:prd-plan","data":{"stage_id":"prd-plan",
             "definition":{"metadata":{"team_obligations":[author]}}}}}
    project_stage_teams(nodes, source, {})
    team = nodes["workflow:stage:prd-plan"]["data"]["team"]
    assert (team["required_total"],team["optional_count"]) == (1,1)
    withdrawn, = [m for m in team["members"] if m.get("required") is False]
    assert withdrawn["status"] == "reassigned" and withdrawn["owner"] == "planner"
    assert withdrawn["disposition"] == removed["disposition"]


def test_correction_projects_only_its_exact_admitted_specialist_opinion():
    from zf.runtime.stage_team_projection import project_stage_teams, team_summary
    from copy import deepcopy
    run, task = "r", "T"
    old_plan = {"ref":"old.plan", "sha256":"old"}
    new_plan = {"ref":"new.plan", "sha256":"new"}
    evidence = {"ref":"assessment.json", "sha256":"sig"}
    envelope = {"ref":"envelope.json", "sha256":"env"}
    id_fields = {"role":"reviewer", "obligation_id":"coverage", "task_map_generation":"g",
                 "contract_revision":"c", "task_id":task}
    job = {"role":"reviewer", "obligation_id":"coverage", "question":"Check AC", "input_refs":["contract"],
           "expected_output":"Review", "pattern_id":"check", "contract_revision":"c"}
    old = {**id_fields, **job, "target_commit":"head", "execution_plan_ref":old_plan, "work_unit_id":"old-unit"}
    def event(id, kind, **payload):
        return ZfEvent(id=id, type=kind, origin="kernel", actor="orchestrator", task_id=task, payload={"workflow_run_id":run, **payload})
    invocation = event("request", "workflow.invoke.requested", workflow_operation_id="parent",
                       workflow_operation_request_hash="parent-hash", verify_specialist_obligation=old,
                       target_ref="head", task_map_generation="g", task_id=task, pattern_id="check")
    admission = event("original", "workflow.call.result.admitted", operation_id="child", attempt_id="old-attempt",
                      request_hash="child-hash", envelope_ref=envelope, control_result_ref=evidence, semantic_verdict="passed")
    plan = event("choice", "orchestrator.run_plan.admitted", execution_plan_ref=new_plan, task_map_generation="g")
    reuse = event("reuse", "task.verify.specialist.reused", **id_fields, target_commit="head",
                 work_unit_id="new-unit", execution_plan_ref=new_plan, source_operation_id="parent",
                 source_result_event_id=admission.id, evidence_ref=evidence)
    reuse.causation_id = admission.id
    ops = {"parent":{"operation_id":"parent", "operation_type":"task_verify_specialist","task_id":task,
                     "status":"settled", "request_hash":"parent-hash","workflow_run_id":run,"admitted_call_result_ref":envelope},
           "child":{"operation_id":"child","parent_operation_id":"parent","status":"settled","task_id":task,
                    "role_instance":"reviewer","workflow_run_id":run,"admitted_call_result_ref":envelope,
                    "request_hash":"child-hash","active_attempt_id":"old-attempt","started_event_id":"start"}}
    member = {**job, "work_unit_id":"new-unit","task_ids":[task],"pipeline_stage":"verify",
              "specialist":True,"reuse_operation_id":"parent"}
    def projected(receipts, assignment=member, operations=ops):
        src={"scope":{"run_id":run,"request_id":"q","revision":2},"compiled":{},"events":list(enumerate(receipts,1)),
             "stage_team_inputs":{"verify":{"event_id":plan.id,"execution_plan_ref":new_plan,"task_map_generation":"g",
                                           "members":[assignment]}}}
        node = {"id":"workflow:stage:verify","data":{"stage_id":"verify","definition":{}}}
        project_stage_teams({node["id"]:node},src,operations)
        return node["data"]["team"]
    before = [invocation, admission, plan]
    assert projected(before)["admitted_count"] == 0
    team = projected([*before, reuse, reuse])
    assert team["admitted_count"] == 1
    row, = team["members"]
    assert row["source_event_id"] == reuse.id and row["source_result_event_id"] == admission.id
    assert row["operation_ids"] == ["child"] and row["attempt_ids"] == ["old-attempt"]
    assert row["source_operation_id"] == "parent" and row["instance"] == "reviewer"
    assert row["original_execution_plan_ref"] == old_plan and row["original_work_unit_id"] == "old-unit"
    assert row["evidence_ref"] == evidence and team_summary(team)["members"][0]["reused"]
    assert team["task_progress"]["verify"] == {"admitted":0,"total":1}
    for field in ("workflow_run_id","source_operation_id","source_result_event_id","role","obligation_id",
                  "task_id","task_map_generation","contract_revision","work_unit_id","target_commit"):
        forged = deepcopy(reuse)
        forged.payload[field] = "unrelated"
        assert projected([*before, forged])["admitted_count"] == 0, field
    for field, invalid in (("execution_plan_ref",old_plan),("evidence_ref",envelope)):
        forged = deepcopy(reuse)
        forged.payload[field] = invalid
        assert projected([*before, forged])["admitted_count"] == 0, field
    assert projected([invocation, plan, reuse, admission])["admitted_count"] == 0
    assert projected([*before, reuse], {**member,"question":"Different check"})["admitted_count"] == 0
    assert projected([*before, reuse], {**member,"reuse_operation_id":"different"})["admitted_count"] == 0
    changed = deepcopy(ops)
    changed["child"]["active_attempt_id"] = "unrelated-attempt"
    assert projected([*before, reuse], operations=changed)["admitted_count"] == 0
    changed_source = deepcopy(invocation)
    changed_source.payload.pop("pattern_id")
    assert projected([changed_source, admission, plan, reuse])["admitted_count"] == 0
    unrelated_cause = deepcopy(reuse)
    unrelated_cause.causation_id = "unrelated"
    assert projected([*before, unrelated_cause])["admitted_count"] == 0


def test_final_judge_separates_optional_advice_from_required_opinions():
    from zf.runtime.stage_team_projection import project_stage_teams

    def event(kind, **payload):
        return ZfEvent(type=kind, origin="kernel", payload={"workflow_run_id":"r", **payload})
    mandatory, advisory, final = "prd-post-verify-discovery", "prd-judge-advisory", "prd-lanes-final"
    signed = {"role":"completeness", "obligation_id":"coverage"}
    extra = {"role":"consultant", "obligation_id":"performance","required":False}
    nodes = {}
    for name, members in ((mandatory,[signed]), (advisory,[extra]), (final,[])):
        id = "workflow:stage:" + name
        nodes[id] = {"id":id,"data":{"stage_id":name, "definition":{"roles":["judge"] if name == final else [],
            "metadata":{"team_obligations":members}}}}
    events = [event("workflow.request.proposed", request_id="q", revision=1, proposal_digest="D"),
              event("workflow.request.approved", request_id="q", revision=1), event("fanout.started",
                stage_id=advisory, fanout_id="A", expected_children=[{"child_id":"c","role_instance":"consultant",
                                                                   "payload":{"stage_obligation":extra}}]),
              event("fanout.child.dispatched", fanout_id="A", child_id="c", role_instance="consultant", operation_id="op"),
              event("workflow.call.result.admitted", operation_id="op", attempt_id="a", request_hash="h",
                    control_result_ref={"ref":"advice","sha256":"d"}, semantic_verdict="rejected")]
    source = {"scope":{"request_id":"q","run_id":"r","revision":1,"proposal_digest":"D"},
              "compiled":{"stage_teams":{"judge":{"author":"judge"}}}, "events":list(enumerate(events,1))}
    ops = {"op":{"role_instance":"consultant","operation_id":"op","workflow_run_id":"r","task_id":"T",
                 "active_attempt_id":"a","request_hash":"h","started_event_id":"e","status":"settled","parent_stage_id":advisory}}
    project_stage_teams(nodes, source, ops)
    team = nodes["workflow:stage:" + final]["data"]["team"]
    assert (team["required_total"],team["admitted_count"],team["optional_count"],team["optional_admitted_count"]) == (2,0,1,1)
    assert team["verdicts"] == {} and team["optional_verdicts"] == {"rejected":1}
    author = next(m for m in team["members"] if m["role"] == "judge")
    assert author["status"] == "waiting_inputs" and author["waiting_for"] == ["coverage"]
    assert sorted(team["uncovered_obligations"]) == ["coverage","judge-author"]
    assert team["decisions"] == []

    # A second freeze on the same commit/generation retires the old Judge team activity.
    # Both frozen prefixes remain independently replayable.
    def freeze(id):
        e = event("candidate.ready", freeze_id=id, candidate_head_commit="same", task_map_generation="g")
        e.id = id
        return e
    first, second = freeze("first"), freeze("second")
    discovery = event("flow.discovery.requested", candidate_snapshot_event_id=first.id, target_commit="same")
    events[2].payload["trigger_event_id"] = discovery.id
    old_judge = event("judge.passed", candidate_event_id=first.id)
    before = [*events[:2], first, discovery, *events[2:], old_judge]
    stage = "candidate"
    def after(receipts, current):
        source["events"] = list(enumerate(receipts,1))
        source["stage_team_inputs"] = {stage:{"event_id":current.id,"freeze_id":current.id}}
        project_stage_teams(nodes, source, ops)
        return nodes["workflow:stage:" + final]["data"]["team"]
    assert [d["event_id"] for d in after(before,first)["decisions"]] == [old_judge.id]
    assert after(before,first)["optional_admitted_count"] == 1
    delayed = event("judge.passed", candidate_event_id=first.id)
    latest = after([*before, second, delayed], second)
    assert latest["optional_admitted_count"] == 0 and latest["decisions"] == []
    request = event("run.delivery.requested", candidate_event_id=second.id, target_commit="same")
    shipped = event("run.delivery.settled", target_commit="same")
    shipped.causation_id = request.id
    manual = event("ship.completed", source_commit="same")
    current = after([*before, second, request, shipped, delayed, manual], second)
    assert [d["event_id"] for d in current["decisions"]] == [shipped.id]
    assert [d["event_id"] for d in current["unbound_deliveries"]] == [manual.id]


def test_partial_generation_keeps_unaffected_active_task_and_replaces_only_changed_task(tmp_path):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.orchestrator_agent_run_plan import EXECUTION_PLAN_SCHEMA
    from zf.runtime.stage_team_projection import project_stage_teams, team_summary
    from zf.web.projections.workflow_team_sources import read_stage_team_inputs

    events = []
    def generation(run, number, tasks):
        identity = {"workflow_run_id":run, "plan_artifact_package_ref":"package-" + number,
                    "plan_artifact_package_digest":"package-hash-" + number,"task_map_generation":number}
        def emit(kind, **payload):
            event = ZfEvent(type=kind, origin="kernel", payload={**identity, **payload})
            events.append(event)
            return event
        emit("task.pipeline.generation.admitted", schema_version="task-pipeline-generation.v1",
             stage_id="pipeline", flow_kind="prd", task_ids=tasks)
        unit = {"work_unit_id":"verification", "task_ids":tasks, "stage_ids":["verify"]}
        delegation = {"work_unit_id":"verification","preferred_role_refs":["reviewer"]}
        ref = write_immutable_json_sidecar(tmp_path, {"schema_version":EXECUTION_PLAN_SCHEMA,
            "identity":identity,"work_units":[unit],"delegation":[delegation]}, root="plans",
            kind="execution_plan",schema_version=EXECUTION_PLAN_SCHEMA,created_by="orchestrator")
        emit("orchestrator.run_plan.admitted",execution_plan_ref=ref)
        return ref
    initial = generation("run","G1",["A","B"])
    cutoff = len(events)
    revised = generation("run","G2",["B"])
    generation("other","unrelated",["C"])
    operations = {}
    for task in ("A","B"):
        operations[task] = {"operation_id":task,"workflow_run_id":"run","task_map_generation":"G1",
            "task_id":task,"role_instance":"reviewer","task_pipeline_stage":"verify","started_event_id":"started",
            "status":"running","active_attempt_id":task + "-attempt","request_hash":"hash",
            "orchestration_assignment":{"execution_plan_ref":initial,"work_unit_id":"verification","selected_role":"reviewer"}}
    def snapshot(prefix):
        scope = {"workflow_run_id":"run","run_id":"run","request_id":"req","revision":1}
        source = {"events":list(enumerate(prefix,1)), "scope":scope, "compiled":{"nodes":[]}}
        source["stage_team_inputs"], refs = read_stage_team_inputs(tmp_path, source)
        stage = {"id":"workflow:stage:pipeline","data":{"stage_id":"pipeline","definition":{}}}
        project_stage_teams({stage["id"]:stage}, source, operations)
        return stage["data"]["team"], refs

    prior, before_refs = snapshot(events[:cutoff])
    assert [m["status"] for m in prior["members"]] == ["running","running"] and before_refs == [initial]
    latest, refs = snapshot(events)
    assert {m["task_ids"][0]:m["status"] for m in latest["members"]} == {"A":"running","B":"ready"}
    assert {m["task_ids"][0]:m["binding"]["execution_plan_ref"] for m in latest["members"]} == {"A":initial,"B":revised}
    assert latest["task_progress"]["verify"] == {"admitted":0,"total":2}
    assert refs == [initial,revised]
    assert {m["task_ids"][0]:m["task_map_generation"] for m in team_summary(latest)["members"]} == {"A":"G1","B":"G2"}


def test_new_run_generation_waits_for_its_own_freeze_before_showing_judge_receipts():
    from zf.runtime.stage_team_projection import project_stage_teams

    def event(kind, **p):
        return ZfEvent(type=kind,origin="kernel",correlation_id="run",payload={"workflow_run_id":"run",**p})
    def gen(n):
        return event("task.pipeline.generation.admitted",schema_version="task-pipeline-generation.v1",
                     task_map_generation=n,task_ids=["B"])
    def freeze(n):
        return event("candidate.ready",schema_version="candidate-freeze-receipt.v1",
                     task_map_generation=n,freeze_id="freeze-" + n,candidate_head_commit="same")
    def judge(candidate):
        return event("judge.passed",candidate_event_id=candidate.id)
    def decisions(receipts):
        node = {"id":"workflow:stage:prd-lanes-final","data":{"stage_id":"prd-lanes-final","definition":{}}}
        project_stage_teams({node["id"]:node}, {"scope":{"run_id":"run","request_id":"q","revision":1},
            "events":list(enumerate(receipts,1)), "compiled":{"stage_teams":{"judge":{"author":"J"}}}}, {})
        return [r["event_id"] for r in node["data"]["team"]["decisions"]]
    first = freeze("G1")
    old = judge(first)
    before = [gen("G1"), first, old]
    assert decisions(before) == [old.id]
    waiting = [*before,gen("G2"),judge(first)]
    assert decisions(waiting) == []
    second = freeze("G2")
    updated = judge(second)
    assert decisions([*waiting,second,updated]) == [updated.id]
