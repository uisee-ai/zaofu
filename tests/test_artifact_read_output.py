"""Output reuse never widens file, selection, or invocation identity."""
from copy import deepcopy

import pytest

from zf.runtime.artifact_read_output import RequiredReadOutput


def _result():
    return {"content": "complete body", "read": {
        "source_id": "task_map", "artifact_id": "task_map",
        "artifact_ref": "artifacts/plan.json", "artifact_sha256": "a" * 64,
        "json_path": "$.tasks[1]", "max_items": 0, "max_chars": 0,
        "status": "read", "truncated": False,
    }}


@pytest.mark.parametrize("field,value", [
    ("artifact_ref", "artifacts/other.json"),
    ("artifact_sha256", "b" * 64),
    ("json_path", "$.tasks[2]"), ("max_items", 1), ("max_chars", 12),
    ("content", "different body"), ("truncated", True),
])
def test_different_read_identity_does_not_share_body(tmp_path, field, value):
    output = RequiredReadOutput(tmp_path)
    first = _result()
    second = deepcopy(first)
    (second if field == "content" else second["read"])[field] = value
    output.render(first)
    assert "content" in output.render(second)


def test_same_resolved_file_alias_can_share_only_within_one_output(tmp_path):
    first = _result()
    second = deepcopy(first)
    second["read"].update(source_id="planning_result", artifact_id="planning_result",
                          artifact_ref=str(tmp_path / "artifacts/plan.json"))
    output = RequiredReadOutput(tmp_path)
    original = output.render(first)
    alias = output.render(second)
    assert alias["content_ref"] == original["content_id"]
    assert alias["source_id"] == second["read"]["source_id"]
    assert "row_sha256" in alias["read_receipt"]
    assert "content" in RequiredReadOutput(tmp_path).render(second)
