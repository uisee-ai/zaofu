from copy import deepcopy

import pytest

from tests.test_result_submit import _running_plan_synth_operation
from zf.runtime.call_result_authority import CallResultAuthorityMixin
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_synth_output_binding import plan_synth_output_binding_issues
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def _verdict():
    return {"status": "completed", "recommendation": "approve", "summary": "Reviewed",
            "report": {"status": "passed", "recommendation": "approve", "summary": "Reviewed"}}


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("field,value", [
    ("task_map_ref", "artifacts/previous/task_map.json"),
    ("task_map_ref", "artifacts/current/task_map.json"),
    ("task_map_ref", "artifacts/unbound/task_map.json"),
    ("plan_ports", [{"name": "task_map", "ref": "artifacts/previous/task_map.json"}]),
    ("task_map", {"tasks": [{"id": "wrong"}]}),
    ("plan_md", "Replacement Plan"),
    ("plan_artifact_ref", "docs/plan.md"),
])
def test_pure_critic_rejects_structure_before_submission(tmp_path, nested, field, value):
    runtime, prepared, service, token = _running_plan_synth_operation(tmp_path, verdict_only=True)
    semantic = _verdict()
    semantic["plan_synth_verdict_only"] = False  # An agent cannot change pinned authority.
    (semantic["report"] if nested else semantic)[field] = value
    before = [event.id for event in runtime.event_log.read_all()]
    validation = service.validate_plan_candidate(
        operation_id=prepared.operation_id, semantic_result=semantic,
        role_instance="plan-critic", credential=token,
    )
    assert any(error["code"] == "plan_synth_structural_output_forbidden"
               for error in validation["errors"])
    with pytest.raises(ResultSubmitError, match="output binding failed"):
        service.submit(operation_id=prepared.operation_id, semantic_result=semantic,
                       role_instance="plan-critic", credential=token)
    assert [event.id for event in runtime.event_log.read_all()] == before
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "running"
    (semantic["report"] if nested else semantic).pop(field)
    semantic["report"]["evidence_refs"] = ["artifacts/previous/task_map.json"]
    submitted = service.submit(operation_id=prepared.operation_id, semantic_result=semantic,
                               role_instance="plan-critic", credential=token)
    assert submitted.canonical_event_type == "fanout.synth.completed"


def test_canonical_admission_checks_pinned_critic_authority(tmp_path):
    runtime, prepared, _service, _token = _running_plan_synth_operation(tmp_path, verdict_only=True)
    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    operation["result_identity"] = request["result_identity"]
    body = {"schema_version": "plan-synthesis-result.v1", "task_map_ref": "artifacts/old.json",
            "plan_synth_verdict_only": False}
    control = write_immutable_json_sidecar(runtime.state_dir, body, root="test-controls",
                                         schema_version=body["schema_version"], kind="control_result",
                                         created_by="test")
    raw_control_before = (runtime.state_dir / control["ref"]).read_bytes()
    envelope = {"control_result": control, "identity": {}}
    issues = CallResultAuthorityMixin._operation_result_currentness_issues(
        runtime, envelope, operation,
    )
    assert any(item["code"] == "plan_synth_structural_output_forbidden" for item in issues)
    assert (runtime.state_dir / control["ref"]).read_bytes() == raw_control_before
    historical = deepcopy(operation)
    historical["result_identity"].pop("plan_synth_verdict_only")
    issues = CallResultAuthorityMixin._operation_result_currentness_issues(runtime, envelope, historical)
    assert not any(item["code"] == "plan_synth_structural_output_forbidden" for item in issues)


def test_verdict_only_leaves_metadata_history_and_empty_structure_available():
    result = {**_verdict(), "task_map": {}, "plan_ports": [], "plan_md": "",
              "plan_content_profile": "full", "evidence_refs": ["artifacts/old.json"],
              "owner_decision_items": [{"question": "Owner decision remains pending"}]}
    assert plan_synth_output_binding_issues({"plan_synth_verdict_only": True}, result) == []


def test_pure_prd_synth_briefing_has_no_structural_scratch(tmp_path):
    import json
    from tests.test_reader_fanout_runtime import _state, _start_fanout, _manifest

    state_dir, log, _transport, orch = _state(tmp_path, synth=True)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")
    manifest = _manifest(state_dir, fanout_id)
    manifest["aggregate_config"]["success_event"] = "task_map.ready"
    role = next(role for role in orch.config.roles if role.name == "review-synth")
    scratch_ref = "tmp/result-submit/plan-synth/result.json"
    orch._write_fanout_synth_briefing(
        role=role, manifest=manifest, run_id="synth-run",
        call_payload={"operation_id": "plan-synth", "result_scratch_ref": scratch_ref},
    )
    scratch = json.loads((state_dir / scratch_ref).read_text())
    assert "plan_md" not in scratch["report"]
    assert "task_map_ref" not in scratch["report"]
    assert "plan_artifact_ref" not in scratch
