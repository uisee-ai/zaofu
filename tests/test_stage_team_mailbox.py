"""Only visible mailbox metadata reaches the read-only current and historical workbench."""
from argparse import Namespace
from io import BytesIO
import json
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from tests.test_stage_team_workbench import approve, get_team, plan_case
from tests.test_workflow_registered_controller import assembly
from zf.cli import workflow_team
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.runtime.assistance_mailbox import runtime_view
from zf.runtime.durable_mailbox import post
from zf.runtime.workflow_requests import mark_workflow_request
from zf.web.server import create_app


def seed_operator_mailbox(case):
    base, state, _, _, writer = case
    run = plan_case(case)
    approve(case, run)
    mark_workflow_request(state, run, status="submitted", actor="owner", run_id=run, writer=writer)
    cfg = load_config(base)
    cfg.runtime.durable_mailbox.enabled = True
    runtime = runtime_view(state, writer, cfg)
    binding = {"workflow_run_id": run, "role_instance": "sender", "operation_id": "op-send",
               "active_attempt_id": "send-1", "request_hash": "hash-1"}
    for event in ("workflow.operation.requested", "workflow.operation.started"):
        writer.append(ZfEvent(type=event, origin="kernel", actor="kernel", correlation_id=run, payload=binding))
    sender = {"workflow_run_id": run, "actor": "sender", "operation_id": "op-send", "attempt_id": "send-1"}
    target = {"mode": "obligation", "stage": "prd-scan", "obligation_id": "check-tech-scan"}
    cursor = len(writer.event_log.read_all())
    for visibility in ("operator_metadata", "restricted"):
        post(runtime, sender, request_id="attempt-" + visibility, message_id="mail-" + visibility,
             recipients=[target], kind="notice", text="private text " + visibility, visibility=visibility)
    return run, cursor, cfg


def test_current_historical_cli_metadata_and_body_isolation(assembly, monkeypatch):
    base, state, _, _, writer = assembly
    req, before, cfg = seed_operator_mailbox(assembly)
    last = writer.event_log.read_all()[-1]
    ref = last.payload["body_ref"]["ref"]
    client = TestClient(create_app(state, project_root=base.parent, config=cfg, read_only=True))
    path = f"/api/projects/default/workflow-requests/{req}/graph"
    def fetch(cursor):
        response = client.get(path + "/structure", params={"as_of": cursor})
        assert response.status_code == 200, response.text
        return response.json()
    snap = fetch(0)
    member = next(m for m in get_team(snap)["members"] if m["role"] == "tech-scan")
    assert member["instance"] == "" and member["mailbox"]["pending"] == 1
    assert member["mailbox"]["owner"] == "tech-scan"
    assert all("mailbox" not in m for m in get_team(fetch(before))["members"])
    assert "mail-restricted" not in json.dumps(snap) and ref not in json.dumps(snap)
    def get(request, timeout):
        url = urlsplit(request.full_url)
        response = client.get(url.path + "?" + url.query, headers=dict(request.header_items()))
        assert response.status_code == 200
        return BytesIO(response.content)
    monkeypatch.setattr(workflow_team, "urlopen", get)
    args = Namespace(base_url="http://testserver", project="default", request=req, as_of=0,
                     stage="prd-plan", generation=snap["generation"], task="", member="check-tech-scan")
    selected, = workflow_team.read_stage_teams(args)["stages"]
    assert selected["members"][0]["mailbox"] == member["mailbox"]
    for suffix in ("/api/events", "/api/events/" + last.id, "/api/artifacts/catalog"):
        response = client.get(suffix)
        assert response.status_code == 200
        assert ref not in response.text and "mail-restricted" not in response.text
    response = client.get("/api/projects/default/agent-session/raw-output", params={"ref": ref})
    assert response.status_code in {400, 403, 404} and "private text" not in response.text
    assert not any(e.type in {"workflow.mailbox.prepared", "workflow.mailbox.acknowledged"} for e in writer.event_log.read_all())


def test_worker_delivery_and_restarted_reader_receive_their_own_discovery_command(tmp_path):
    from types import SimpleNamespace
    from zf.core.config.durable_mailbox import DurableMailboxConfig
    from zf.runtime.mailbox_briefing import mailbox_hint
    from zf.runtime.reader_respawn_continuation import reader_continuation_guidance
    from zf.runtime.task_attempt_briefing import bind_task_attempt_to_briefing
    runtime = SimpleNamespace(state_dir=tmp_path,
                              config=SimpleNamespace(runtime=SimpleNamespace(durable_mailbox=DurableMailboxConfig())))
    assert mailbox_hint(runtime.config, tmp_path, "mine") == ""
    runtime.config.runtime.durable_mailbox.enabled = True
    attempt = {"workflow_run_id":"run", "operation_id":"op", "attempt_id":"mine", "instance_id":"worker",
               "dispatch_id":"dispatch", "lease_id":"lease"}
    hint = mailbox_hint(runtime.config, tmp_path, attempt["attempt_id"])
    file = tmp_path / "brief.md"
    file.write_text("Active task: T")
    bind_task_attempt_to_briefing(file, attempt, inbox_hint=hint)
    assert "mailbox inbox --attempt mine" in file.read_text()
    prompt = reader_continuation_guidance(attempt_id="other", backend="mock", cli="zf", runtime=runtime)
    assert "mailbox inbox --attempt other" in prompt and "mine" not in prompt


def test_unsent_questions_to_coordination_remain_visible_on_the_sender_after_handoff():
    from zf.runtime.stage_team_mailbox import member_mailbox
    row = {"kind": "question", "status": "claimed", "question_status": "claimed", "posted_ts": "2026-11-25",
           "owner": "orchestrator", "notification_status": "unknown", "recovery_owner": "run_manager"}
    member = {"stage":"prd-scan", "role":"tech-scan", "obligation_id":"tech", "history":[{"operation_id":"op", "attempt_id":"a"}]}
    result = member_mailbox(member, ({}, {("op","a"):[("sent",row)]}), "P", "")
    assert (result["sent_unanswered"], result["pending"], result["unanswered"]) == (1, 0, 0)
    assert result["recovery_owner"] == "run_manager"
