"""Owner retries retain shadow evidence without inventing applied OA authority."""

import copy

import pytest

from tests.test_control_actions_recovery import _req, _service
from tests.test_plan_artifact_package import _fixture_port, _package
from tests.test_reader_repair_target import setup_reader
from zf.core.events import ZfEvent
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _shadow(service, *, run_id="run-1", mutation=None):
    port = _fixture_port(service.state_dir, "shadow_decision", {"decision": "revise"})
    ref = {"ref": port["ref"], "sha256": port["sha256"]}
    event = service.writer.append(ZfEvent(type="orchestrator.semantic.decision.shadowed",
        origin="kernel", payload={"workflow_run_id": run_id, "decision_ref": ref,
            "checkpoint_policy": "shadow", "applied": False, **(mutation or {})}))
    return ref, event


def _retry(service, source, *, approval="operator:approved"):
    payload = {"source_event_id": source.id, "approval_ref": approval}
    result = service.execute(action="stage-retrigger", requested_action="stage-retrigger",
                             payload=payload, requested=_req(payload))
    assert result["ok"], result
    return next(e for e in service.writer.event_log.read_all() if e.id == result["event_id"])


def test_retrigger_preserves_shadow_as_readable_observation(tmp_path):
    service = _service(tmp_path)
    ref, shadow = _shadow(service)
    package = write_plan_artifact_package(service.state_dir, _package(service.state_dir))
    source = service.writer.append(ZfEvent(type="flow.discovery.requested", payload={
        "workflow_run_id": "run-1", "orchestrator_decision_ref": ref,
        "plan_artifact_package_ref": package["ref"],
        "plan_artifact_package_digest": package["sha256"],
        "operator_feedback": ["Repair the scoped task contracts."],
    }))
    before = copy.deepcopy(source.payload)
    trigger = _retry(service, source)
    assert "orchestrator_decision_ref" not in trigger.payload
    history = trigger.payload["historical_orchestration_context"]
    assert history["orchestrator_decision_ref"] == ref
    assert history["shadow_event_id"] == shadow.id
    assert source.payload == before
    refs = build_plan_handoff_input_refs(state_dir=service.state_dir, project_root=tmp_path,
                                        payload=trigger.payload)
    sources = {r["source_id"]: r for r in refs}
    assert "plan-rework-observation" in sources
    assert "plan-rework-decision" not in sources
    body = hydrate_sidecar_ref(service.state_dir, sources["plan-rework-context"]).payload
    assert body["historical_orchestration_context"] == history
    assert body["operator_feedback"] == before["operator_feedback"]
    (service.state_dir / ref["ref"]).write_text('{"tampered":true}')
    with pytest.raises(ValueError):
        build_plan_handoff_input_refs(state_dir=service.state_dir, project_root=tmp_path,
                                      payload=trigger.payload)


@pytest.mark.parametrize("case", ["no_approval", "other_run", "other_ref", "applied", "non_kernel"])
def test_retrigger_cannot_demote_unproven_or_applied_authority(tmp_path, case):
    service = _service(tmp_path)
    mutation = {"workflow_run_id": "other"} if case == "other_run" else {}
    if case == "applied":
        mutation["applied"] = True
    if case == "non_kernel":
        # Persist only an untrusted assertion, not a kernel shadow verdict.
        ref = {"ref": "observation.json", "sha256": "a" * 64}
        service.writer.append(ZfEvent(type="orchestrator.semantic.decision.shadowed",
            payload={"workflow_run_id": "run-1", "decision_ref": ref,
                     "checkpoint_policy": "shadow", "applied": False}))
    else:
        ref, _ = _shadow(service, mutation=mutation)
    if case == "other_ref":
        ref = {**ref, "sha256": "b" * 64}
    source = service.writer.append(ZfEvent(type="flow.discovery.requested", payload={
        "workflow_run_id": "run-1", "orchestrator_decision_ref": ref}))
    trigger = _retry(service, source, approval="" if case == "no_approval" else "owner")
    assert trigger.payload["orchestrator_decision_ref"] == ref
    assert "historical_orchestration_context" not in trigger.payload


def test_applied_return_to_plan_authority_is_preserved(tmp_path):
    service = _service(tmp_path)
    ref, _ = _shadow(service)
    service.writer.append(ZfEvent(type="orchestrator.replan_requested", origin="kernel",
        payload={"workflow_run_id": "run-1", "orchestrator_decision_ref": ref}))
    source = service.writer.append(ZfEvent(type="flow.discovery.requested", payload={
        "workflow_run_id": "run-1", "orchestrator_decision_ref": ref}))
    assert _retry(service, source).payload["orchestrator_decision_ref"] == ref


def test_new_generation_also_demotes_proven_shadow(tmp_path):
    service, origin, payload, _ = setup_reader(tmp_path)
    payload.pop("repair_target")
    ref, _ = _shadow(service, run_id="RUN")
    # The causal failed stage must contain the mistaken authority claim.
    original_events = service.writer.event_log.read_all()
    from zf.core.events import EventLog, EventWriter
    service.writer = EventWriter(EventLog(service.state_dir / "causal.jsonl"))
    for event in original_events:
        if event.id == origin.id:
            event.payload["orchestrator_decision_ref"] = ref
        service.writer.append(event)
    result = service.execute(action="stage-replan-new-generation",
        requested_action="stage-replan-new-generation", payload=payload, requested=_req(payload))
    assert result["ok"], result
    trigger = next(e for e in service.writer.event_log.read_all() if e.id == result["event_id"])
    assert trigger.payload["historical_orchestration_context"]["orchestrator_decision_ref"] == ref
    assert "orchestrator_decision_ref" not in trigger.payload
