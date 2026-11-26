"""Live operator routes bind one granted team member to its published final reply."""
from dataclasses import replace

import yaml
from fastapi.testclient import TestClient
from tests.test_transport_stream_json import FakeResultMessage
from tests.test_workflow_registered_controller import assembly
from tests.test_workflow_session_excerpt import PUBLIC, _submitted_project, seed_member_session
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.runtime.transport import DispatchContext
from zf.web.server import create_app


def test_current_public_reply_has_separate_live_authorization(assembly, monkeypatch):
    base, state, _, _, writer = assembly
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-observer")
    ids = seed_member_session(assembly, answer=PUBLIC + " api_key=secret1234567890")
    config = load_config(base)
    def client(ro=False):
        return TestClient(create_app(state, config=config, project_root=base.parent, read_only=ro))
    c = client()
    path = "/api/projects/default/workflow-requests/" + ids["request"]
    graph = c.get(path + "/graph/structure").json()
    query = {"scope": graph["scope"], "generation": graph["generation"], "operation_id": ids["operation"],
        "attempt_id": ids["attempt"], "instance": ids["instance"], "obligation_id": "verification", "stage_id": "prd-lanes-impl"}
    headers = {"x-zf-web-token": "test-observer"}
    def post(**values):
        return c.post(path + "/member-session", json={**query, **values}, headers=headers)
    assert post().json() == {"available": True}
    observed = post(read=True)
    assert observed.status_code == 200 and PUBLIC in observed.text, observed.text
    assert "private instruction" not in observed.text and "secret1234567890" not in observed.text
    assert "no-store" in observed.headers["Cache-Control"]
    for suffix in ["/graph/structure", "/graph/node?node_id=workflow:stage:prd-lanes-impl", "/graph/execution", "/graph"]:
        view = c.get(path + suffix, headers={"X-ZF-Structure-Generation":graph["generation"]})
        assert view.status_code == 200 and PUBLIC not in view.text
    assert c.post(path+"/member-session",json=dict(query, read=True)).status_code == 403
    assert client(True).post(path+"/member-session",json=dict(query,read=True),headers=headers).status_code == 403
    for wrong in ({"scope":dict(query["scope"],historical=True)}, {"scope":dict(query["scope"],project_id="foreign")},
                  {"generation":"stale"}, {"instance":"verify-lane-0"}, {"operation_id":"op-T-A"},
                  {"attempt_id":"expired"}, {"stage_id":"prd-plan"}, {"obligation_id":"other"}):
        assert post(**wrong).status_code == 404
    history = c.get(path + "/graph/structure", params={"as_of": graph["scope"]["head_seq"]}).json()
    assert history["generation"] != graph["generation"] and post(generation=history["generation"]).status_code == 404
    original = base.read_text()
    base.write_text(original.replace("workflow_session_excerpts: true", "workflow_session_excerpts: false"))
    assert post().status_code == 404
    base.write_text(original)
    graph = c.get(path+"/graph/structure").json()
    query.update(scope=graph["scope"], generation=graph["generation"])
    assert post(read=True).status_code == 200
    writer.append(ZfEvent(type="workflow.operation.started",origin="kernel",actor="zf-cli",task_id="foreign",
        payload={"workflow_run_id":"another-run","operation_id":"new-operation","request_hash":"foreign-hash",
                 "role_instance":ids["instance"],"active_attempt_id":"new-attempt"}))
    # Re-reading at the NEW live cursor still cannot inherit the reused worker instance.
    graph = c.get(path+"/graph/structure").json()
    query.update(scope=graph["scope"], generation=graph["generation"])
    assert post(read=True).status_code == 404


def test_plan_expert_can_open_its_real_taskless_turn_and_reject_mixed_receipts(assembly, monkeypatch):
    from types import SimpleNamespace
    from zf.runtime.stream_turn_lifecycle import run_stream_turn
    from zf.runtime.workflow_session_excerpt import current_reply

    base, state, _, _, writer = assembly
    _submitted_project(assembly, True)
    assert yaml.safe_load((state / "kanban.json").read_text()) == []
    role = "product-scan"
    ctx = DispatchContext(run_id="req-1", operation_id="plan-op", attempt_id="plan-attempt",
        lease_id="plan-lease", dispatch_id="plan-dispatch", instance_id=role)
    op = {"workflow_run_id":ctx.run_id, "operation_id":ctx.operation_id, "role_instance":role, "request_hash":"plan-hash",
          "active_attempt_id":ctx.attempt_id, "dispatch_id":ctx.dispatch_id, "lease_id":ctx.lease_id,
          "provider_session_id":"plan-session", "parent_stage_id":"prd-scan"}
    def emit(kind, **p):
        return writer.append(ZfEvent(type=kind, origin="kernel", actor="zf-cli", correlation_id=ctx.run_id,
            payload={"workflow_run_id":ctx.run_id, **p}))
    emit("workflow.submit.accepted", request_id=ctx.run_id)
    emit("fanout.started", stage_id="prd-scan", fanout_id="scan", expected_children=[{"child_id":"experts-1",
        "role_instance":role,"payload":{"stage_obligation":{"obligation_id":"check-product-scan"}}}])
    emit("workflow.operation.requested",**op)
    emit("workflow.operation.started",**op)
    emit("fanout.child.dispatched", fanout_id="scan", child_id="experts-1", role_instance=role,
         operation_id=ctx.operation_id, attempt_id=ctx.attempt_id)
    run_stream_turn(state,ctx,"plan-session",lambda: ([FakeResultMessage(session_id="plan-session",result="Taskless reply.")],
        SimpleNamespace(value="ok")), publish_excerpt=True)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN","test-observer")
    c = TestClient(create_app(state,config=load_config(base),project_root=base.parent))
    path = "/api/projects/default/workflow-requests/req-1"
    web = c.get(path+"/graph/structure").json()
    query = {"scope":web["scope"],"generation":web["generation"],"stage_id":"prd-scan",
             "obligation_id":"check-product-scan","instance":role,"operation_id":ctx.operation_id,"attempt_id":ctx.attempt_id,
             "read":True}
    response=c.post(path+"/member-session",json=query,headers={"x-zf-web-token":"test-observer"})
    assert response.status_code == 200 and response.json()["text"] == "Taskless reply.", response.text

    events = writer.event_log.read_all()
    closed = events[-1]
    args = dict(run=ctx.run_id,operation=ctx.operation_id,attempt=ctx.attempt_id,role=role,read=True)
    assert current_reply(state,events[:-1],**args) is None
    for changes in ({"origin":"worker"}, {"task_id":"leaked"},
                    {"payload": {**closed.payload,"task_id":"leaked"}}, {"payload": {**closed.payload,"session_id":"wrong"}},
                    {"payload":{**closed.payload,"operator_excerpt_ref":{"ref":"artifacts/anything"}}}):
        assert current_reply(state,[*events[:-1],replace(closed,**changes)],**args) is None
    # A closed ticket whose bound immutable content was replaced cannot be read.
    (state/closed.payload["operator_excerpt_ref"]["ref"]).write_text("tampered")
    assert c.post(path+"/member-session",json=query,headers={"x-zf-web-token":"test-observer"}).status_code == 404
