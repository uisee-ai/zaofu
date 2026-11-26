"""Operator views of task-bound evidence, corrected plans and ledger cursors."""
from dataclasses import replace
from argparse import Namespace
from io import BytesIO
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from tests.test_stage_team_workbench import approve, plan_case
from tests.test_workflow_registered_controller import assembly
from zf.cli import workflow_team
from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_run_plan import EXECUTION_PLAN_SCHEMA
from zf.web.server import create_app


def seed_task_receipts(case, run):
    _, state, _, _, writer = case
    ids = ["T-A","T-B"]
    TaskStore(state / "kanban.json").add_many([Task(id=t,title=t,status="in_progress") for t in ids])
    stage = "prd-lanes-impl"
    identity = {"workflow_run_id":run,"task_map_generation":"G1","plan_artifact_package_ref":"package",
                "plan_artifact_package_digest":"d"}
    def emit(kind, *, task=None, actor="orchestrator", cause=None, origin="kernel", **payload):
        return writer.append(ZfEvent(type=kind, origin=origin,actor=actor,task_id=task,causation_id=cause,
                                     correlation_id=run,payload={"workflow_run_id":run,**payload}))
    emit("workflow.submit.accepted",request_id=run)
    emit("task.pipeline.generation.admitted", schema_version="task-pipeline-generation.v1",
         stage_id=stage,flow_kind="prd",task_ids=ids,**identity)
    roles = ["verify-lane-0","verify-lane-1"]
    unit = {"work_unit_id":"verification","task_ids":ids,"stage_ids":["verify"],
            "verification_scope":{"required_evidence":["Check the workflow contract"]}}
    ref = write_immutable_json_sidecar(state, {"schema_version":EXECUTION_PLAN_SCHEMA,
        "identity":identity,"work_units":[unit],"delegation":[{"work_unit_id":"verification","preferred_role_refs":roles}]},
        root="plans",kind="execution_plan",schema_version=EXECUTION_PLAN_SCHEMA,created_by="kernel")
    emit("orchestrator.run_plan.admitted", execution_plan_ref=ref, **identity)
    for task, role in zip(ids,roles,strict=True):
        op = "op-" + task
        binding = {"operation_id":op,"request_hash":"hash-" + task,"task_map_generation":"G1",
                   "parent_stage_id":stage,"task_pipeline_stage":"verify","role_instance":role,"task_id":task,
                   "dispatch_id":"dispatch-"+task,"lease_id":"lease-"+task}
        emit("workflow.operation.requested", task=task, orchestration_assignment={
            "execution_plan_ref":ref,"work_unit_id":"verification","selected_role":role}, **binding)
        emit("workflow.operation.started",task=task,active_attempt_id="attempt-"+task,**binding)
        actor = {"workflow_run_id":run, "operation_id":op,"attempt_id":"attempt-"+task,"instance_id":role}
        audience = {"planner":None}
        emit("workflow.board.posted",task=task,actor="workflow-board",message_key="post-"+task,
             sender=actor,audience=audience,body_ref={"ref":"private.json"},text="private body")
        emit("worker.heartbeat",task=task,actor=role,origin="worker",operation_id=op,attempt_id="attempt-"+task)
        if task == "T-A":
            interrupt = emit("workflow.operation.interrupted",task=task,reason="transport lost",
                             source_attempt_id="attempt-"+task,**binding)
    return interrupt


def test_operator_reads_task_details_exact_instance_messages_and_wait_prefix(assembly, monkeypatch):
    req = plan_case(assembly)
    approve(assembly, req)
    interrupt = seed_task_receipts(assembly, req)
    base,state,*_ = assembly
    client = TestClient(create_app(state,project_root=base.parent,config=load_config(base),read_only=True))
    path = f"/api/projects/default/workflow-requests/{req}/graph"
    def snapshot(cursor=0):
        return client.get(path + "/structure", params={"as_of":cursor}).json()
    def read(snap, node, cursor=0):
        response=client.get(path + "/node",params={"as_of":cursor,"node_id":node},
                            headers={"X-ZF-Structure-Generation":snap["generation"]})
        assert response.status_code==200,response.text
        return response.json()["node"]
    snap = snapshot()
    task = read(snap, "task:T-A")
    stage,=task["data"]["team_stages"]
    observer,=stage["data"]["team"]["members"]
    assert observer["source_event_id"] == interrupt.id and observer["status"] == "suspended"
    assert observer["wait"]["reason"]=="transport lost" and observer["last_activity"]["kind"]=="heartbeat"
    assert [m["message_key"] for m in stage["data"]["team"]["communications"]] == ["post-T-A"]
    assert "private" not in str(task["data"]["team_stages"])
    before=observer["source_seq"]-1
    assert read(snapshot(before),"task:T-A",before)["data"]["team_stages"][0]["data"]["team"]["members"][0]["status"]=="running"
    cli_reads = []
    def get(request, timeout):
        url=urlsplit(request.full_url)
        cli_reads.append(url)
        response=client.get(url.path+"?"+url.query,headers=dict(request.header_items()))
        assert response.status_code==200
        return BytesIO(response.content)
    monkeypatch.setattr(workflow_team,"urlopen",get)
    args = Namespace(project="default",request=req,as_of=0,generation=snap["generation"],base_url="http://testserver",
                     stage="prd-lanes-impl",task="",member="",instance="verify-lane-1")
    team,=workflow_team.read_stage_teams(args)["stages"]
    assert [m["message_key"] for m in team["communications"]] == ["post-T-B"]
    assert [m["task_ids"] for m in team["members"]] == [["T-B"]]
    args.task="T-A"
    assert workflow_team.read_stage_teams(args)["stages"]==[]
    args.instance=""
    cli_reads.clear()
    task_team, = workflow_team.read_stage_teams(args)["stages"]
    assert [m["message_key"] for m in task_team["communications"]] == ["post-T-A"]
    assert len(cli_reads) == 2 and cli_reads[-1].path.endswith("/graph/node") and "task%3AT-A" in cli_reads[-1].query


def test_candidate_projection_loads_the_exact_approved_immutable_plan(tmp_path,monkeypatch):
    from tests.test_candidate_reader_assignments import _assignments,_scoped_project
    from zf.runtime.call_result_envelope import canonical_json_sha256
    from zf.web.projections.workflow_team_sources import read_stage_team_inputs
    runtime = _scoped_project(tmp_path,monkeypatch)
    raw = runtime.event_log.read_all()[-1]
    ident = {k:raw.payload[k] for k in ("workflow_run_id","task_map_generation","plan_artifact_package_ref","plan_artifact_package_digest")}
    digest=canonical_json_sha256(_assignments())
    plan = write_immutable_json_sidecar(runtime.state_dir,{"schema_version":EXECUTION_PLAN_SCHEMA,"identity":ident,
        "candidate_reader_assignments_digest":digest},root="plans",kind="plan",schema_version=EXECUTION_PLAN_SCHEMA,created_by="kernel")
    approval={"candidate_reader_assignments_digest":digest,"execution_plan_ref":plan}
    approved=ZfEvent(type="orchestrator.run_plan.admitted",origin="kernel",payload={**approval,**ident})
    frozen=replace(raw,payload={**raw.payload,"candidate_reader_approval":approval},origin="kernel")
    source={"scope":{"run_id":ident["workflow_run_id"]},"compiled":{"stage_teams":{"candidate_verify":{"width":2}},
        "nodes":[{"stage_id":"candidate","trigger":"candidate.ready","success_event":"test.passed"}]},
        "events": [(1,approved),(2,frozen)]}
    inputs,refs=read_stage_team_inputs(runtime.state_dir,source)
    assert [m["role"] for m in inputs["candidate"]["members"]]==["reader-a","reader-b"]
    assert plan in refs
    source["events"]=[(1,approved),(2,replace(frozen,payload={**frozen.payload,"candidate_reader_approval":{}}))]
    invalid,_=read_stage_team_inputs(runtime.state_dir,source)
    assert invalid["candidate"]["diagnostic"] and not invalid["candidate"].get("members")


def test_plan_reserve_only_joins_the_current_approved_fanout_after_its_evidence():
    from zf.runtime.stage_team_projection import project_stage_teams, team_summary

    timeline = []
    scope = {"request_id": "req", "run_id": "run", "revision": 1, "proposal_digest": "digest"}
    def emit(kind, origin="kernel", **p):
        event = ZfEvent(type=kind, origin=origin, payload={"workflow_run_id": "run", **p})
        timeline.append(event)
        return event

    def snapshot(end=None):
        def stage(name, members):
            return {"id": "workflow:stage:" + name, "data": {"stage_id": name, "definition": {
                "metadata": {"team_obligations": members}}}}
        nodes = [stage("prd-scan", []), stage("prd-plan", [{"role": "planner", "obligation_id": "plan-author"}])]
        source = {"scope": scope, "compiled": {"stage_teams": {"plan": {"author": "planner"}}},
                  "events": list(enumerate(timeline[:end], 1))}
        project_stage_teams({n["id"]: n for n in nodes}, source, {})
        return nodes[1]["data"]["team"]

    emit("workflow.request.proposed", request_id="req", revision=1, proposal_digest="digest")
    emit("workflow.request.approved", request_id="req", revision=1)
    emit("fanout.started", stage_id="prd-scan", fanout_id="scan")
    previous = emit("fanout.child.completed", fanout_id="scan", child_id="old")
    wrong = emit("fanout.child.completed", fanout_id="elsewhere")
    charter = {"role": "expert", "obligation_id": "cross-cutting", "question": "Who owns this edge?",
               "selection_reason": "new finding", "completion_condition": "record owner", "recipient": "planner"}
    declaration = {"stage_id": "prd-scan", "fanout_id": "scan", "child_id": "ex", "role_instance": "expert",
                   "workflow_proposal_digest": "digest", "evidence_event_id": previous.id, "member": charter}
    for mismatch in ({"evidence_event_id": wrong.id}, {"workflow_proposal_digest": "old"},
                     {"workflow_run_id": "foreign"}, {"origin": "worker"}):
        emit("fanout.plan.member.admitted", **(declaration | mismatch))
    dispatch = {"fanout_id": "scan", "child_id": "ex", "role_instance": "expert", "attempt_id": "stale"}
    emit("fanout.child.dispatched", **dispatch)
    baseline = len(timeline)
    admitted = emit("fanout.plan.member.admitted", **declaration)
    assert [m["role"] for m in snapshot(baseline)["members"]] == ["planner"]
    team = snapshot()
    added, = [m for m in team["members"] if m["role"] == "expert"]
    assert team["required_total"] == 2 and added["status"] == "ready" and not added["attempt_ids"]
    assert added["source_event_id"] == admitted.id and added["admission_evidence_id"] == previous.id
    assert added["selection_reason"] == charter["selection_reason"] and added["recipient"] == "planner"
    author, = [m for m in team["members"] if m["role"] == "planner"]
    assert author["wait"]["seq"] == baseline+1 and author["waiting_for"] == ["cross-cutting"]
    assert "selection_reason" not in str(team_summary(team))
    emit("fanout.child.dispatched", **(dispatch | {"attempt_id": "real"}))
    assert snapshot()["members"][-1]["attempt_ids"] == ["real"]
    emit("fanout.started", stage_id="prd-scan", fanout_id="replacement")
    assert [m["role"] for m in snapshot()["members"]] == ["planner"]


def test_expired_attempts_and_other_instances_cannot_supply_current_progress():
    from zf.runtime.stage_team_observations import receipt_observations, scoped_team
    from zf.runtime.stage_team_projection import _apply_operation

    op = {"operation_id": "op", "workflow_run_id": "run", "task_id": "task", "role_instance": "new",
          "active_attempt_id": "now", "redrive_count": 1, "request_hash": "hash", "status": "running",
          "started_event_id": "start", "recent_activity": [
              {"id": "stale", "type": "agent.usage", "payload": {"attempt_id": "old"}, "ts": "later"},
              {"id": "ambiguous", "type": "agent.usage", "payload": {}, "ts": "latest"}]}
    member = {"operation_ids": [], "attempt_ids": [], "history": [], "last_activity": {"event_id": "earlier"}}
    _apply_operation(member, op, {}, {}, detailed=False)
    assert "last_activity" not in member

    pulse = ZfEvent(type="worker.heartbeat", origin="worker", actor="new", task_id="task", correlation_id="run",
                    payload={"operation_id": "op", "attempt_id": "now"})
    invalid = [replace(pulse, id="former", actor="old"), replace(pulse, id="foreign", correlation_id="another"),
               replace(pulse, id="other-task", task_id="other"),
               replace(pulse, id="old-attempt", payload={**pulse.payload, "attempt_id": "old"})]
    receipts = [*invalid, pulse]
    evidence = receipt_observations(receipts, {"op": op}, {e.id: i for i, e in enumerate(receipts, 1)})
    assert [row["event_id"] for row in evidence["op"]] == [pulse.id]

    messages = [{"source_event_id": role, "sender": role, "recipient": "owner"} for role in ("old", "new")]
    filtered = scoped_team({"unbound_operation_ids": []}, [{"messages": messages}], instance="new")
    assert filtered["communications"] == filtered["members"][0]["messages"] == [messages[1]]
