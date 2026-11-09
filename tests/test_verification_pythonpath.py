"""Python import search roots remain separate, scoped verification inputs."""

import pytest

from zf.runtime.task_map import validate_task_map_payload
from zf.runtime.verification_path_refs import command_path_refs


@pytest.mark.parametrize("prefix", ["env ", ""])
def test_task_map_accepts_each_declared_pythonpath_component(prefix):
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "QUALITY-COMPARISON", "title": "Compare reports", "wave": 1,
            "allowed_paths": ["app/tests/quality/**"],
            "allowed_paths_reason": "Owns comparison tests.",
            "verification_read_paths": ["app", "app/tools", "app/.venv/**"],
            "validation": {"commands": [{
                "id": "comparison", "owner": "task_verify", "tier": "runtime",
                "command": prefix + "PYTHONPATH=app:app/tools PYTHONDONTWRITEBYTECODE=1 "
                "app/.venv/bin/python -m pytest app/tests/quality/test_comparison.py -q",
            }]},
        }],
    })
    assert result.passed, result.errors


@pytest.mark.parametrize("unowned", ["private", ".private", "..", "../private", "/tmp/private"])
def test_task_map_rejects_unlisted_pythonpath_component(unowned):
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "QUALITY-COMPARISON", "title": "Compare reports", "wave": 1,
            "allowed_paths": ["app/tests/quality/**"],
            "allowed_paths_reason": "Owns comparison tests.",
            "verification_read_paths": ["app/tools"],
            "verification": f"env PYTHONPATH=app/tools:{unowned} "
            "python -m pytest app/tests/quality/test_comparison.py",
        }],
    })
    assert not result.passed
    assert any("references path outside" in error and unowned in error
               for error in result.errors)


def test_quoted_pythonpath_keeps_space_in_component_and_deduplicates():
    assert command_path_refs(
        "env PYTHONPATH='app/library helpers:app/tools:app/tools' python -m pytest"
    ) == ["app/library helpers", "app/tools"]


def test_non_path_list_assignment_preserves_colon_path():
    assert command_path_refs("env FIXTURE=app/data:fixture.json python -m pytest") == [
        "app/data:fixture.json",
    ]
