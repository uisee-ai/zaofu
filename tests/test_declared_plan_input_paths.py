"""Planner-owned read partitions preserve native artifact schemas and content."""

import copy

import pytest

from zf.runtime.task_scoped_plan_inputs import task_input_paths


def artifact():
    return {
        "schema_version": "test-matrix.v1",
        "policy": {"rule": "deny revoked access"},
        "rows": [{"id": "a", "body": "complete A"}, {"id": "b", "body": "complete B"}],
        "task_input_policy": {
            "schema_version": "task-input-policy.v1",
            "shared_paths": ["$.schema_version", "$.policy"],
            "tasks": [
                {"task_id": "A", "paths": ["$.rows[0]"]},
                {"task_id": "B", "paths": ["$.rows[1]"]},
            ],
        },
    }


def test_native_schema_and_complete_rows_preserved():
    body = artifact()
    original = copy.deepcopy(body)
    assert task_input_paths(body, task_id="B") == ["$.schema_version", "$.policy", "$.rows[1]"]
    assert task_input_paths(body, task_id="", candidate=True) == ["$"]
    assert body == original


@pytest.mark.parametrize("candidate", [False, True])
def test_unassigned_content_fails_closed(candidate):
    body = artifact()
    body["hidden_rule"] = "must remain visible"
    with pytest.raises(ValueError, match="unassigned"):
        task_input_paths(body, task_id="A", candidate=candidate)


@pytest.mark.parametrize("path", ["$.missing", "$.rows[2]", "$.rows[*]", "$.rows[0]junk", "$.task_input_policy"])
def test_invalid_or_policy_self_reference_is_rejected(path):
    body = artifact()
    body["task_input_policy"]["tasks"][0]["paths"] = [path]
    with pytest.raises(ValueError):
        task_input_paths(body, task_id="A")


def test_missing_and_duplicate_task_rejected():
    body = artifact()
    with pytest.raises(ValueError, match="no partition"):
        task_input_paths(body, task_id="missing")
    body["task_input_policy"]["tasks"].append(body["task_input_policy"]["tasks"][0])
    with pytest.raises(ValueError, match="unique"):
        task_input_paths(body, task_id="A")
