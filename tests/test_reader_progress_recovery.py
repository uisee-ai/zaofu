"""A progressing reader survives transport loss without semantic rework."""
from pathlib import Path

import pytest

from tests.test_reader_fanout_runtime import _start_fanout, _state, _task_map_synth_orchestrator
from zf.core.config.schema import ExecutionConfig, ExecutionProfileConfig
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path, materialize_attempt_source_ref, read_attempt_artifact,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def _reader(tmp_path: Path, *, plan: bool = False):
    state, log, transport, runtime = _state(
        tmp_path, synth=plan, child_success_event="workflow.child.completed",
        child_failure_event="workflow.child.failed",
    )
    runtime.config.workflow.execution_profiles["bounded-direct-v1"] = ExecutionProfileConfig()
    runtime.config.workflow.flow_metadata = {"result_protocol": {"mode": "blocking"}}
    for role in runtime.config.roles:
        role.execution = ExecutionConfig(default_profile="bounded-direct-v1",
                                         profile_allowlist=["bounded-direct-v1"])
    if plan:
        stage = runtime.config.workflow.stages[0]
        stage.id, stage.attempt_domain = "prd-plan", "plan"
        stage.roles = ["review-a"]
        stage.aggregate.success_event = "task_map.ready"
        stage.aggregate.failure_event = "prd.plan.failed"
    source = tmp_path / "input.txt"
    source.write_text("Required plan content.\n" * 1500)
    ref = materialize_attempt_source_ref(state_dir=state, project_root=tmp_path,
        ref=str(source), source_id="critical-workflow-input")
    _start_fanout(runtime, payload={"input_refs": [ref]})
    dispatch = next(e for e in log.read_all() if e.type == "fanout.child.dispatched"
                    and e.payload["child_id"] == "review-a")
    manifest = hydrate_sidecar_ref(state, dispatch.payload["attempt_source_manifest"]).payload
    return state, log, transport, runtime, dispatch, manifest, ref


def _read(state, manifest, ref, start):
    return read_attempt_artifact(state, manifest=manifest, source_id=ref["source_id"],
        artifact_id=ref["artifact_id"], page_start=start, page_size=100)


def _close(runtime, log, turn, *, role="review-a", status="failed"):
    event = ZfEvent(type="provider.turn.closed", actor=role, payload={
        "backend": "codex", "provider_event": "task_complete", "provider_status": status,
        "provider_stop_reason": "transport_error", "turn_id": turn,
    })
    log.append(event)
    runtime.run_once(events=[event])


@pytest.mark.parametrize("progress", [True, False])
def test_second_transport_loss_uses_unique_input_progress(tmp_path, progress):
    state, log, transport, runtime, dispatch, manifest, ref = _reader(tmp_path)
    operation_id = dispatch.payload["operation_id"]
    before = load_workflow_operation(log, operation_id)
    _read(state, manifest, ref, 0)
    _close(runtime, log, "first")
    _read(state, manifest, ref, 100 if progress else 0)
    _close(runtime, log, "second")
    after = load_workflow_operation(log, operation_id)
    assert after["status"] == ("running" if progress else "failed")
    for key in ("request_hash", "request_ref", "active_attempt_id"):
        assert after[key] == before[key]
    assert len([x for x in transport.sent if x[0] == "review-a"]) == (3 if progress else 2)
    if progress:
        # A third loss with no new coverage remains bounded, including replay.
        _close(runtime, log, "third")
        runtime.run_once(events=[])
        failures = [e for e in log.read_all() if e.type == "fanout.child.failed"
                    and e.payload["child_id"] == "review-a"]
        assert len(failures) == 1


@pytest.mark.parametrize("fault", ["wrong_checkpoint", "corrupt_ledger", "normal_turn_end"])
def test_progress_cannot_bypass_recovery_identity_or_evidence(tmp_path, fault):
    state, log, _transport, runtime, dispatch, manifest, ref = _reader(tmp_path)
    _read(state, manifest, ref, 0)
    _close(runtime, log, "first")
    _read(state, manifest, ref, 100)
    if fault == "wrong_checkpoint":
        previous = next(e for e in log.read_all() if e.type == "fanout.child.dispatch_lost")
        runtime.event_writer.append(ZfEvent(type=previous.type, actor="zf-cli",
            payload={**previous.payload, "request_hash": "different-request"}))
    elif fault == "corrupt_ledger":
        path = active_read_ledger_path(state, manifest["attempt_id"])
        with path.open("a") as stream:
            stream.write("invalid ledger\n")
    _close(runtime, log, "second", status="completed" if fault == "normal_turn_end" else "failed")
    assert load_workflow_operation(log, dispatch.payload["operation_id"])["status"] == "failed"


@pytest.mark.parametrize("semantic_plan_submit", [False, True])
def test_synth_transport_recovery_also_preserves_read_progress(tmp_path, monkeypatch, semantic_plan_submit):
    monkeypatch.setenv("ZF_CLI_CMD", "/current/zf")
    state, log, runtime, _fanout_id = _task_map_synth_orchestrator(
        tmp_path, semantic_plan_submit=semantic_plan_submit)
    dispatch = next(e for e in log.read_all() if e.type == "fanout.synth.dispatched")
    manifest = hydrate_sidecar_ref(state, dispatch.payload["attempt_source_manifest"]).payload
    ref = next(s for s in manifest["sources"] if s["source_id"] == "plan-synth-contract")
    before = load_workflow_operation(log, dispatch.payload["operation_id"])
    _read(state, manifest, ref, 0)
    _close(runtime, log, "first", role="task-map-synth")
    _read(state, manifest, ref, 100)
    _close(runtime, log, "second", role="task-map-synth")
    dispatches = [e for e in log.read_all() if e.type == "fanout.synth.dispatched"]
    assert len(dispatches) == 3
    binding = f"--operation {before['operation_id']} --state-dir {state.resolve()} --scratch"
    prompt = runtime.transport.sent[-1][2]
    assert (f"run `/current/zf result validate {binding}` in this turn" in prompt) is semantic_plan_submit
    assert (f"run `/current/zf result submit {binding}`" in prompt) is semantic_plan_submit
    assert not any(e.type == "fanout.synth.completed" for e in log.read_all())
    after = load_workflow_operation(log, dispatch.payload["operation_id"])
    for key in ("request_hash", "request_ref", "active_attempt_id"):
        assert after[key] == before[key]
    runtime.run_once(events=[])
    assert len([e for e in log.read_all() if e.type == "fanout.synth.dispatched"]) == 3


def test_exhausted_plan_reader_does_not_become_missing_plan_ports(tmp_path):
    _state_dir, log, _transport, runtime, _dispatch, _manifest, _ref = _reader(tmp_path, plan=True)
    _close(runtime, log, "first")
    _close(runtime, log, "second")
    failed = next(e for e in log.read_all() if e.type == "prd.plan.failed")
    assert failed.payload["failure_class"] == "reader_execution_failure"
    assert not any(e.type == "fanout.synth.completed"
                   and e.payload.get("failure_class") == "plan_candidate_preflight"
                   for e in log.read_all())


def test_progress_recovery_replays_a_deferred_retry_without_spending_it(tmp_path, monkeypatch):
    state, log, transport, runtime, dispatch, manifest, ref = _reader(tmp_path)
    _read(state, manifest, ref, 0)
    _close(runtime, log, "first")
    _read(state, manifest, ref, 100)
    retry = runtime._retry_fanout_child
    monkeypatch.setattr(runtime, "_retry_fanout_child", lambda **kwargs: False)
    _close(runtime, log, "second")
    assert len([item for item in transport.sent if item[0] == "review-a"]) == 2

    monkeypatch.setattr(runtime, "_retry_fanout_child", retry)
    runtime.run_once(events=[])
    operation = load_workflow_operation(log, dispatch.payload["operation_id"])
    assert operation["status"] == "running"
    assert len([item for item in transport.sent if item[0] == "review-a"]) == 3
    assert len([event for event in log.read_all()
                if event.type == "fanout.child.dispatch_lost"]) == 2
