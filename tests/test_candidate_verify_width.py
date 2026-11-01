import copy

import pytest

from tests.test_workflow_registered_controller import assembly, _candidate, _admit
from zf.core.config.loader import load_config
from zf.core.config.task_pipeline_profile import TaskPipelineProfileError
from tests.test_task_pipeline_profile import _compile, _task_pipeline
from zf.runtime.workflow_synthesis import WorkflowSynthesisError


@pytest.mark.parametrize("width", [1, 2, 8])
def test_registered_candidate_width_preserves_pools_and_all_acceptance_gates(assembly, width):
    assembly[3]["short_flow_spec"]["parameters"]["candidate_verify_width"] = width
    _, _, path = _candidate(assembly)
    config = load_config(path)
    stage = next(s for s in config.workflow.stages if s.trigger == "candidate.ready")
    assert stage.roles == [f"verify-lane-{i}" for i in range(width)]
    assert stage.aggregate.child_success_event == "verify.child.completed"
    assert stage.aggregate.success_event == "test.passed"
    metadata = config.workflow.flow_metadata
    assert metadata["task_pipeline"]["candidate"]["verify_width"] == width
    assert metadata["task_pipeline"]["pools"]["verify"]["capacity"] == 8
    assert metadata["task_pipeline"]["pools"]["impl"]["capacity"] == 8
    assert metadata["product_acceptance"]["mode"] == "blocking"
    assert any(s.trigger == "flow.discovery.requested" for s in config.workflow.stages)
    assert any(s.trigger == "flow.goal.closed" for s in config.workflow.stages)
    assert config.quality_gates["static"].required_checks == assembly[3]["short_flow_spec"]["parameters"]["verification_commands"]
    from tests.test_reader_fanout_runtime import _state
    from zf.core.config.schema import RoleConfig
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter
    runtime_root = path.parent / "mock-runtime"
    runtime_root.mkdir()
    _, log, transport, runtime = _state(runtime_root)
    runtime.config.roles = [RoleConfig(name=name, instance_id=name, backend="mock", role_kind="reader")
                            for name in stage.roles]
    runtime.config.workflow.stages[0].roles = stage.roles
    trigger = EventWriter(log).append(ZfEvent(type="candidate.ready", correlation_id="run-width",
        payload={"candidate_head_commit": "a" * 40, "pdd_id": "F-width"}))
    runtime.run_once(events=[trigger])
    assert len(transport.sent) == width
    runtime.run_once(events=[trigger])
    assert len(transport.sent) == width  # replay cannot create extra verifiers


@pytest.mark.parametrize("width", [0, 9, True, "1", 1.5])
def test_synthesis_cannot_select_zero_or_unavailable_verifiers(assembly, width):
    result = copy.deepcopy(assembly[3])
    result["short_flow_spec"]["parameters"]["candidate_verify_width"] = width
    with pytest.raises(WorkflowSynthesisError, match="candidate_verify_width"):
        _admit(assembly, result)


@pytest.mark.parametrize("mode,width", [("shadow", 1), ("blocking", 0), ("blocking", 3), ("blocking", True)])
def test_direct_yaml_cannot_bypass_candidate_width_bounds(mode, width):
    raw = _task_pipeline(mode=mode)
    raw["candidate"]["verifyWidth"] = width
    with pytest.raises(TaskPipelineProfileError):
        _compile(raw)


def test_width_is_scoped_to_its_flow_in_a_multi_flow_project(tmp_path):
    import yaml
    from zf.cli.flow import draft_multi_kind_project_spec

    docs = draft_multi_kind_project_spec(backend="mock", project_name="multi", project_root=tmp_path)
    for doc in docs:
        if doc.get("kind") not in {"IssueFlow", "PrdFlow"}:
            continue
        kind = "issue" if doc["kind"] == "IssueFlow" else "prd"
        raw = _task_pipeline(mode="blocking")
        raw["candidate"]["verifyWidth"] = 1 if kind == "prd" else 2
        doc["spec"].update(lanes=2, topology="fanout", flowProfile=f"{kind}-flow-v4-task-pipeline", taskPipeline=raw)
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all(docs))
    config = load_config(path)
    stages = {stage.flow_kind: stage for stage in config.workflow.stages
              if stage.trigger == "candidate.ready"}
    assert len(stages["prd"].roles) == 1
    assert len(stages["issue"].roles) == 2
    assert len(stages["refactor"].roles) == 5
