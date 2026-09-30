"""An explicit request narrows writes without granting writes to probe inputs."""

import pytest

from zf.runtime.light_flow import synthesize_light_task_map
from zf.runtime.task_map import validate_task_map_payload


@pytest.mark.parametrize("target", [".", "/tmp/project", "app"])
def test_light_task_map_uses_explicit_scope_not_whole_target(target):
    scope = ["app/answer.txt"] if target == "app" else ["answer.txt"]
    probe = "app/tests/verify.py" if target == "app" else "tests/verify.py"
    result = synthesize_light_task_map(
        pdd_id="ISSUE-SCOPED", objective="Repair only the declared answer",
        prd_ref="docs/issue.md", target_root=target, scope=scope,
        verification_commands=[f"python {probe}"], flow_kind="issue",
    )
    [task] = result["tasks"]
    assert task["allowed_paths"] == scope
    assert "README.md" not in task["allowed_paths"]
    assert task["verification_read_paths"] == (
        ["app/**", "README.md"] if target == "app" else ["**", "README.md"]
    )
    assert validate_task_map_payload(result).passed
