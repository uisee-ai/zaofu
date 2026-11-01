"""Chat-only Task requirements reach a registered controller without an invented PRD."""
import copy
import json

import pytest
import yaml

from tests.test_workflow_proposal import _request
from tests.test_workflow_registered_controller import assembly, _candidate
from zf.core.task.schema import Task, TaskContract
from zf.runtime.workflow_request_acceptance import bind_task_workflow_inputs
from zf.runtime.workflow_task_input_artifacts import materialize_task_workflow_input


def rewrite_request(state, fields):
    fixture = state / "workflow-requests/req-1/requirement.json"
    fixture.unlink()
    fixture.parent.rmdir()
    return _request(state, requirement_fields=fields)


def chat_assembly(assembly):
    base, state, _, result, writer = assembly
    task = Task(id="TASK-CHAT", title="Hello", contract=TaskContract(
        behavior='中文姓名输出 "Hello, 张三!\\n"。', scope=["hello.py"],
        acceptance_criteria=["精确核对 stdout 与退出码"], explicit_non_goals=["网络访问"],
    ))
    params, binding, contract, _ = bind_task_workflow_inputs({}, task, task_contract_digest="task-digest")
    materialized = materialize_task_workflow_input(
        workflow_dir=base.parent / "artifacts/workflow/req-1", project_root=base.parent,
        source_ref="", source_refs=params["source_refs"], artifact_refs=[],
        acceptance=params["acceptance"], constraints=params["constraints"],
        scope=params["scope"], binding=binding, input_contract=contract,
    )
    fields = dict(params, kind="prd", source_ref="", target_root=".", confirmed=True,
                  objective=task.contract.behavior, task_input_binding=binding,
                  source_refs=materialized.source_refs,
                  task_input_contract_ref=materialized.contract_ref,
                  task_input_contract_digest=materialized.contract_digest)
    request = rewrite_request(state, fields)
    result = copy.deepcopy(result)
    result.update(requirement_ref=request["requirement_spec_ref"],
                  requirement_digest=request["requirement_spec_digest"])
    return (base, state, request, result, writer), fields


def test_chat_task_materializes_full_confirmed_requirement_through_controller(assembly):
    bound, fields = chat_assembly(assembly)
    _, _, candidate = _candidate(bound)
    flow = next(d for d in yaml.safe_load_all(candidate.read_text()) if d["kind"] == "PrdFlow")
    source = bound[0].parent / flow["spec"]["prdRef"]
    body = json.loads(source.read_text())
    assert body["objective"] == fields["objective"]
    assert body["acceptance"] == fields["acceptance"]
    assert body["constraints"] == fields["constraints"]
    assert body["task_input_binding"] == fields["task_input_binding"]
    assert body["task_input_contract_digest"] == fields["task_input_contract_digest"]
    assert source.is_relative_to(bound[0].parent / "artifacts")
    source.write_text('{"objective":"tampered"}')
    with pytest.raises(ValueError, match="collision"):
        _candidate(bound)


@pytest.mark.parametrize("fault", ["digest", "identity", "escape", "explicit_missing", "unconfirmed"])
def test_chat_source_rejects_unbound_or_invalid_inputs(assembly, fault):
    bound, fields = chat_assembly(assembly)
    if fault == "digest":
        fields["task_input_contract_digest"] = "sha256:" + "0" * 64
    elif fault == "identity":
        fields["task_input_binding"]["task_id"] = "TASK-OTHER"
    elif fault == "escape":
        fields["task_input_contract_ref"] = str(bound[0].parent.parent / "outside.json")
    elif fault == "explicit_missing":
        fields["source_ref"] = "missing.md"
    else:
        fields["confirmed"] = False
    request = rewrite_request(bound[1], fields)
    result = copy.deepcopy(bound[3])
    result.update(requirement_ref=request["requirement_spec_ref"], requirement_digest=request["requirement_spec_digest"])
    with pytest.raises(ValueError):
        _candidate((bound[0], bound[1], request, result, bound[4]))
