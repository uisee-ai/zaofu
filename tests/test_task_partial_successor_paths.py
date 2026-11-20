"""Git path spelling must survive partial-work scope checks and replay."""

import pytest

from tests.test_task_partial_successor import scenario
from tests.test_task_pipeline_partial_continuation import git
from zf.core.task.store import TaskStore
from zf.runtime.candidates import CandidateRebuilder
from zf.runtime.task_partial_successor import (
    apply_partial_successor_commit,
    partial_successor_proof,
)
from zf.runtime.task_pipeline_terminal import task_pipeline_workspace_base


@pytest.mark.parametrize("path", [
    "app/evidence/privacy/真实浏览器验收.webm",
    'app/evidence/privacy/quote"tab\tline\ncarriage\rreceipt.json',
    " evidence with spaces.txt ",
])
@pytest.mark.parametrize("allowed", [True, False])
def test_partial_paths_keep_exact_names_and_scope(tmp_path, path, allowed):
    runtime, task, context, _, config, candidate, partial = scenario(
        tmp_path, product_path=path,
    )
    # Force Git's normal quoted display regardless of the developer's config.
    git(tmp_path, "config", "core.quotePath", "true")
    if not allowed:
        task.contract.scope = ["gap.txt"]
        TaskStore(runtime.state_dir / "kanban.json").update(task.id, contract=task.contract)
    rebuilder = CandidateRebuilder(state_dir=runtime.state_dir, project_root=tmp_path,
                                   config=config, event_log=runtime.event_log)
    assert rebuilder._commit_files(partial) == {path}
    assert rebuilder._paths_existing_at_commit(partial, [path]) == {path}
    proof = partial_successor_proof(runtime, task_id=task.id, partial_head=partial,
                                   candidate_head=candidate, workflow_run_id="run")
    target = tmp_path / "replay"
    git(tmp_path, "worktree", "add", "--detach", str(target), candidate)
    if not allowed:
        assert proof == {}
        with pytest.raises(ValueError, match="exceeds its admitted scope"):
            apply_partial_successor_commit(rebuilder, task_id=task.id,
                                           worktree=target, commit=partial)
        assert git(target, "rev-parse", "HEAD") == candidate
        return
    assert proof["partial_paths"] == [path]
    combined = task_pipeline_workspace_base(runtime, task=task, generation_context=context)
    assert git(tmp_path, "show", combined + ":" + path) == "partial implementation"
    assert apply_partial_successor_commit(rebuilder, task_id=task.id,
                                         worktree=target, commit=partial) == "applied"
    assert (target / path).read_text() == "partial implementation\n"
    assert (target / "concurrent.txt").read_text() == "other task\n"
    assert git(tmp_path, "rev-parse", "candidate/FEATURE") == candidate
