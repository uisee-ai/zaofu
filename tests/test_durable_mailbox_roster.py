from types import SimpleNamespace

import pytest

from tests.test_stage_team_workbench import approve
from tests.test_workflow_registered_controller import assembly
from tests.test_reader_fanout_runtime import _config, _RecordingTransport
from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent
from zf.runtime import durable_mailbox as mail
from zf.runtime.durable_mailbox_authority import roster
from zf.runtime.workflow_operation import reduce_workflow_operations


def test_pre_dispatch_obligation_pulls_only_after_real_wrc_dispatch(assembly, tmp_path):
    from tests.test_prd_stage_teams import select
    from tests.test_workflow_registered_controller import _candidate
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.workflow_proposal import build_workflow_proposal

    base, state, _, _, writer = assembly
    (state / "kanban.json").write_text("[]")
    select(assembly, ["product-scan", "tech-scan"])
    outcome, req, candidate = _candidate(assembly)
    config = load_config(candidate)
    proposal, _ = build_workflow_proposal(
        state,
        request=req,
        base_config_path=base,
        candidate_config_path=candidate,
        synthesis_result_ref=outcome.result_ref,
        flow_kind="prd",
        writer=writer,
        preflight={"status": "GO", "blockers": []},
    )
    assert proposal["approval_status"] == "approvable"
    run = req["request_id"]
    rt = SimpleNamespace(state_dir=state, event_log=writer.event_log, event_writer=writer, config=config)
    rt.config.runtime.durable_mailbox.enabled = True

    def members():
        events = rt.event_log.read_all()
        return roster(rt, events, reduce_workflow_operations(events), run)

    assert members() == []
    approve(assembly, run)
    choices = [
        a for a, ops in members() if a["stage"] == "prd-scan" and a["role"] == "product-scan" and not ops
    ]
    assert len(choices) == 1
    target = choices[0]
    for kind in ("requested", "started"):
        writer.append(
            ZfEvent(
                type="workflow.operation." + kind,
                origin="kernel",
                payload={
                    "workflow_run_id": run,
                    "operation_id": "post",
                    "operation_key": "post",
                    "operation_type": "task_pipeline",
                    "request_hash": "input",
                    "role_instance": "orchestrator",
                    "active_attempt_id": "send",
                },
            )
        )
    sender = {"actor": "orchestrator", "attempt_id": "send", "operation_id": "post"}
    msg = mail.post(
        rt,
        sender,
        request_id="before",
        message_id="before",
        kind="question",
        text="Must review this requirement",
        visibility="operator_metadata",
        recipients=[
            {"mode": "obligation", "stage": target["stage"], "obligation_id": target["obligation_id"]}
        ],
    )
    assert (
        mail.project_mailbox(rt.event_log.read_all(), run_id=run)["items"][0]["status"] == "pending_binding"
    )
    assert not any(e.type == "fanout.child.dispatched" for e in rt.event_log.read_all())
    # Replay the actual approved stage through deterministic WRC, keeping the same event store.
    stage = next(s for s in config.workflow.stages if s.id == "prd-scan")
    runtime = Orchestrator(state, _config(mode="wait_for_all", synth=False), _RecordingTransport())
    runtime.config.workflow.stages = [stage]
    runtime.config.roles = [
        RoleConfig(name=x, instance_id=x, backend="mock", role_kind="reader")
        for x in ("product-scan", "tech-scan")
    ]
    # Stage ingress and dispatch use the mock runtime without extra lanes or ad hoc membership.
    trigger = runtime.event_writer.append(
        ZfEvent(
            type="prd.requested",
            origin="kernel",
            correlation_id=run,
            payload={"workflow_run_id": run, "flow_kind": "prd", "pdd_id": "F", "prd_ref": "prd.md"},
        )
    )
    runtime.run_once(events=[trigger])
    matched = [(a, ops) for a, ops in members() if a == target]
    assert len(matched) == 1 and len(matched[0][1]) == 1
    op = reduce_workflow_operations(rt.event_log.read_all())[matched[0][1][0]]
    recipient = {
        "actor": op["role_instance"],
        "operation_id": op["operation_id"],
        "attempt_id": op["active_attempt_id"],
    }
    delivery = mail.prepare(rt, recipient, request_id="first", message_ids=[msg["message_key"]])
    assert delivery["messages"][0]["text"] == "Must review this requirement"
    first = mail.acknowledge(rt, recipient, delivery_id=delivery["delivery_id"])
    assert first["status"] == "acknowledged"
    mail.question_action(
        rt,
        recipient,
        message_id=msg["message_key"],
        action="claim",
        request_id="take",
        delivery_id=delivery["delivery_id"],
    )
    from zf.runtime.workflow_operation import WorkflowOperationService

    service = WorkflowOperationService(state_dir=state, event_writer=writer, event_log=rt.event_log)
    ident = {"workflow_run_id": run, "operation_id": op["operation_id"], "request_hash": op["request_hash"]}
    service.fail(**ident, reason="transport_stopped")
    service.mark_retry_started(
        **ident,
        retry_attempt=1,
        reason="restore_current_obligation",
        role_instance=op["role_instance"],
        active_attempt_id="renewed",
        dispatch_id="retry-dispatch",
        lease_id="retry-lease",
    )
    with pytest.raises(ValueError):
        mail.acknowledge(rt, recipient, delivery_id=delivery["delivery_id"])
    with pytest.raises(ValueError):
        mail.question_action(
            rt,
            recipient,
            message_id=msg["message_key"],
            action="answer",
            request_id="late",
            text="Old consumer",
            delivery_id=delivery["delivery_id"],
        )
    successor = {**recipient, "attempt_id": "renewed"}
    restored = mail.prepare(rt, successor, request_id="first", message_ids=[msg["message_key"]])
    assert restored["delivery_id"] != delivery["delivery_id"]
    assert restored["messages"][0]["text"] == delivery["messages"][0]["text"]
    mail.acknowledge(rt, successor, delivery_id=restored["delivery_id"])
    mail.question_action(
        rt,
        successor,
        message_id=msg["message_key"],
        action="claim",
        request_id="take",
        delivery_id=restored["delivery_id"],
    )
    mail.question_action(
        rt,
        successor,
        message_id=msg["message_key"],
        action="answer",
        request_id="answer",
        text="Recovered",
        delivery_id=restored["delivery_id"],
    )
    assert mail.read_answer(rt, sender, message_id=msg["message_key"])["text"] == "Recovered"

    # An amended requirement and a second real approved proposal change this
    # member's remit. Neither the previous result nor its live operation transfers.
    pending = mail.post(
        rt,
        sender,
        request_id="question-2",
        message_id="question-2",
        kind="question",
        text="Does the original evidence still cover the new requirement?",
        recipients=[target],
        visibility="operator_metadata",
    )
    import json
    import copy
    from zf.runtime.workflow_requests import revise_workflow_request

    manifest = tmp_path / "change.json"
    manifest.write_text(json.dumps({"request_id": run, "kind": "prd"}))
    revised = revise_workflow_request(
        state,
        manifest,
        actor="owner",
        objective="Evaluate the revised product source.",
        acceptance=["Check the revised product and its source."],
        source_root=".",
        target_root=".",
        expected_revision=1,
        confirm=True,
        writer=writer,
    )
    result = copy.deepcopy(assembly[3])
    result.update(
        request_revision=revised["revision"],
        requirement_ref=revised["requirement_spec_ref"],
        requirement_digest=revised["requirement_spec_digest"],
    )
    new_case = (base, state, revised, result, writer)
    plan = select(new_case, ["product-scan", "tech-scan"])
    plan["members"][0]["question"] = "Evaluate the revised product source."
    outcome, request, candidate = _candidate(new_case)
    proposal, _ = build_workflow_proposal(
        state,
        request=request,
        base_config_path=base,
        candidate_config_path=candidate,
        synthesis_result_ref=outcome.result_ref,
        flow_kind="prd",
        writer=writer,
        preflight={"status": "GO", "blockers": []},
    )
    assert proposal["approval_status"] == "approvable"
    approve(new_case, run)
    ((updated, ids),) = [
        (m, ids)
        for m, ids in members()
        if m["stage"] == target["stage"] and m["obligation_id"] == target["obligation_id"]
    ]
    assert updated["version"] != target["version"] and ids == []
    with pytest.raises(ValueError):
        mail.prepare(rt, successor, message_ids=[pending["message_key"]], request_id="cross-version")
    assert mail.reconcile_mailbox(rt) >= 1
    with pytest.raises(ValueError, match="applicability"):
        mail.question_action(
            rt,
            sender,
            message_id=pending["message_key"],
            request_id="implicit",
            action="handoff",
            target={
                "mode": "obligation",
                "stage": updated["stage"],
                "obligation_id": updated["obligation_id"],
            },
        )
    handoff = mail.question_action(
        rt,
        sender,
        message_id=pending["message_key"],
        request_id="owner-decision",
        action="handoff",
        target=updated,
        text="The prior question is applicable to the newly approved product source.",
    )
    assert handoff["status"] == "handed_off"
    latest = next(e for e in reversed(writer.event_log.read_all()) if e.id == handoff["event_id"])
    assert (
        latest.payload["target"]["version"] == updated["version"] and latest.payload["note_ref"]["required"]
    )
    with pytest.raises(ValueError):
        mail.prepare(rt, successor, message_ids=[pending["message_key"]], request_id="still-old")


def test_admitted_task_delta_keeps_the_original_address_only_for_the_unaffected_member(tmp_path):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.orchestrator_agent_run_plan import EXECUTION_PLAN_SCHEMA
    from zf.runtime.stage_team_sources import read_stage_team_inputs
    from zf.runtime.durable_mailbox_authority import member_address

    events = []

    def generation(name, tasks):
        identity = {
            "workflow_run_id": "r",
            "plan_artifact_package_ref": "plan-" + name,
            "plan_artifact_package_digest": "digest-" + name,
            "task_map_generation": name,
        }
        unit = {"work_unit_id": "verification", "task_ids": tasks, "stage_ids": ["verify"]}
        plan = {
            "schema_version": EXECUTION_PLAN_SCHEMA,
            "identity": identity,
            "work_units": [unit],
            "delegation": [{"work_unit_id": "verification", "preferred_role_refs": ["reviewer"]}],
        }
        ref = write_immutable_json_sidecar(
            tmp_path,
            plan,
            root="plans",
            kind="execution_plan",
            schema_version=EXECUTION_PLAN_SCHEMA,
            created_by="orchestrator",
        )
        events.extend(
            [
                ZfEvent(
                    type="task.pipeline.generation.admitted",
                    origin="kernel",
                    payload={
                        **identity,
                        "schema_version": "task-pipeline-generation.v1",
                        "stage_id": "pipeline",
                        "flow_kind": "prd",
                        "task_ids": tasks,
                    },
                ),
                ZfEvent(
                    type="orchestrator.run_plan.admitted",
                    origin="kernel",
                    payload={**identity, "execution_plan_ref": ref},
                ),
            ]
        )

    def addresses(proposal):
        inputs = {"scope": {"run_id": "r"}, "compiled": {}, "events": list(enumerate(events, 1))}
        rows, refs = read_stage_team_inputs(tmp_path, inputs)
        return {
            m["task_ids"][0]: member_address(
                {**m, "stage": "pipeline"}, m["task_ids"][0], proposal_digest=proposal
            )
            for m in rows["pipeline"]["members"]
        }

    generation("v1", ["A", "B"])
    first = addresses("approved-first")
    generation("v2", ["B"])
    second = addresses("approved-new")
    assert first["A"] == second["A"]
    assert first["B"]["version"] != second["B"]["version"]
