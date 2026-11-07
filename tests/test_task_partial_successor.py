"""A failed partial Task can continue while unrelated Candidate work advances."""

from types import SimpleNamespace

import pytest

from tests.test_task_pipeline_partial_continuation import setup, git
from zf.core.events import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_partial_successor import partial_successor_proof
from zf.runtime.task_pipeline_terminal import task_pipeline_workspace_base
from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError
from zf.runtime.candidate_incremental import (
    CandidateIncrementalError,
    _select_integration_base,
)
from zf.runtime.candidates import CandidateRebuilder
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_refs import TaskRefManager


def scenario(tmp_path, fault=""):
    state, log, writer, config, _, base, partial = setup(tmp_path, fault)
    git(tmp_path, "checkout", "-qb", "candidate/FEATURE", base)
    (tmp_path / "concurrent.txt").write_text("other task\n")
    git(tmp_path, "add", "--", "concurrent.txt")
    if fault == "conflict":
        (tmp_path / "product.txt").write_text("concurrent product edit\n")
        git(tmp_path, "add", "--", "product.txt")
    git(tmp_path, "commit", "-qm", "concurrent task")
    candidate = git(tmp_path, "rev-parse", "HEAD")
    task = Task(id="NEXT", title="continue", key="FEATURE:NEXT")
    task.contract.feature_id = "FEATURE"
    task.contract.contract_revision = "next-contract"
    task.contract.scope = ["product.txt", "gap.txt"]
    task.contract.source_ref = "git:" + partial
    task.contract.evidence_contract = {
        "supersedes_task_ids": ["PART"],
        "workflow_run_id": "run",
    }
    task.contract.validation = {
        "commands": [
            {
                "id": "all-files",
                "command": "test -f concurrent.txt && test -f gap.txt && test -f product.txt && grep -q 'partial implementation' product.txt",
                "rolling_smoke": True,
                "tier": "runtime",
            }
        ]
    }
    TaskStore(state / "kanban.json").add(task)
    writer.append(
        ZfEvent(type="task.created", task_id="NEXT", payload={"feature_id": "FEATURE"})
    )
    writer.append(
        ZfEvent(
            type="candidate.updated",
            origin="kernel",
            payload={
                "incremental": True,
                "workflow_run_id": "run",
                "candidate_head": candidate,
            },
        )
    )
    runtime = SimpleNamespace(state_dir=state, project_root=tmp_path, event_log=log)
    context = {"workflow_run_id": "run", "dispatch_base_commit": partial}
    return runtime, task, context, writer, config, candidate, partial


def test_workspace_reuses_verified_partial_without_publishing_it(tmp_path):
    runtime, task, context, _, _, candidate, partial = scenario(tmp_path)
    assert (
        task_pipeline_workspace_base(runtime, task=task, generation_context=context)
        == partial
    )
    assert git(tmp_path, "rev-parse", "candidate/FEATURE") == candidate
    assert git(tmp_path, "show", candidate + ":product.txt") == "base"


@pytest.mark.parametrize(
    "fault",
    [
        "missing_admission",
        "branch_moved",
        "corrupt_result",
        "newer_impl",
        "scope",
        "binding",
        "run",
        "predecessor",
    ],
)
def test_partial_successor_does_not_relax_unproven_divergence(tmp_path, fault):
    runtime, task, context, _, _, candidate, partial = scenario(tmp_path, fault)
    if fault == "scope":
        task.contract.scope = ["gap.txt"]
    if fault == "binding":
        task.contract.source_ref = "git:" + candidate
    if fault == "run":
        context["workflow_run_id"] = "another-run"
    if fault == "predecessor":
        task.contract.evidence_contract["supersedes_task_ids"] = ["unrelated"]
    TaskStore(runtime.state_dir / "kanban.json").update(task.id, contract=task.contract)
    if fault == "run":
        assert not partial_successor_proof(
            runtime,
            task_id=task.id,
            partial_head=partial,
            candidate_head=candidate,
            workflow_run_id="another-run",
        )
    else:
        with pytest.raises(TaskPipelineRuntimeError, match="base diverged"):
            task_pipeline_workspace_base(runtime, task=task, generation_context=context)


@pytest.mark.parametrize("conflict", [False, True])
def test_integration_preserves_candidate_partial_and_worker_delta(tmp_path, conflict):
    runtime, task, context, writer, config, candidate, partial = scenario(
        tmp_path, "conflict" if conflict else ""
    )
    git(tmp_path, "checkout", "-qb", "worker/next", partial)
    (tmp_path / "gap.txt").write_text("completed gap\n")
    git(tmp_path, "add", "--", "gap.txt")
    git(tmp_path, "commit", "-qm", "complete gap")
    head = git(tmp_path, "rev-parse", "HEAD")
    result = TaskRefManager(
        state_dir=runtime.state_dir, project_root=tmp_path, config=config
    ).process_dev_build_done(
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="NEXT",
            payload={
                "source_commit": head,
                "source_branch": "worker/next",
                "base_git_head": partial,
                "feature_id": "FEATURE",
                "files_touched": ["gap.txt"],
            },
        )
    )
    assert result is not None and result.status == "updated"
    rebuilder = CandidateRebuilder(
        state_dir=runtime.state_dir,
        project_root=tmp_path,
        config=config,
        event_log=runtime.event_log,
    )
    result = rebuilder.integrate_task_pipeline_task(
        task_id="NEXT",
        workflow_run_id="run",
        task_map_generation="next-map",
        operation_generation=1,
        pipeline_key="next-pipeline",
        dispatch_base_commit=partial,
        contract_revision="next-contract",
        event_writer=writer,
        causation_id="verify-admitted",
    )
    if conflict:
        assert result.status != "integrated"
        assert git(tmp_path, "rev-parse", "candidate/FEATURE") == candidate
        assert (
            git(tmp_path, "show", candidate + ":product.txt")
            == "concurrent product edit"
        )
        return
    assert result.status == "integrated", result
    receipt = hydrate_sidecar_ref(
        runtime.state_dir, result.payload["receipt_ref"]
    ).payload
    assert receipt["previous_candidate_head"] == candidate
    assert receipt["integration_base_head"] == candidate
    assert receipt["partial_base_replayed"] is True
    assert receipt["adopted_base_commits"] == [partial]
    new = receipt["new_candidate_head"]
    assert git(tmp_path, "show", new + ":product.txt") == "partial implementation"
    assert git(tmp_path, "show", new + ":gap.txt") == "completed gap"
    assert git(tmp_path, "show", new + ":concurrent.txt") == "other task"
    assert git(tmp_path, "merge-base", "--is-ancestor", candidate, new) == ""
    # Arbitrary task source history still cannot enter through this exception.
    with pytest.raises(CandidateIncrementalError, match="diverges"):
        _select_integration_base(
            rebuilder,
            task_id="NEXT",
            task_source_commit=candidate,
            expected_candidate_head=candidate,
            dispatch_base_commit=partial,
        )
