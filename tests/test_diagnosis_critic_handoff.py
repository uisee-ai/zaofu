"""An independent Critic pre-dispatch failure can preserve an already-admitted Planner."""

from dataclasses import replace

import pytest

import tests.test_plan_candidate_diagnosis_review as producer
from tests.test_plan_candidate_preflight import _PlanSynthHarness
from zf.core.events.model import ZfEvent
from zf.runtime.diagnosis_critic_handoff import admitted_unissued_planner
from zf.runtime.fanout_report_failure import project_synth_failure_payload
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


@pytest.fixture
def ready(tmp_path, monkeypatch):
    original = producer._diagnosis_trigger

    def seeded(runtime):
        t = original(runtime)
        log = runtime.event_writer
        log.append(
            ZfEvent(
                id=t["candidate_event_id"],
                type="candidate.ready",
                actor="zf-cli",
                origin="kernel",
                payload={
                    "schema_version": "candidate-freeze-receipt.v1",
                    "workflow_run_id": t["workflow_run_id"],
                    "candidate_head_commit": t["source_commit"],
                },
            )
        )
        trigger = log.append(
            ZfEvent(id="evt-trigger", type="issue.created", actor="zf-cli", origin="kernel", payload=t)
        )
        log.append(
            ZfEvent(
                type="fanout.started",
                actor="zf-cli",
                origin="kernel",
                causation_id=trigger.id,
                payload={
                    "fanout_id": "fanout-plan-candidate",
                    "workflow_run_id": t["workflow_run_id"],
                    "stage_id": "issue-triage",
                },
            )
        )
        return t

    monkeypatch.setattr(producer, "_diagnosis_trigger", seeded)
    runtime, prepared, service, token, req, sem = producer.active.__wrapped__(tmp_path)
    monkeypatch.chdir(runtime.project_root)
    service.submit(
        operation_id=prepared.operation_id, role_instance="planner", credential=token, semantic_result=sem
    )
    events = runtime.event_log.read_all()
    admit = next(e for e in events if e.type == "workflow.call.result.admitted")
    child = next(e for e in events if e.id == admit.payload["source_event_id"])
    certified = {
        k: admit.payload[k]
        for k in ("workflow_run_id", "attempt_id", "request_hash", "operation_id", "control_result_ref")
    }
    runtime.event_writer.append(
        ZfEvent(
            type="fanout.child.completed",
            origin="kernel",
            actor="zf-cli",
            causation_id=child.id,
            payload={
                **certified,
                "child_id": "planner",
                "role_instance": "planner",
                "fanout_id": req["fanout_id"],
            },
        )
    )
    manifest = {
        **req["plan_candidate_validation"]["manifest"],
        "trace_id": req["workflow_run_id"],
        "trigger_event_id": "evt-trigger",
        "aggregate_config": {
            "success_event": "task_map.ready",
            "failure_event": "issue.plan.failed",
            "synth_role": "plan-critic",
        },
        "children": [],
    }
    return runtime, manifest, sem, admit


def test_handoff_failure_reaches_reactor_and_resident_without_restarting_planner(
    ready, tmp_path, monkeypatch
):
    from zf.runtime.orchestrator_reactor import EventReactorMixin
    from zf.runtime.run_manager import _pending_semantic_event_actions

    runtime, manifest, sem, admit = ready
    root = tmp_path / "dispatch"
    root.mkdir()
    harness = _PlanSynthHarness(root, [{"child_id": "planner", "report": sem}])
    harness.config.workflow.stages[0].trigger = "issue.created"
    harness.config.workflow.stages[0].topology = "fanout_reader"
    harness.config.runtime.run_manager.resident_agent.enabled = True
    harness.project_root, harness.state_dir = runtime.project_root, runtime.state_dir
    harness.event_log, harness.event_writer = runtime.event_log, runtime.event_writer
    harness._checkout_fanout_reader = lambda *args: None

    def unavailable(**kwargs):
        raise ValueError("fixture exercises any mechanical input failure")

    monkeypatch.setattr("zf.runtime.plan_synth_handoff.build_plan_synth_call_payload", unavailable)
    harness._dispatch_fanout_synth(manifest["fanout_id"], manifest, "wait_for_all", "plan-critic")
    failed = harness.finalized
    assert failed.type == "fanout.synth.completed" and failed.causation_id == manifest["trigger_event_id"]
    assert failed.payload["failure_scope"] == "diagnosis_critic_handoff"
    assert failed.payload["planner_admitted_event_id"] == admit.id
    assert failed.payload["planner_result_ref"] == admit.payload["control_result_ref"]
    assert failed.payload["semantic_attempt_consumed"] is False
    assert "failure_scope" not in project_synth_failure_payload({}, failed.payload, {}, [])
    projected = project_synth_failure_payload({}, failed.payload, {}, [], verified_diagnosis_handoff=True)
    stage = runtime.event_writer.append(
        ZfEvent(
            type="issue.plan.failed",
            origin="kernel",
            actor="zf-cli",
            causation_id=failed.id,
            payload={
                **projected,
                "workflow_run_id": manifest["trace_id"],
                "stage_id": manifest["stage_id"],
                "fanout_id": manifest["fanout_id"],
            },
        )
    )
    rows = runtime.event_log.read_all()
    assert plan_reader_stage_replan(harness.config, rows, stage, state_dir=runtime.state_dir) == (
        None,
        "infrastructure_failure",
    )
    assert plan_reader_stage_replan(
        harness.config, rows, stage, state_dir=runtime.state_dir, authorized_generation="unrelated"
    ) == (None, "diagnosis_critic_handoff_only")
    decision = EventReactorMixin._on_reader_stage_failure_replan(harness, stage)
    assert decision.action == "wait" and decision.target_role == "run-manager"
    [action] = [
        a for a in _pending_semantic_event_actions([stage]) if stage.id in a.get("source_event_ids", [])
    ]
    assert (
        action["planner_admitted_event_id"] == admit.id
        and action["safe_resume_action"] == "diagnose_attention"
    )
    assert action["expected_downstream_events"] == ["fanout.synth.dispatched"]
    assert not any(
        e.type in {"fanout.synth.dispatched", "task_map.ready", "plan.approved"}
        for e in runtime.event_log.read_all()
    )


@pytest.mark.parametrize(
    "fault",
    [
        "different-fanout",
        "different-trigger",
        "superseded-freeze",
        "uncertified",
        "wrong-role",
        "critic-dispatched",
        "critic-issued",
        "tampered-result",
    ],
)
def test_only_current_unissued_review_is_eligible(ready, fault):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    runtime, manifest, _, admit = ready
    rows = runtime.event_log.read_all()
    parent, fanout = manifest["trigger_event_id"], manifest["fanout_id"]
    if fault == "different-fanout":
        fanout = "other"
    elif fault == "different-trigger":
        parent = "evt-other"
    elif fault == "superseded-freeze":
        rows.append(
            replace(rows[0], id="evt-new", payload={**rows[0].payload, "candidate_head_commit": "c" * 40})
        )
    elif fault == "uncertified":
        rows = [e for e in rows if e.id != admit.id]
    elif fault == "wrong-role":
        rows[-1] = replace(rows[-1], payload={**rows[-1].payload, "role_instance": "another"})
    elif fault == "critic-dispatched":
        rows.append(ZfEvent(type="fanout.synth.dispatched", payload={"fanout_id": fanout}))
    elif fault == "critic-issued":
        ref = write_immutable_json_sidecar(
            runtime.state_dir,
            {"request": {"fanout_id": fanout}},
            root="requests",
            kind="request",
            schema_version="request.v1",
            created_by="test",
        )
        rows.append(
            ZfEvent(
                type="workflow.operation.requested",
                origin="kernel",
                payload={
                    "workflow_run_id": manifest["trace_id"],
                    "parent_stage_id": manifest["stage_id"],
                    "operation_type": "fanout_synth",
                    "request_ref": ref,
                },
            )
        )
    else:
        path = runtime.state_dir / admit.payload["control_result_ref"]["ref"]
        path.write_text("changed")
        with pytest.raises(ValueError):
            admitted_unissued_planner(runtime.state_dir, rows, fanout_id=fanout, trigger_event_id=parent)
        return
    assert (
        admitted_unissued_planner(runtime.state_dir, rows, fanout_id=fanout, trigger_event_id=parent) is None
    )
