"""Admitted OA product repair reaches the existing controlled gap route."""
from copy import deepcopy

import pytest

from tests import test_orchestrator_agent_semantic_failure as oa
from tests.test_candidate_plan_recovery import _incident
from tests.test_plan_candidate_preflight import _task_map
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint


def incident(tmp_path, monkeypatch, *, blocked=False, canonical=False, peer=False, capped=False, before_candidate=None):
    task_map = _task_map()
    task = task_map["tasks"][0]
    task.update(task_id="TASK-2", source_refs=["docs/prd.md"], owner_role="dev",
                base_commit="b" * 40)
    if canonical:
        task_map["required_plan_ports"] = ["execution_spec", "acceptance_matrix", "test_matrix", "real_e2e_matrix"]
        task.pop("source_refs")
        task.update(source_ref="docs/prd.md", entry="standard", explicit_non_goals=["Do not change authentication"],
            evidence_contract={"plan_input_selection": {"schema_version": "task-plan-input-selection.v1",
                "implementation": ["execution_spec"], "task-verify": ["execution_spec"]}})
    task["acceptance_criteria"][0]["id"] = "AC-5"
    task["validation"]["commands"][0]["acceptance_ids"] = ["AC-5"]
    old_port = oa._port

    def port(state, name):
        if name != "task_map":
            return old_port(state, name)
        ref = write_immutable_json_sidecar(state, task_map, root="fixtures/task-map", kind="task_map",
            schema_version="task-map.v1", created_by="test")
        return {**ref, "logical_name": name, "artifact_kind": name, "producer_stage_id": "prd-plan"}

    monkeypatch.setattr(oa, "_port", port)
    def prior_repairs(runtime, run):
        from zf.core.events import ZfEvent
        for index in range(2):
            runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", correlation_id=run,
                payload={"workflow_run_id": run, "pdd_id": "TASK-1", "rework_of": f"earlier-failure-{index}"}))

    runtime, request, candidate, child = _incident(tmp_path, blocked=blocked,
        before_candidate=prior_repairs if capped else before_candidate)
    runtime.event_writer.default_origin = "kernel"
    if peer:
        from tests.test_candidate_semantic_handoff import _peer_failure
        request, _ = _peer_failure(runtime, request, child)
    runtime.task_store.update("TASK-2", status="done", active_dispatch_id="")
    gap = {**deepcopy(task), "task_id": "TASK-2-FIX", "supersedes_task_ids": ["TASK-2"],
           "base_commit": candidate.payload["candidate_head_commit"]}
    repair = {"classification": "product_defect", "contract_changed": False,
              "rationale": "Implementation violates the unchanged approved AC-5.", "gap_tasks": [gap]}
    return runtime, request, repair


def submit(runtime, request, repair):
    prepared = request_semantic_failure_checkpoint(runtime, request)
    event = oa._submit_rework(runtime, prepared, action="rework", directive_fields={"candidate_repair": repair})
    return event, apply_orchestrator_agent_decision(runtime, event)


@pytest.mark.parametrize("canonical", [False, True])
def test_unchanged_contract_product_fix_dispatches_successor_once_without_plan(tmp_path, monkeypatch, canonical):
    runtime, request, repair = incident(tmp_path, monkeypatch, canonical=canonical)
    event, result = submit(runtime, request, repair)
    assert result["applied"], result.get("reason", result)
    assert apply_orchestrator_agent_decision(runtime, event) == result
    ready = [e for e in runtime.event_log.read_all() if e.type == "task_map.ready"]
    assert len(ready) == 1
    assert ready[0].payload["task_ids"] == ["TASK-2-FIX"]
    assert ready[0].payload["source_commit"] == "c" * 40
    assert ready[0].payload["rework_of"] == request.payload["failure_event_ids"][0]
    assert runtime.task_store.get("TASK-2").status == "done"
    if canonical:
        import json
        from zf.runtime.writer_fanout_admission import writer_task_items, select_writer_task_items, validate_writer_task_items

        body = json.loads((runtime.state_dir / ready[0].payload["task_map_ref"]).read_text())
        selected, _, _, _ = select_writer_task_items(body, writer_task_items(body), ready[0].payload)
        validate_writer_task_items(selected)
        row = next(row for row in body["tasks"] if row["task_id"] == "TASK-2-FIX")
        original = repair["gap_tasks"][0]
        for key in ("entry", "evidence_contract", "acceptance_criteria", "validation", "explicit_non_goals", "source_ref"):
            assert row[key] == original[key]
    assert not any(e.type in {"prd.plan.requested", "orchestrator.semantic.rework.requested", "plan.approved"}
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize("history, expected", [("plan", 1), ("one_product", 2),
                                              ("two_products", None), ("foreign", None)])
def test_candidate_repair_budget_uses_same_run_plan_failure_sources(tmp_path, monkeypatch, history, expected):
    from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig
    from zf.core.events import ZfEvent

    def previous_maps(runtime, run):
        runtime.config.workflow.stages.append(WorkflowStageConfig(
            id="custom-outline", trigger="outline.requested", topology="fanout_reader", attempt_domain="plan",
            aggregate=FanoutAggregateConfig(success_event="task_map.ready", failure_event="custom.outline.failed")))
        for kind, stage in [("custom.outline.failed", "custom-outline"), ("plan.rejected", "writers")]:
            trigger = runtime.event_writer.append(ZfEvent(type=kind, origin="kernel", payload={
                "workflow_run_id": "elsewhere" if history == "foreign" else run, "pdd_id": "TASK-1",
                "stage_id": stage}))
            runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload={
                "workflow_run_id": run, "pdd_id": "TASK-1", "rework_of": trigger.id}))
        for _ in range({"one_product": 1, "two_products": 2}.get(history, 0)):
            failure = runtime.event_writer.append(ZfEvent(type="test.failed", origin="kernel", payload={
                "workflow_run_id": run, "pdd_id": "TASK-1"}))
            runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload={
                "workflow_run_id": run, "pdd_id": "TASK-1", "rework_of": failure.id,
                "rework_source": "plan.rejected"}))  # An annotation cannot override the source event.

    runtime, request, repair = incident(tmp_path, monkeypatch, before_candidate=previous_maps)
    _, result = submit(runtime, request, repair)
    if expected is None:
        assert not result["applied"] and "cap exhausted" in result["reason"]
    else:
        assert result["applied"], result
        ready = next(e for e in reversed(runtime.event_log.read_all()) if e.type == "task_map.ready")
        assert ready.payload["rework_attempt"] == expected and ready.payload["task_ids"] == ["TASK-2-FIX"]


@pytest.mark.parametrize("change", ["scope", "acceptance", "command", "classification", "contract", "missing",
                                   "unknown_task", "malformed_parent", "blocked", "cap"])
def test_candidate_repair_cannot_expand_authority_or_hide_a_blocker(tmp_path, monkeypatch, change):
    runtime, request, repair = incident(tmp_path, monkeypatch, blocked=change == "blocked", capped=change == "cap")
    gap = repair["gap_tasks"][0]
    if change == "scope":
        gap["allowed_paths"].append("secrets/**")
    elif change == "acceptance":
        gap["acceptance_criteria"][0]["statement"] = "Less strict behavior"
    elif change == "command":
        gap["validation"]["commands"][0]["command"] = "true"
    elif change == "classification":
        repair["classification"] = "unknown"
    elif change == "contract":
        repair["contract_changed"] = True
    elif change == "missing":
        repair.pop("gap_tasks")
    elif change == "unknown_task":
        gap["supersedes_task_ids"] = ["TASK-UNRELATED"]
    elif change == "malformed_parent":
        gap["supersedes_task_ids"] = [{"task_id": "TASK-2"}]
    _, result = submit(runtime, request, repair)
    assert not result["applied"]
    assert not any(e.type == "task_map.amended" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("scope", ["aggregate", "single"])
def test_candidate_repair_does_not_discard_an_unresolved_peer(tmp_path, monkeypatch, scope):
    runtime, request, repair = incident(tmp_path, monkeypatch, peer=True)
    if scope == "single":
        request.payload.pop("recovery_scope")
        request.payload["target_operation_id"] = "op-candidate-verify"
    _, result = submit(runtime, request, repair)
    assert not result["applied"]
    assert not any(e.type == "task_map.amended" for e in runtime.event_log.read_all())


def test_candidate_repair_cannot_revive_run_cancelled_after_oa_submission(tmp_path, monkeypatch):
    from zf.core.events import ZfEvent

    runtime, request, repair = incident(tmp_path, monkeypatch)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    event = oa._submit_rework(runtime, prepared, action="rework", directive_fields={"candidate_repair": repair})
    runtime.event_writer.append(ZfEvent(type="run.cancelled", origin="kernel",
        payload={"workflow_run_id": request.correlation_id}))
    assert not apply_orchestrator_agent_decision(runtime, event)["applied"]
    assert not any(e.type == "task_map.amended" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("crash_after", ["task_map.amended", "task_map.ready",
                                       "workflow.resume.control_action.result"])
def test_candidate_repair_recovers_partial_publication_without_duplicate_dispatch(tmp_path, monkeypatch, crash_after):
    runtime, request, repair = incident(tmp_path, monkeypatch, canonical=True)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    event = oa._submit_rework(runtime, prepared, action="rework", directive_fields={"candidate_repair": repair})
    emit = runtime.event_writer.emit

    def crash(event_type, **kwargs):
        emitted = emit(event_type, **kwargs)
        if event_type == crash_after:
            raise RuntimeError("simulated controller exit after publication")
        return emitted

    monkeypatch.setattr(runtime.event_writer, "emit", crash)
    with pytest.raises(RuntimeError, match="simulated controller exit"):
        apply_orchestrator_agent_decision(runtime, event)
    monkeypatch.setattr(runtime.event_writer, "emit", emit)
    result = apply_orchestrator_agent_decision(runtime, event)
    assert result["applied"], result
    for kind in ("task_map.amended", "task_map.ready"):
        assert len([e for e in runtime.event_log.read_all() if e.type == kind]) == 1
    assert runtime.task_store.get("TASK-2").status == "done"


@pytest.mark.parametrize("after_publication", ["run_cancelled", "corrupt_map", "external_ready"])
def test_recovery_acknowledges_only_exact_native_historical_effect(tmp_path, monkeypatch, after_publication):
    runtime, request, repair = incident(tmp_path, monkeypatch, canonical=True)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    event = oa._submit_rework(runtime, prepared, action="rework", directive_fields={"candidate_repair": repair})
    emit = runtime.event_writer.emit

    def crash(kind, **kwargs):
        if kind == "task_map.ready" and after_publication == "external_ready":
            runtime.event_writer.default_origin = "external"
        emitted = emit(kind, **kwargs)
        runtime.event_writer.default_origin = "kernel"
        if kind == "task_map.ready":
            raise RuntimeError("exit after publication")
        return emitted

    monkeypatch.setattr(runtime.event_writer, "emit", crash)
    with pytest.raises(RuntimeError, match="exit after publication"):
        apply_orchestrator_agent_decision(runtime, event)
    monkeypatch.setattr(runtime.event_writer, "emit", emit)
    ready = next(e for e in runtime.event_log.read_all() if e.type == "task_map.ready")
    if after_publication == "run_cancelled":
        emit("run.cancelled", correlation_id=request.correlation_id, payload={"workflow_run_id": request.correlation_id})
    elif after_publication == "corrupt_map":
        (runtime.state_dir / ready.payload["task_map_ref"]).write_text('{}\n')
    result = apply_orchestrator_agent_decision(runtime, event)
    assert bool(result["applied"]) == (after_publication == "run_cancelled"), result
    if result["applied"]:
        assert result["publication_replayed"] is True
    events = runtime.event_log.read_all()
    assert len([e for e in events if e.type == "task_map.ready"]) == 1
    assert not any(e.type == "run.goal.updated" for e in events)
