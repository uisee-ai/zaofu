"""Candidate checks must use the input contract of the actual dispatch batch."""
from copy import deepcopy

import pytest

from tests.test_plan_candidate_preflight import _source_index, _task_map
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight


def _evaluate(tmp_path, *, selector=None, policy=True, port="task_execution_input",
              malformed=False, selected_partition=True, local_required=False):
    task_map = _task_map()
    first = task_map["tasks"][0]
    first["wave"] = 1
    second = deepcopy(first)
    second.update(task_id="TASK-2", title="Retained task", goal_claim_ids=[], wave=2,
                  allowed_paths=["src/retained.py", "tests/test_retained.py"])
    second["validation"]["commands"][0].update(
        id="test-retained", command="pytest -q tests/test_retained.py", acceptance_ids=["AC-2"])
    second["acceptance_criteria"][0].update(id="AC-2", verification_command_ids=["test-retained"])
    task_map["tasks"].append(second)
    task_map["required_plan_ports"] = ["task_execution_input"]
    if policy:
        for task in task_map["tasks"]:
            task["evidence_contract"] = {"plan_input_selection": {
                "schema_version": "task-plan-input-selection.v1",
                "implementation": ["task_execution_input"],
                "task-verify": ["task_execution_input"],
            }}
    if local_required:
        evidence = first.setdefault("evidence_contract", {})
        evidence["required_plan_ports"] = [port]
        if policy:
            evidence["plan_input_selection"]["task-verify"].append(port)
    index = _source_index()
    index["tasks"].append({**index["tasks"][0], "task_id": "TASK-2"})
    body = {"schema_version": "task-scoped-plan-input.v1", "shared": {}, "tasks": (
        [{"task_id": "TASK-1", "body": {"requirement": "Full selected input"}}]
        if selected_partition else [])}
    if malformed:
        body["tasks"] = "invalid"
    ports = [{"logical_name": port, "body": body}]
    if port != "task_execution_input":
        ports.append({"logical_name": "task_execution_input", "body": {
            "schema_version": "task-scoped-plan-input.v1", "shared": {},
            "tasks": [{"task_id": t["task_id"], "body": {"requirement": t["title"]}}
                      for t in task_map["tasks"]],
        }})
    if policy:
        task_map["required_plan_ports"] += ["execution_spec", "acceptance_matrix",
                                             "test_matrix", "real_e2e_matrix"]
        for name, rows_key, rows in [
            ("execution_spec", "tasks", [{"task_id": t["task_id"]} for t in task_map["tasks"]]),
            ("acceptance_matrix", "acceptance", [
                {**ac, "task_id": t["task_id"]} for t in task_map["tasks"]
                for ac in t["acceptance_criteria"]]),
            ("test_matrix", "commands", [cmd for t in task_map["tasks"]
                                           for cmd in t["validation"]["commands"]]),
            ("real_e2e_matrix", "rows", []),
        ]:
            ports.append({"logical_name": name, "body": {"status": "ready",
                          "metadata": {"enrichment_contract": {"status": "fulfilled"}},
                          rows_key: rows}})
    return evaluate_plan_candidate_preflight(
        state_dir=tmp_path / "state", project_root=tmp_path,
        reports=[{"report": {"task_map": task_map, "source_index": index,
                              "plan_md": "# Plan", "plan_ports": ports}}],
        manifest={"trigger_payload": {"flow_kind": "issue", "issue_ref": "requirements.md",
                                       **(selector or {})}},
        metadata={"flow_kind": "issue"},
    )


@pytest.mark.parametrize("selector", [{"task_ids": ["TASK-1"]}, {"wave": 1}])
def test_actual_preflight_checks_only_selected_dispatch_inputs(tmp_path, selector):
    result = _evaluate(tmp_path, selector=selector)
    assert result["status"] == "passed", result["errors"]


@pytest.mark.parametrize("policy", [True, False])
def test_actual_preflight_ignores_unconsumed_historical_port(tmp_path, policy):
    result = _evaluate(tmp_path, policy=policy, port="historical_supplement")
    assert result["status"] == "passed", result["errors"]


@pytest.mark.parametrize("kwargs", [
    {},  # Full dispatch still needs both partitions.
    {"selector": {"task_ids": ["TASK-1"]}, "selected_partition": False},
    {"port": "historical_supplement", "malformed": True},
])
def test_actual_preflight_keeps_partition_and_structure_gates(tmp_path, kwargs):
    result = _evaluate(tmp_path, **kwargs)
    assert "task_scoped_input_invalid" in {error["code"] for error in result["errors"]}


def test_unknown_dispatch_task_remains_rejected(tmp_path):
    result = _evaluate(tmp_path, selector={"task_ids": ["unknown"]})
    assert "writer_fanout_task_selection_invalid" in {e["code"] for e in result["errors"]}


@pytest.mark.parametrize("policy", [True, False])
def test_verify_only_and_legacy_local_inputs_remain_required(tmp_path, policy):
    result = _evaluate(tmp_path, selector={"task_ids": ["TASK-1"]}, policy=policy,
                       port="verify_supplement", selected_partition=False, local_required=True)
    assert any(e["code"] == "task_scoped_input_invalid"
               and e["field"] == "plan_ports.verify_supplement" for e in result["errors"])
