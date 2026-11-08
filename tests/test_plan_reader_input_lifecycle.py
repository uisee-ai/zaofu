"""Preregistered Plan baselines remain readable only by their live bound reader."""
import json

import pytest

from tests.test_oa_replan_baseline import _replan, _preregister
from zf.cli.main import main
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.call_result_runtime import mark_call_operation_started
from zf.runtime.workflow_operation import reduce_workflow_operations


def setup(tmp_path):
    runtime, marker, trigger = _replan(tmp_path)
    prepared = _preregister(runtime, trigger)
    mark_call_operation_started(runtime, prepared, task_id=prepared.task_id,
                                dispatch_id=prepared.attempt_id)
    manifest = load_attempt_source_manifest(runtime.state_dir, prepared.attempt_id)
    return runtime, prepared, manifest


@pytest.mark.parametrize("command", ["index", "recover"])
def test_public_reader_input_projection_agrees_with_preregistration(tmp_path, monkeypatch, capsys, command):
    from zf.runtime.artifact_read_capability import (
        provision_role_artifact_read_credential, bind_attempt_artifact_read_capability,
    )

    runtime, prepared, manifest = setup(tmp_path)
    token = provision_role_artifact_read_credential(runtime.state_dir, "planner",
                                                    role_name="planner", provider="codex")
    bind_attempt_artifact_read_capability(runtime.state_dir, operation_id=prepared.operation_id,
        attempt_id=prepared.attempt_id, role_instance="planner", manifest=manifest)
    for key, value in {"ZF_ROLE_INSTANCE": "planner", "ZF_ROLE_NAME": "planner",
                       "ZF_ROLE_BACKEND": "codex", "ZF_ARTIFACT_PURPOSE": "workflow-read",
                       "ZF_ARTIFACT_READ_TOKEN_FILE": str(token)}.items():
        monkeypatch.setenv(key, value)
    assert main(["artifact", command, "--attempt", prepared.attempt_id,
                 "--state-dir", str(runtime.state_dir)]) == 0
    result = json.loads(capsys.readouterr().out)
    lifecycle = result["inputs"]["lifecycle"] if command == "recover" else result["lifecycle"]
    assert lifecycle["requires_rebind"] is False
    assert lifecycle["status"] == "pinned"
    assert lifecycle["reason"] == "current_plan_reader_baseline"
    assert lifecycle["authorizes_rebind"] is False
    if command == "recover":
        assert result["status"] == "bound"
        assert result["authorizes_execution"] is False
        assert result["current_memory"] == "unknown"


@pytest.mark.parametrize("fault", [
    "cancelled", "settled", "superseded", "new_attempt", "fanout_cancelled", "wrong_run",
    "manifest", "request_bytes", "domain", "type", "missing_lease", "wrong_lease", "package_rejected",
    "admission_aborted",
])
def test_plan_baseline_rejects_unproven_or_retired_reader(tmp_path, fault):
    runtime, prepared, manifest = setup(tmp_path)
    writer = runtime.event_writer
    row = reduce_workflow_operations(runtime.event_log.read_all())[prepared.operation_id]
    if fault in {"cancelled", "settled", "superseded"}:
        writer.emit("workflow.operation." + fault, payload={"operation_id": prepared.operation_id})
    elif fault == "new_attempt":
        writer.emit("workflow.operation.started", payload={"operation_id": prepared.operation_id,
                                                          "active_attempt_id": "replacement"})
    elif fault == "fanout_cancelled":
        writer.emit("fanout.cancelled", payload={"workflow_run_id": prepared.workflow_run_id,
                                                "fanout_id": "fanout-scoped-replan"})
    elif fault == "wrong_run":
        manifest["workflow_run_id"] = "another-run"
    elif fault == "manifest":
        manifest["sources"][0]["ref"] = "unbound-input.json"
    elif fault == "request_bytes":
        (runtime.state_dir / row["request_ref"]["ref"]).write_text("{}")
    elif fault in {"domain", "type"}:
        manifest["handoff_authority_contract"]["attempt_domain" if fault == "domain" else "operation_type"] = "task-stage"
    elif fault == "wrong_lease":
        writer.emit("workflow.operation.started", payload={"operation_id": prepared.operation_id, "lease_id": "other"})
    events = runtime.event_log.read_all()
    if fault == "package_rejected":
        # A rejected candidate has never been admitted as this baseline.
        next(event for event in events if event.type == "plan.artifact_package.admitted").type = "plan.artifact_package.rejected"
    if fault == "admission_aborted":
        from zf.core.events import ZfEvent

        admission = next(event for event in events if event.type == "plan.artifact_package.admitted")
        admission.payload["source_event_id"] = "aborted-plan-source"
        events.append(ZfEvent(type="fanout.cancelled", payload={
            "workflow_run_id": prepared.workflow_run_id, "failure_scope": "plan_admission",
            "trigger_event_id": "aborted-plan-source",
        }))
    if fault == "missing_lease":
        for event in events:
            if event.payload.get("operation_id") == prepared.operation_id:
                event.payload.pop("lease_id", None)
    assert input_lifecycle(manifest, events, state_dir=runtime.state_dir)["requires_rebind"]


def test_unrelated_fanout_cancellation_keeps_live_plan_baseline(tmp_path):
    runtime, prepared, manifest = setup(tmp_path)
    runtime.event_writer.emit("fanout.cancelled", payload={
        "workflow_run_id": prepared.workflow_run_id, "fanout_id": "unrelated",
    })
    assert not input_lifecycle(manifest, runtime.event_log.read_all(),
                               state_dir=runtime.state_dir)["requires_rebind"]
