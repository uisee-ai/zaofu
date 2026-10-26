"""Plan inspection explains dependency paths without authorizing dispatch."""

import json

import pytest

from zf.runtime.plan_execution_review import review_execution


def _review(tasks):
    return review_execution({"tasks": tasks}, ports={}, policy={}, metadata={})


def test_diamond_explains_longest_path_and_parallel_levels_without_mutation():
    tasks = [
        {"task_id": "read", "blocked_by": ["contract"]},
        {"task_id": "contract", "blocked_by": []},
        {"task_id": "audit", "blocked_by": ["contract"]},
        {"task_id": "integrate", "blocked_by": ["read", "audit"]},
    ]
    before = json.dumps(tasks)
    graph = _review(tasks)["graph"]
    assert graph["longest_dependency_path"] == ["contract", "audit", "integrate"]
    assert graph["tasks_by_level"] == {1: ["contract"], 2: ["audit", "read"], 3: ["integrate"]}
    assert graph["direct_dependencies"]["integrate"] == ["audit", "read"]
    assert graph["direct_dependents"]["contract"] == ["audit", "read"]
    assert graph == _review(list(reversed(tasks)))["graph"]
    assert json.dumps(tasks) == before
    assert "verdict" not in _review(tasks)


@pytest.mark.parametrize("tasks,missing", [
    ([{"task_id": "a", "blocked_by": ["absent"]},
      {"task_id": "b", "blocked_by": ["a"]}], ["absent"]),
    ([{"task_id": "a", "blocked_by": ["b"]},
      {"task_id": "b", "blocked_by": ["a"]}], []),
])
def test_unresolved_graph_has_no_complete_path_but_keeps_dependency_reasons(tasks, missing):
    graph = _review([*tasks, {"task_id": "independent", "blocked_by": []}])["graph"]
    assert graph["longest_dependency_path"] is None
    assert graph["longest_dependency_chain"] is None
    assert graph["unresolved_task_ids"] == ["a", "b"]
    assert graph["missing_dependencies"] == missing
    assert graph["tasks_by_level"] == {1: ["independent"]}
    assert graph["direct_dependencies"]["a"] == tasks[0]["blocked_by"]


def test_empty_graph_has_empty_path_and_levels():
    graph = _review([])["graph"]
    assert graph["longest_dependency_path"] == []
    assert graph["tasks_by_level"] == {}
    assert graph["direct_dependencies"] == {}
    assert graph["direct_dependents"] == {}


def test_deep_graph_inspection_does_not_recurse():
    tasks = [{"task_id": f"task-{i}", "blocked_by": [f"task-{i-1}"] if i else []}
             for i in range(1200)]
    graph = _review(tasks)["graph"]
    assert graph["longest_dependency_path"] == [t["task_id"] for t in tasks]
    assert graph["longest_dependency_chain"] == len(tasks)


def test_cli_returns_explanations_without_runtime_state(tmp_path, capsys):
    from zf.cli.main import main

    config = tmp_path / "zf.yaml"
    config.write_text("project:\n  name: dependency-review\n  state_dir: runtime-state\n")
    task_map = tmp_path / "task_map.json"
    task_map.write_text(json.dumps({"tasks": [
        {"task_id": "contract", "blocked_by": []},
        {"task_id": "read", "blocked_by": ["contract"]},
    ]}))
    assert main(["plan", "inspect-execution", "--config", str(config),
                 "--task-map", str(task_map), "--flow-kind", "prd"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["graph"]["longest_dependency_path"] == ["contract", "read"]
    assert report["graph"]["direct_dependents"]["contract"] == ["read"]
    assert report["scope"] == "mechanical_facts_for_semantic_review"
    assert not (tmp_path / "runtime-state").exists()
