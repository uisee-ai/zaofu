"""Plan diagnostics must describe the integration consumer, not prose ordering."""
import json
from types import SimpleNamespace

import pytest

from zf.core.config.schema import ZfConfig
from zf.runtime.candidate_incremental import CandidateIncrementalError, _run_rolling_smoke
from zf.runtime.plan_execution_review import planning_execution_context, review_execution
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize("self_contained", [False, True])
def test_review_exposes_only_commands_really_run_in_clean_integration(tmp_path, self_contained):
    worktree = tmp_path / "candidate"
    worktree.mkdir()
    smoke = "test -f built.txt"
    if self_contained:
        smoke = "touch built.txt && test -f built.txt"
    commands = [
        {"id": "build", "command": "touch built.txt", "owner": "task_verify"},
        {"id": "smoke", "command": smoke, "owner": "task_verify", "rolling_smoke": True},
        {"id": "later", "command": "false", "owner": "candidate_verify", "rolling_smoke": False},
    ]
    task = {"task_id": "T", "validation": {"commands": commands}}
    builder = SimpleNamespace(
        state_dir=tmp_path / "state",
        task_store=SimpleNamespace(get=lambda _: SimpleNamespace(
            contract=SimpleNamespace(validation=task["validation"], verification=""))),
    )
    kwargs = dict(task_id="T", worktree=worktree, target_commit="target", source_event_id="source")
    if self_contained:
        refs, _ = _run_rolling_smoke(builder, **kwargs)
        assert [hydrate_sidecar_ref(builder.state_dir, ref).payload["command_id"] for ref in refs] == ["smoke"]
    else:
        with pytest.raises(CandidateIncrementalError, match="rolling smoke failed: smoke"):
            _run_rolling_smoke(builder, **kwargs)
        assert not (worktree / "built.txt").exists()

    report = review_execution({"tasks": [task]}, ports={}, policy={},
                              metadata={"task_pipeline": {"mode": "blocking"}})
    verification = report["verification"]
    assert verification["task_requirements"][0]["rolling_smoke_command_ids"] == ["smoke"]
    assert verification["rolling_smoke_policy"]["implicit_unselected_commands"] is False
    assert verification["rolling_smoke_policy"]["execution_workspace"] == "candidate_worktree"
    assert "verdict" not in report  # Semantic prerequisite judgment stays with the reviewer.


def test_planner_and_critic_receive_same_integration_execution_facts(tmp_path):
    from zf.runtime.plan_execution_review import attach_execution_review

    lines = planning_execution_context(ZfConfig(), flow_kind="prd")
    planner = json.loads(lines[lines.index("```json") + 1])
    review = review_execution({"tasks": [{"task_id": "T"}]}, ports={}, policy={}, metadata={})
    payload = {}
    attach_execution_review(state_dir=tmp_path, payload=payload, review=review, source_event_id="evt")
    ref = next(r for r in payload["input_refs"] if r["source_id"] == "plan-execution-review")
    delivered = hydrate_sidecar_ref(tmp_path, ref).payload
    assert planner["rolling_smoke_policy"] == delivered["verification"]["rolling_smoke_policy"]
    assert planner["rolling_smoke_policy"]["order"] == "command_registry_order"
