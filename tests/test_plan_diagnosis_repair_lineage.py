from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_plan_candidate_diagnosis_inputs import _diagnosis
from zf.core.events import ZfEvent
from zf.runtime import plan_candidate_diagnosis_inputs
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_replan_generation import escalation_generation_id


def _case(tmp_path, monkeypatch, *, cap=False):
    runtime, payload, bound = _diagnosis(tmp_path)
    history = runtime.event_log.read_all()
    run, anchor = payload["trace_id"], payload["pdd_id"]
    for i, event in enumerate(history):
        if event.id == payload["candidate_event_id"]:
            history[i] = replace(event, payload={**event.payload, "freeze_id": "freeze"})
    parent, = [e for e in history if e.type == "prd.plan.requested" and e.payload.get("rework_of")]

    def emit(kind, cause, body, actor="zf-cli", origin="kernel"):
        row = ZfEvent(type=kind, actor=actor, origin=origin, task_id=anchor, correlation_id=run,
                      causation_id=cause.id, payload=body)
        history.append(row)
        return row

    def carrier(trigger):
        return emit("fanout.started", trigger, {"trigger_event_id": trigger.id, "fanout_id": "fan-" + trigger.id,
                    "stage_id": "prd-plan", "topology": "fanout_reader", "aggregate": {"failure_event": "prd.plan.failed"}})

    def failed(trigger):
        start = carrier(trigger)
        common = {"fanout_id": start.payload["fanout_id"], "stage_id": "prd-plan", "status": "failed", "pdd_id": anchor}
        synth = emit("fanout.synth.completed", trigger, common)
        return emit("prd.plan.failed", synth, {**common, "failure_kind": "infra"})

    def controlled(trigger, failure, escalation=None):
        subject = {"escalation_event_id": escalation.id} if escalation else {"repaired_failure_event_id": failure.id}
        action = "stage-replan-new-generation"
        args = {**subject, "approval_ref": "owner-" + failure.id}
        request = emit("web.action.requested", failure, {"action": action, "request": args}, "web", "")
        audit = {"action": action, "surface": "web", "attempt_id": "att-" + request.id, "payload": args}
        started = emit("runtime.action.attempt.started", request, audit, "web", "")
        recovered = emit(parent.type, request, {**trigger.payload, "rework_of": failure.id,
            "stage_replan_generation_source_event_id": escalation.id if escalation else failure.id,
            "stage_replan_generation": escalation_generation_id(run, escalation.id) if escalation else "repair-" + request.id,
            "operator_authorization_ref": args["approval_ref"], "operator_authorized": True,
            "repaired_failure_event_id": trigger.payload.get("repaired_failure_event_id", failure.id), "rework_attempt": 1}, origin="")
        emit("runtime.action.attempt.completed", started, {**audit, "status": "applied", "result_event_id": recovered.id}, "web", "")
        return recovered

    first_failure = failed(parent)
    first = controlled(parent, first_failure)
    latest = first
    if cap:
        second_failure = failed(first)
        second = emit(parent.type, second_failure, {**first.payload, "rework_of": second_failure.id, "rework_attempt": 2})
        last_failure = failed(second)
        escalation = emit("human.escalate", last_failure, {"failure_class": "stage_replan_cap_exhausted",
                          "source_event_id": last_failure.id, "allowed_actions": ["start_new_generation"]})
        latest = controlled(second, last_failure, escalation)
        assert latest.payload["repaired_failure_event_id"] == first_failure.id != latest.payload["rework_of"]
    current = carrier(latest)
    monkeypatch.setattr(plan_candidate_diagnosis_inputs, "EventLog", lambda _: SimpleNamespace(read_all=lambda: history))

    def read(source=None, **changes):
        refs = build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root,
            source_event_id=source or current.id, payload={**payload, **latest.payload, **changes})
        sources = {r["source_id"]: r for r in refs}
        context = hydrate_sidecar_ref(runtime.state_dir, sources["plan-rework-context"]).payload
        return context, sources

    return history, read, first_failure, parent, latest, bound


@pytest.mark.parametrize("cap", [False, True])
def test_controlled_planner_repair_preserves_original_reader_evidence_and_empty_writer_scope(tmp_path, monkeypatch, cap):
    _, read, _, original, latest, bound = _case(tmp_path, monkeypatch, cap=cap)
    context, sources = read()
    assert context["rework_of"] == latest.payload["rework_of"]
    assert not context.get("task_ids") and not context.get("input_task_ids")
    index = context["candidate_diagnosis_evidence"]
    assert index["failure_event_id"] == original.payload["rework_of"]
    assert index["candidate_event_id"] == original.payload["candidate_event_id"]
    assert {r["finding_id"] for r in index["reader_findings"]} == {f["finding_id"] for f in bound["findings"]}
    assert all(r["status"] == "available" for r in index["basis"] + index["reader_findings"])
    assert not any(k.startswith("previous-plan-candidate-") for k in sources)


@pytest.mark.parametrize("fault", ["scope", "replaced_source", "missing_receipt", "forged_request", "approval",
                                   "mixed_failure", "other_run", "wrong_fanout", "superseded_freeze", "old_package",
                                   "attempt_jump", "wrong_escalation", "worker_escalation", "forged_generation", "stripped_repair", "previous_candidate"])
def test_repair_denies_unproven_or_superseded_lineage(tmp_path, monkeypatch, fault):
    history, read, failure, original, latest, _ = _case(tmp_path, monkeypatch, cap=True)
    idx = lambda kind: next(i for i, e in enumerate(history) if e.type == kind)
    position = idx("runtime.action.attempt.completed")
    if fault == "missing_receipt":
        history.pop(position)
    elif fault == "forged_request":
        i = idx("web.action.requested")
        history[i] = replace(history[i], origin="worker")
    elif fault == "approval":
        i = idx("web.action.requested")
        history[i].payload["request"]["approval_ref"] = "other"
    elif fault == "mixed_failure":
        failure.payload["failure_kind"] = "mixed"
    elif fault == "other_run":
        history[history.index(failure)] = replace(failure, correlation_id="other")
    elif fault == "wrong_fanout":
        failure.payload["fanout_id"] = "different"
    elif fault == "superseded_freeze":
        old = next(e for e in history if e.id == original.payload["candidate_event_id"])
        history.append(replace(old, id="other", payload={**old.payload, "freeze_id": "new"}))
    elif fault == "old_package":
        idx_approval = next(i for i,e in enumerate(history) if e.type == "plan.artifact_package.admitted")
        history.pop(idx_approval)
    elif fault == "forged_generation":
        latest.payload["stage_replan_generation"] = "other-generation"
    elif fault == "attempt_jump":
        next(e for e in history if e.payload.get("rework_attempt") == 2).payload["rework_attempt"] = 3
    elif fault in {"wrong_escalation", "worker_escalation"}:
        i = idx("human.escalate")
        if fault == "wrong_escalation":
            history[i].payload["source_event_id"] = failure.id
        else:
            history[i] = replace(history[i], origin="worker")
    changes = {"scope": {"input_task_ids": ["unrelated"]}, "replaced_source": {"source": original.id},
               "previous_candidate": {"candidate_event_id": "foreign"}}.get(fault, {})
    if fault == "stripped_repair":
        changes = {"stage_replan_generation": "", "repaired_failure_event_id": "", "rework_of": original.payload["rework_of"]}
    with pytest.raises(ValueError, match="Candidate diagnosis|approved Plan Artifact Package"):
        read(**changes)

def test_idle_reader_replays_after_control_effect_receipt(tmp_path):
    from tests.test_reader_fanout_runtime import _state

    from zf.core.state.locks import locked_path
    from zf.runtime.stage_replan_generation import generation_publication_lock

    state_dir, log, transport, runtime = _state(tmp_path)
    action = "stage-replan-new-generation"
    args = {"approval_ref": "owner-test", "repaired_failure_event_id": "failed"}
    request = ZfEvent(type="web.action.requested", actor="web", payload={"action": action, "request": args})
    start = ZfEvent(type="runtime.action.attempt.started", actor="web", causation_id=request.id,
                    payload={"surface": "web", "action": action, "attempt_id": "test", "payload": args})
    trigger = ZfEvent(type="candidate.ready", actor="zf-cli", causation_id=request.id, correlation_id="trace-1",
                      payload={"pdd_id": "F-11111111", "resume_scope": "candidate_diagnosis_only", "rework_of": "failed",
                               "stage_replan_generation": "repair-" + request.id, "operator_authorized": True,
                               "operator_authorization_ref": args["approval_ref"]})
    with locked_path(generation_publication_lock(state_dir, request.id)):
        for event in (request, start, trigger):
            log.append(event)
        runtime.run_once(events=[trigger])
        runtime.run_once(events=[])
        assert not transport.sent and not any(e.type == "fanout.started" for e in log.read_all())
        log.append(ZfEvent(type="runtime.action.attempt.completed", actor="web", causation_id=start.id,
                           payload={"surface": "web", "action": action, "attempt_id": "test",
                                    "result_event_id": trigger.id, "status": "applied"}))
    runtime.run_once(events=[])
    runtime.run_once(events=[])
    assert len([e for e in log.read_all() if e.type == "fanout.started" and e.causation_id == trigger.id]) == 1
    assert [s[0] for s in transport.sent] == ["review-a", "review-b"]

def test_abandoned_publication_returns_to_normal_preregistration_failure(tmp_path, monkeypatch):
    from zf.core.state.locks import locked_path
    from zf.runtime.plan_diagnosis_repair_lineage import awaiting_diagnosis_repair_receipt
    from zf.runtime.stage_replan_generation import generation_publication_lock

    history, read, _, _, trigger, _ = _case(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="crash"), locked_path(generation_publication_lock(tmp_path, trigger.causation_id)):
        assert awaiting_diagnosis_repair_receipt(trigger, tmp_path)
        raise RuntimeError("crash")
    assert not awaiting_diagnosis_repair_receipt(trigger, tmp_path)
    history[:] = [e for e in history if e.type != "runtime.action.attempt.completed"]
    with pytest.raises(ValueError, match="controlled Planner lineage"):
        read()


@pytest.mark.parametrize("succeeded", [True, False])
def test_controlled_publication_includes_terminal_audit_then_releases(tmp_path, monkeypatch, succeeded):
    from tests.test_control_actions_recovery import _service
    from zf.runtime.plan_diagnosis_repair_lineage import awaiting_diagnosis_repair_receipt

    svc = _service(tmp_path)
    action = "stage-replan-new-generation"
    request = ZfEvent(type="web.action.requested", actor="web", payload={"action": action})
    trigger = ZfEvent(type="prd.plan.requested", causation_id=request.id, payload={
        "resume_scope": "candidate_diagnosis_only", "operator_authorized": True,
        "stage_replan_generation": "repair-" + request.id, "rework_of": "stage-failure"})
    seen = []
    append = svc.writer.append

    def observe(event):
        if event.type in {"runtime.action.attempt.started", "runtime.action.attempt.completed", "runtime.action.attempt.failed"}:
            seen.append(event.type)
            assert awaiting_diagnosis_repair_receipt(trigger, svc.state_dir)
        return append(event)

    monkeypatch.setattr(svc.writer, "append", observe)
    monkeypatch.setattr(svc, "_execute_action", lambda **kw: {"ok": succeeded, "status": "applied" if succeeded else "failed"})
    result = svc.execute(action=action, requested_action=action, payload={}, requested=request)
    assert result["ok"] == succeeded
    assert seen == ["runtime.action.attempt.started",
                    "runtime.action.attempt.completed" if succeeded else "runtime.action.attempt.failed"]
    assert not awaiting_diagnosis_repair_receipt(trigger, svc.state_dir)


@pytest.mark.parametrize("fault", ["", "foreign_parent", "missing_closeout", "other_turn",
                                   "semantic_loss", "foreign_attempt", "mismatched_digest"])
def test_transport_failed_synth_requires_exact_closed_operation(tmp_path, fault):
    from tests.test_synth_restart_checkpoint import failed_synth
    from zf.runtime.plan_diagnosis_repair_lineage import _closed_synth_transport
    from zf.runtime.reader_terminal_recovery import reconcile_exhausted_reader_operations

    state, log, runtime, _, _, _, failure = failed_synth(tmp_path)
    assert reconcile_exhausted_reader_operations(runtime, log.read_all())
    events = log.read_all()
    fanout = next(e for e in events if e.type == "fanout.started"
                  and e.payload["fanout_id"] == failure.payload["fanout_id"])
    parent = next(e for e in events if e.id == fanout.causation_id)
    repair = ZfEvent(type=parent.type)
    events.append(repair)
    loss = next(e for e in events if e.id == failure.causation_id)
    if fault == "foreign_parent":
        parent = repair
    elif fault == "missing_closeout":
        events = [e for e in events if e.type != "workflow.operation.failed"]
    elif fault == "other_turn":
        loss.payload["lost_signal_event_id"] = "other"
    elif fault == "semantic_loss":
        loss.payload["semantic_attempt_consumed"] = True
    elif fault == "foreign_attempt":
        loss.payload["run_id"] = "other"
    elif fault == "mismatched_digest":
        dispatch = next(e for e in reversed(events) if e.type == "fanout.synth.dispatched")
        dispatch.payload["request_hash"] = "0" * 64
    by_id = {e.id: e for e in events}
    positions = {e.id: i for i, e in enumerate(events)}
    assert _closed_synth_transport(state, events, by_id, positions, parent, fanout, failure, repair) == (not fault)
