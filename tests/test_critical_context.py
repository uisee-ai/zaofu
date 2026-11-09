"""The actual call preparation path retains original answers and Raw settings."""
import json

import pytest

from tests.test_call_result_runtime import _runtime
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.critical_context import critical_context_sources
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def seed(runtime):
    message = runtime.event_writer.append(ZfEvent(type="user.message", actor="user", payload={
        "message": "请保留全部字段；先询问导出格式。", "project_id": "P", "conversation_id": "C",
    }))
    runtime.event_writer.append(ZfEvent(type="task.created", actor="kernel", task_id="T", payload={
        "request": {"project_id": "P", "conversation_id": "C", "originating_message_event_ids": [message.id],
                    "plan_continuation": {"answers": [{"question_id": "format", "answer": "CSV"}]}}}))
    config = write_immutable_json_sidecar(runtime.state_dir,
        {"request_revision": 3, "yaml": "kind: PrdFlow\nspec:\n  lanes: 4\n  roleDefaults:\n    model: approved-model\n"},
        root="test-config", kind="effective_config_snapshot", schema_version="effective-config-snapshot.v1", created_by="test")
    runtime.event_writer.append(ZfEvent(type="config.run_contract.request_bound", actor="kernel", task_id="T",
        payload={"run_id": "R", "effective_config_ref": config}))


def test_prepared_operations_keep_original_answers_and_approved_config_across_attempts(tmp_path):
    runtime = _runtime(tmp_path)
    seed(runtime)
    for attempt in ("first", "rework"):
        payload = {"workflow_run_id": "R", "flow_kind": "workflow", "generic_workflow_operation": "agent.read",
                   "role_instance": "reader", "stage_id": "collect", "canonical_success_event": "workflow.child.completed"}
        prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_reader_child",
            operation_key=attempt, stage_id="collect", task_id="T", dispatch_id=attempt)
        manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
        source = next(s for s in manifest["sources"] if s["source_id"] == "critical-user-and-config")
        body = json.loads((runtime.state_dir / source["ref"]).read_text())
        assert body["original_user_inputs"][0]["message"] == "请保留全部字段；先询问导出格式。"
        assert body["clarification_answers"]["answers"][0]["answer"] == "CSV"
        spec = body["approved_configuration"]["flow_documents"][0]["spec"]
        assert spec == {"lanes": 4, "roleDefaults": {"model": "approved-model"}}
        assert any(row["source_id"] == source["source_id"] for row in payload["required_reads"])
        assert manifest["attempt_id"] == prepared.attempt_id == attempt
        repeated = prepare_call_operation(runtime, payload=payload, operation_type="fanout_reader_child",
            operation_key=attempt, stage_id="collect", task_id="T", dispatch_id=attempt)
        assert repeated.operation_id == prepared.operation_id
        assert len([item for item in payload["input_refs"] if item.get("source_id") == "critical-user-and-config"]) == 1


def test_foreign_origin_is_not_imported(tmp_path):
    runtime = _runtime(tmp_path)
    seed(runtime)
    events = runtime.event_log.read_all()
    events[0].payload["conversation_id"] = "another-conversation"
    with pytest.raises(ValueError, match="another origin"):
        critical_context_sources(runtime.state_dir, events, "R")


def test_recovery_briefing_restores_only_current_live_inputs(tmp_path):
    from zf.runtime.recovery import build_recovery_briefing

    runtime = _runtime(tmp_path)
    task = Task(id="T", title="Keep answers", status="in_progress", assigned_to="dev", active_dispatch_id="d2")
    for attempt in ("d1", "d2"):
        runtime.event_writer.append(ZfEvent(type="task.dispatched", actor="kernel", task_id="T", payload={
            "dispatch_id": attempt, "attempt_id": attempt, "role_instance": "dev",
            "attempt_source_manifest_ref": f"artifacts/{attempt}.json",
            "required_reads": [{"source_id": "critical-user-and-config", "artifact_id": "critical.json"}],
        }))
    body = build_recovery_briefing(runtime.state_dir, "dev", task, compact=True)
    assert "artifact read --attempt d2 --required" in body
    assert "artifact read --attempt d1 --required" not in body
    runtime.event_writer.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T", payload={"attempt_id": "d2"}))
    assert "artifact read --attempt d2 --required" not in build_recovery_briefing(runtime.state_dir, "dev", task)
