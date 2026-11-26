"""A new PRD Run derives its public notice audience from the approved team."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from tests.test_prd_stage_teams import member
from tests.test_reader_fanout_runtime import _RecordingTransport
from tests.test_workflow_proposal import _request
from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
from zf.runtime.board_liveness import board_silence_attention
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.run_contract import (
    bind_run_contract_workflow_artifacts,
    build_run_contract,
    write_run_contract_snapshot,
)
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.workflow_proposal import build_workflow_proposal
from zf.runtime.workflow_requests import mark_workflow_request

pytestmark = pytest.mark.mock_e2e


def open_prd_run(tmp_path, readers):
    config_path = tmp_path / "zf.yaml"
    flow = {
        "scanRoles": readers,
        "lanes": 1,
        "backend": "mock",
        "topology": "fanout",
        "prdRef": "prd.md",
        "deliveryPolicy": "report_only",
        "runBoard": {"enabled": True, "silenceSeconds": 300},
        "stageTeams": {
            "plan": {
                "author": "planner",
                "reason": "Review two distinct risks",
                "members": [member(role) for role in readers],
            }
        },
    }
    config_path.write_text(
        yaml.safe_dump_all(
            [
                {"apiVersion": "zaofu.dev/v1", "kind": "PrdFlow", "metadata": {"name": "prd"}, "spec": flow},
                {
                    "apiVersion": "zaofu.dev/v1",
                    "kind": "ZfConfig",
                    "metadata": {"name": "demo"},
                    "spec": {"version": "1.0", "project": {"name": "demo", "state_dir": ".board-state"}},
                },
            ]
        )
    )
    (tmp_path / "prd.md").write_text("Confirm the user flow and the implementation boundary.")
    config = load_config(config_path)
    state = tmp_path / config.project.state_dir
    request = _request(state, requirement_fields={"kind": "prd", "target_root": ".", "source_ref": "prd.md"})
    transport = _RecordingTransport()
    runtime = Orchestrator(state, config, transport)
    source_manifest = tmp_path / "manifest.json"
    source_manifest.write_text('{"kind": "prd", "source_ref": "prd.md"}')
    credentials = {}
    for role in readers:
        provision_role_submit_credential(state, role)
        credentials[role] = provision_role_artifact_read_credential(
            state, role, role_name=role, provider="mock"
        )
    proposal, ref = build_workflow_proposal(
        state,
        request=request,
        base_config_path=config_path,
        flow_kind="prd",
        writer=runtime.event_writer,
        preflight={"status": "GO", "blockers": []},
    )
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    run = "fresh-board-run"
    for status in ["approved", "submitted"]:
        mark_workflow_request(
            state,
            request["request_id"],
            status=status,
            actor="owner",
            writer=runtime.event_writer,
            run_id=run,
            event_type="workflow.request." + status,
        )
    contract = bind_run_contract_workflow_artifacts(
        build_run_contract(config, config_path=config_path),
        proposal_ref=ref,
        proposal_digest=proposal["proposal_digest"],
        effective_config_ref=proposal["effective_config_ref"],
    )
    snapshot = write_run_contract_snapshot(state, contract)
    invoke = runtime.event_writer.append(
        ZfEvent(
            type="workflow.invoke.requested",
            actor="zf-cli",
            task_id="TASK-BOARD",
            correlation_id=run,
            payload={
                "request_id": request["request_id"],
                "request_revision": request["revision"],
                "pattern_id": "prd-scan",
                "flow_kind": "prd",
                "workflow_run_id": run,
                "workflow_input_manifest_ref": str(source_manifest),
                "workflow_generation": proposal["proposal_digest"],
                "workflow_proposal_ref": ref,
                "workflow_proposal_digest": proposal["proposal_digest"],
                "effective_config_ref": proposal["effective_config_ref"],
                "effective_config_digest": proposal["effective_config_ref"]["sha256"],
                "run_contract_ref": snapshot["ref"],
                "run_contract_digest": snapshot["contract_digest"],
                "source_refs": {"source_ref": "prd.md"},
            },
        )
    )
    log = runtime.event_log
    runtime.run_once(events=[invoke])
    assert log.query(type="workflow.invoke.accepted"), [
        e.payload for e in log.query(type="workflow.invoke.rejected")
    ]
    (fanout,) = log.query(type="task.fanout.requested")
    runtime.run_once(events=[fanout])
    return runtime, transport, credentials, run


@pytest.mark.parametrize(
    "recipient_role,unassigned", [("tech-scan", "orchestrator"), ("orchestrator", "planner")]
)
def test_admitted_prd_readers_exchange_one_notice_and_route_silence(
    tmp_path, monkeypatch, capsys, recipient_role, unassigned
):
    monkeypatch.chdir(tmp_path)
    readers = ["product-scan", recipient_role]
    runtime, transport, credentials, run = open_prd_run(tmp_path, readers)
    state, config, log = runtime.state_dir, runtime.config, runtime.event_log
    assert sorted(role for role, *_ in transport.sent) == sorted(readers), [
        (e.type, e.payload.get("reason")) for e in log.read_all()[-10:]
    ]
    operations = reduce_workflow_operations(log.read_all())
    actors = {
        op["role_instance"]: {
            "actor": op["role_instance"],
            "attempt_id": op["active_attempt_id"],
            "operation_id": op["operation_id"],
        }
        for op in operations.values()
        if op["role_instance"] in readers and op["status"] == "running"
    }
    sender, recipient = (actors[role] for role in readers)
    for role, briefing, *_ in transport.sent:
        assert f"artifact board --attempt {actors[role]['attempt_id']}" in briefing.read_text()
        assert "--roster" in briefing.read_text()

    def cli(actor, *args, success=True):
        role = actor["actor"]
        monkeypatch.setenv("ZF_ROLE_INSTANCE", role)
        monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(credentials[role]))
        code = main(["artifact", "board", "--state-dir", str(state), "--attempt", actor["attempt_id"], *args])
        output = capsys.readouterr()
        if success:
            assert code == 0, output.err
            return json.loads(output.out)
        assert code == 1
        return output.err

    roster = cli(sender, "--roster")
    assert sorted(roster["members"]) == sorted(readers)
    assert sorted(roster["groups"]["prd-scan"]) == sorted(readers)
    refusal = cli(
        sender,
        "--post",
        "outside",
        "--recipient",
        unassigned,
        "--text",
        "Inspect this need.",
        success=False,
    )
    assert "outside" in refusal
    arguments = (
        "--post",
        "overlap",
        "--recipient",
        recipient["actor"],
        "--text",
        "Inspect the shared boundary.",
    )
    notice = cli(sender, *arguments)
    assert notice["workflow_run_id"] == run
    assert notice["audience"][recipient["actor"]]["operation_id"] == recipient["operation_id"]
    (posted,) = log.query(type="workflow.board.posted")
    runtime.run_once(events=[posted])
    runtime.run_once(events=[posted])
    (wake,) = log.query(type="worker.reply.requested")
    assert wake.payload["attempt_id"] == recipient["attempt_id"]
    runtime.run_once(events=[wake])
    runtime.run_once(events=[wake])
    (reply,) = log.query(type="worker.reply.sent")
    assert reply.causation_id == wake.id
    reminders = [
        (role, context) for role, _, _, context in transport.sent if context and context.continuation
    ]
    assert [(role, context.operation_id) for role, context in reminders] == [
        (recipient["actor"], recipient["operation_id"])
    ]
    assert cli(sender, *arguments) == notice
    assert len(log.query(type="workflow.board.posted")) == 1
    (row,) = cli(recipient, "--ack", notice["message_key"])
    assert row["body"] == "Inspect the shared boundary." and row["consumed"]
    now = datetime.now(timezone.utc)
    events = log.read_all()
    assert not board_silence_attention(state, config, events, now)
    late = now + timedelta(days=1)
    (diagnosis,) = board_silence_attention(state, config, events, late)
    assert diagnosis["suggested_route"] == "run_manager_recovery"
    assert diagnosis["suggested_action"]["authorizes_retry"] is False
    assert set(diagnosis["suggested_action"]["operation_ids"]) == {a["operation_id"] for a in actors.values()}
    assert not board_silence_attention(state, config, events, late, paused=True)
