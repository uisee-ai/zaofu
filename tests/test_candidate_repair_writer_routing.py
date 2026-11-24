"""A scoped OA amendment survives restart into its original flow's Writer."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from tests.test_candidate_task_repair_decision import incident, submit
from zf.core.config.schema import WorkflowStageConfig
from zf.runtime.candidate_repair_outcome import routable_candidate_repair
from zf.runtime.orchestrator import WorkflowRuntimeCoordinator


def old_publication(runtime, monkeypatch, *, stop_after_amendment=False):
    original = runtime.event_writer.emit

    def emit(kind, **kwargs):
        if kind in {"task_map.ready", "task_map.amended"}:
            kwargs["payload"] = dict(kwargs["payload"])
            kwargs["payload"].pop("flow_kind", None)
        published = original(kind, **kwargs)
        if stop_after_amendment and kind == "task_map.amended":
            raise RuntimeError("old build stopped")
        return published

    monkeypatch.setattr(runtime.event_writer, "emit", emit)
    return original


@pytest.mark.parametrize("legacy", [True, False])
def test_restarted_writer_consumes_native_amendment_once(tmp_path, monkeypatch, legacy):
    from zf.runtime import plan_artifact_package_runtime

    runtime, request, repair = incident(tmp_path, monkeypatch, canonical=True)
    if legacy:
        old_publication(runtime, monkeypatch)
    _, applied = submit(runtime, request, repair)
    assert applied["applied"], applied
    events = runtime.event_log.read_all()
    ready = next(e for e in events if e.type == "task_map.ready")
    assert ready.payload.get("flow_kind") == (None if legacy else "prd")
    runtime.config.workflow.stages = [WorkflowStageConfig(
        id="prd-writers", flow_kind="prd", trigger="task_map.ready", topology="fanout_writer_scoped",
        roles=["dev-a"],
    )]
    coordinator = WorkflowRuntimeCoordinator(runtime.state_dir, runtime.config, Mock(), project_root=tmp_path)
    observed = []
    # The package handoff is the first Writer boundary after the real routing and run checks.
    def admit(runtime, event, stages):
        observed.append((event.id, event.payload["flow_kind"], event.payload["task_ids"], stages[0].id))
        runtime.event_writer.emit("fanout.started", payload={"trigger_event_id": event.id,
                                  "stage_id": stages[0].id, "fanout_id": "writers"})
        return None

    monkeypatch.setattr(plan_artifact_package_runtime, "admit_task_map_trigger_package", admit)
    coordinator._resume_unstarted_rework_task_maps(events)
    coordinator._resume_unstarted_rework_task_maps(coordinator.event_log.read_all())
    assert observed == [(ready.id, "prd", ["TASK-2-FIX"], "prd-writers")]
    assert len([e for e in coordinator.event_log.read_all() if e.type == "task_map.ready"]) == 1
    for forged in (replace(ready, origin="worker"), replace(ready, id="unknown"),
                   replace(ready, payload={**ready.payload, "flow_kind": "issue"}),
                   replace(ready, payload={**ready.payload, "task_ids": ["unrelated"]})):
        assert routable_candidate_repair(coordinator, forged) is None
    coordinator.event_writer.emit("task_map.ready", correlation_id=ready.correlation_id,
                                  payload={"pdd_id": ready.payload["pdd_id"], "task_map_generation": "newer"})
    assert routable_candidate_repair(coordinator, ready) is None


def test_restart_after_old_partial_amendment_reuses_its_original_id(tmp_path, monkeypatch):
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint

    runtime, request, repair = incident(tmp_path, monkeypatch, canonical=True)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    event = _submit_rework(runtime, prepared, action="rework", directive_fields={"candidate_repair": repair})
    original = old_publication(runtime, monkeypatch, stop_after_amendment=True)
    with pytest.raises(RuntimeError, match="old build stopped"):
        apply_orchestrator_agent_decision(runtime, event)
    monkeypatch.setattr(runtime.event_writer, "emit", original)
    applied = apply_orchestrator_agent_decision(runtime, event)
    assert applied["applied"], applied
    events = runtime.event_log.read_all()
    amended, = [e for e in events if e.type == "task_map.amended"]
    ready, = [e for e in events if e.type == "task_map.ready"]
    assert "flow_kind" not in amended.payload
    assert ready.payload["flow_kind"] == "prd" and routable_candidate_repair(runtime, ready) == ready
