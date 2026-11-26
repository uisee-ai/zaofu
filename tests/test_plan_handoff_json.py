"""Planner output must be readable from its exact frozen bytes by the next role."""
import hashlib
import json

import pytest

from zf.runtime.artifact_text_codec import decode_artifact_text
from zf.runtime.result_submit import ResultSubmitError
from tests.test_plan_candidate_diagnosis_review import active as diagnosis_case
from tests.test_result_submit import _plan_candidate, _running_plan_candidate_operation


@pytest.mark.parametrize(("location", "second", "relationship"), [
    ("task_map_ref", '"task-map.v1"', "identical values"),
    ("artifact_refs", "false", "conflicting values"),
])
def test_authors_repair_duplicate_handoff_json_before_admission(tmp_path, monkeypatch, location, second, relationship):
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    monkeypatch.chdir(runtime.project_root)
    body = _plan_candidate()
    name = "task_map.json" if location == "task_map_ref" else "appendix.json"
    path = runtime.project_root / name
    if location == "task_map_ref":
        clean = json.dumps(body.pop("task_map")).encode()
        raw = b'{"schema_version":' + second.encode() + b',' + clean[1:]
        field = "$.schema_version"
        body[location] = name
    else:
        raw = b'{"sections":[{"answer":0,"answer":' + second.encode() + b'}]}'
        clean = b'{"sections":[{"answer":false}]}'
        field = "$.sections[0].answer"
        body[location] = [name]
    path.write_bytes(raw)
    credentials = dict(operation_id=prepared.operation_id, semantic_result=body,
                       role_instance="planner", credential=token)
    before = [event.id for event in runtime.event_log.read_all()]
    result = service.validate_plan_candidate(**credentials, project_root=runtime.project_root)
    assert result["status"] == "failed"
    errors = [item for item in result["errors"] if item["code"] == "plan_handoff_json_invalid"]
    assert any(name in item["message"] and field in item["message"] and relationship in item["message"]
               for item in errors)
    assert [event.id for event in runtime.event_log.read_all()] == before
    with pytest.raises(ResultSubmitError) as rejected:
        service.submit(**credentials)
    assert rejected.value.code == "plan_candidate_not_admitted"
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())
    assert path.read_bytes() == raw
    with pytest.raises(ValueError, match="duplicate key"):
        decode_artifact_text(raw, "$")
    path.write_bytes(clean)
    assert service.validate_plan_candidate(**credentials, project_root=runtime.project_root)["status"] == "passed"
    assert service.submit(**credentials).canonical_event_type == "issue.triage.child.completed"


def test_prepared_json_with_duplicate_key_stops_critic_dispatch(tmp_path):
    from tests.test_plan_candidate_preflight import _PlanSynthHarness

    raw = b'{"sections":[{"stage":"native","stage":"alternate"}]}'
    report = _plan_candidate()
    harness = _PlanSynthHarness(tmp_path, reports=[{"child_id": "planner", "report": report}])
    path = harness.state_dir / "artifacts" / "fanouts" / "plan" / "planner" / "appendix.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    report["artifact_refs"] = [str(path.relative_to(harness.state_dir))]
    harness._dispatch_fanout_synth("fanout-plan", {
        "fanout_id": "fanout-plan", "trace_id": "run-plan", "stage_id": "issue-triage",
        "trigger_event_id": "evt-input", "trigger_payload": {"flow_kind": "issue", "issue_ref": "docs/issue.md"},
        "aggregate_config": {"success_event": "task_map.ready", "failure_event": "issue.plan.failed",
                             "synth_role": "plan-critic"}, "children": [],
    }, "wait_for_all", "plan-critic")
    assert not harness.critic_dispatch_attempted
    assert harness.finalized.payload["failure_class"] == "plan_candidate_preflight"
    assert "plan_handoff_json_invalid" in json.dumps(harness.finalized.payload)
    assert path.read_bytes() == raw


def test_invalid_trailing_content_does_not_hide_preexisting_duplicate():
    with pytest.raises(ValueError, match="duplicate key"):
        decode_artifact_text(b'{"a":1,"a":2} trailing', "$")


def test_handoff_json_uses_port_size_limit_and_keeps_unstructured_sources(tmp_path, monkeypatch):
    from zf.runtime import plan_candidate_ports as ports

    monkeypatch.setattr(ports, "DEFAULT_HYDRATE_LIMIT_BYTES", 96)
    for name, data in {
        "large.md": b"[A Markdown link](file)" * 200,
        "large.json": b'{"p":"' + b"x" * 200 + b'"}',
        "document": b'{"a":true,"a":1}',
        "small.txt": b'[A link](file)',
    }.items():
        (tmp_path / name).write_bytes(data)
    errors = ports.handoff_json_errors(
        [{"artifact_refs": ["large.md", "large.json", "document", "small.txt"]}],
        state_dir=tmp_path / "state", project_root=tmp_path,
    )
    assert [(e["code"], e["message"].split(":", 1)[0]) for e in errors] == [
        ("plan_port_too_large", "plan port 'large.json' exceeds sidecar hydration limit"),
        ("plan_handoff_json_invalid", "document"),
    ]


@pytest.mark.parametrize("kind", ["owner_review", "diagnosis_review"])
def test_review_only_candidates_require_readable_declared_sources(diagnosis_case, kind):
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
    from tests.test_plan_candidate_owner_review import candidate

    runtime, _, _, _, request, diagnosis = diagnosis_case
    report = candidate(runtime.project_root) if kind == "owner_review" else diagnosis
    path = runtime.project_root / "source.json"
    raw = path.read_bytes().replace(b'{"schema_version":', b'{"schema_version":"legacy","schema_version":', 1)
    path.write_bytes(raw)
    report["source_index_sha256"] = hashlib.sha256(raw).hexdigest()
    outcome = evaluate_plan_candidate_preflight(
        state_dir=runtime.state_dir, project_root=runtime.project_root,
        reports=[{"report": report}], manifest=request["plan_candidate_validation"]["manifest"], metadata={},
    )
    assert outcome["candidate_kind"] == kind and outcome["execution_ready"] is False
    assert outcome["status"] == "failed"
    assert any(e["code"] == "plan_handoff_json_invalid" for e in outcome["errors"])
