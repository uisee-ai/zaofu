"""Project targets must not be mistaken for or replace explicit write scopes."""

from types import SimpleNamespace

import pytest

from zf.core.events import ZfEvent
from zf.runtime.run_isolation import concurrent_isolation_blocker


@pytest.mark.parametrize("on_active", [False, True])
@pytest.mark.parametrize("target,scope,expected", [
    ("absolute", "src/a", ""),
    (".", "src/a", ""),
    ("src", "src/a", ""),
    ("absolute-child", "src/a", ""),
    ("outside", "src/a", "target_root is outside project root"),
    ("../other", "src/a", "target_root is outside project root"),
    ("symlink-outside", "src/a", "target_root is outside project root"),
    ("src", "other/a", "work scope is outside target_root"),
    ("absolute", "", "explicit work scope"),
    ("absolute", "/tmp/escape", "invalid work scope"),
])
def test_concurrent_target_root_is_separate_from_write_scope(
    tmp_path, on_active, target, scope, expected,
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "link").symlink_to(tmp_path / "other", target_is_directory=True)
    runtime = SimpleNamespace(project_root=root, config=SimpleNamespace(
        runtime=SimpleNamespace(workdirs=SimpleNamespace(enabled=True, mode="worktree")),
    ))
    events = [ZfEvent(type="workflow.invoke.requested", task_id=f"TASK-{run}", payload={
        "workflow_run_id": run, "request_id": run, "scope": [f"run/{run}"],
        "effective_config_digest": "shared-config", "run_contract_digest": f"contract-{run}",
    }) for run in ("A", "B")]
    victim = events[0 if on_active else 1]
    victim.payload["scope"] = [scope] if scope else []
    victim.payload["source_refs"] = {"target_root": {
        "absolute": str(root), "absolute-child": str(root / "src"),
        "outside": str(tmp_path / "other"), "symlink-outside": str(root / "link"),
    }.get(target, target)}
    reason = concurrent_isolation_blocker(
        runtime, events[1], active_run_ids=["A"], events=events,
        run_id_for=lambda event: event.payload["workflow_run_id"],
    )
    if expected:
        assert expected in reason
    else:
        assert reason == ""
