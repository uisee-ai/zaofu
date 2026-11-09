"""A failed partial Task can continue while unrelated Candidate work advances."""

from types import SimpleNamespace
from pathlib import Path
from copy import deepcopy

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
    combined = task_pipeline_workspace_base(runtime, task=task, generation_context=context)
    assert git(tmp_path, "show", combined + ":concurrent.txt") == "other task"
    assert git(tmp_path, "show", combined + ":product.txt") == "partial implementation"
    assert git(tmp_path, "merge-base", "--is-ancestor", candidate, combined) == ""
    assert git(tmp_path, "merge-base", "--is-ancestor", partial, combined) == ""
    assert task_pipeline_workspace_base(runtime, task=task, generation_context=context) == combined
    assert git(tmp_path, "rev-parse", "candidate/FEATURE") == candidate
    assert git(tmp_path, "show", candidate + ":product.txt") == "base"


def test_dependency_partial_conflict_does_not_publish_or_modify_worktree(tmp_path):
    runtime, task, context, _, _, candidate, _ = scenario(tmp_path, "conflict")
    before = git(tmp_path, "show-ref")
    status = git(tmp_path, "status", "--porcelain")
    with pytest.raises(TaskPipelineRuntimeError, match="partial.*conflict"):
        task_pipeline_workspace_base(runtime, task=task, generation_context=context)
    assert git(tmp_path, "show-ref") == before
    assert git(tmp_path, "rev-parse", "HEAD") == candidate
    assert git(tmp_path, "status", "--porcelain") == status


@pytest.mark.parametrize("fault", ["", "base", "run", "cycle", "ambiguous", "missing_admission"])
def test_repeated_successor_requires_explicit_same_base_lineage(tmp_path, fault):
    runtime, task, context, _, _, candidate, partial = scenario(
        tmp_path, "missing_admission" if fault == "missing_admission" else "")
    store = TaskStore(runtime.state_dir / "kanban.json")
    prior = deepcopy(task)
    prior.id = "MIDDLE"
    prior.key = "FEATURE:MIDDLE"
    if fault == "base":
        prior.contract.source_ref = "git:" + candidate
    if fault == "run":
        prior.contract.evidence_contract["workflow_run_id"] = "foreign-run"
    if fault == "cycle":
        prior.contract.evidence_contract["supersedes_task_ids"] = [task.id]
    if fault == "ambiguous":
        prior.contract.evidence_contract["supersedes_task_ids"] = ["PART", "OTHER"]
    store.add(prior)
    task.contract.evidence_contract["supersedes_task_ids"] = [prior.id]
    store.update(task.id, contract=task.contract)
    if fault:
        with pytest.raises(TaskPipelineRuntimeError, match="base diverged"):
            task_pipeline_workspace_base(runtime, task=task, generation_context=context)
    else:
        combined = task_pipeline_workspace_base(runtime, task=task, generation_context=context)
        assert git(tmp_path, "show", combined + ":concurrent.txt") == "other task"
        assert git(tmp_path, "show", combined + ":product.txt") == "partial implementation"
        proof = partial_successor_proof(runtime, task_id=task.id, partial_head=partial,
                                       candidate_head=candidate, workflow_run_id="run")
        assert proof["supersedes_task_ids"] == [prior.id]
        assert proof["partial_commits"] == [partial]


@pytest.mark.parametrize("stage", ["impl", "verify"])
def test_dispatch_composes_new_workspace_then_preserves_frozen_base(tmp_path, monkeypatch, stage):
    from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
    from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage

    runtime, task, context, _, config, candidate, partial = scenario(tmp_path)
    config.runtime.workdirs.enabled = True
    config.runtime.workdirs.mode = "worktree"
    config.runtime.workdirs.provision_paths = []
    runtime.config = config
    role = RoleConfig(name="dev", backend="mock", lifecycle=RoleLifecycleConfig(mode="on_demand"))
    runtime._find_role_by_instance = lambda _: role
    context["task_map_generation"] = "next-map"
    captured = []

    class SnapshotBoundary(Exception):
        pass

    def snapshot_boundary(*args, **kwargs):
        captured.append(kwargs["workspace"])
        raise SnapshotBoundary

    monkeypatch.setattr("zf.runtime.task_pipeline_targets.prepare_contract_snapshot", snapshot_boundary)

    def dispatch(current_stage):
        with pytest.raises(SnapshotBoundary):
            dispatch_task_pipeline_stage(runtime, policy={}, task=task,
                assignment={"stage": current_stage, "role_instance": role.instance_id},
                generation_context=context, operation_rows=[], attempt_rows=[])

    dispatch("impl")
    workspace = captured[-1]
    project = Path(workspace.project_path)
    frozen = git(project, "rev-parse", "HEAD")
    assert git(project, "show", "HEAD:concurrent.txt") == "other task"
    assert git(project, "show", "HEAD:product.txt") == "partial implementation"
    assert workspace.base_commit == frozen
    assert git(tmp_path, "merge-base", "--is-ancestor", partial, frozen) == ""
    assert git(tmp_path, "rev-parse", "candidate/FEATURE") == candidate
    (project / "gap.txt").write_text("worker delta\n")
    git(project, "add", "--", "gap.txt")
    git(project, "commit", "-qm", "work after frozen base")
    worker_head = git(project, "rev-parse", "HEAD")
    (project / "uncommitted.txt").write_text("in flight\n")

    # A later Candidate conflicts with the old partial. Existing work must not
    # even attempt to recompose; its frozen base and uncommitted data survive.
    (tmp_path / "product.txt").write_text("later candidate edit\n")
    git(tmp_path, "add", "--", "product.txt")
    git(tmp_path, "commit", "-qm", "later candidate")
    runtime.event_log.append(ZfEvent(type="candidate.updated", origin="kernel", payload={
        "incremental": True, "workflow_run_id": "run",
        "candidate_head": git(tmp_path, "rev-parse", "HEAD"),
    }))
    dispatch(stage)
    assert captured[-1].base_commit == frozen
    assert git(project, "rev-parse", "HEAD") == worker_head
    assert (project / "uncommitted.txt").read_text() == "in flight\n"


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


@pytest.mark.parametrize("conflict,compose,repeated", [
    (False, False, False), (True, False, False), (False, True, False), (False, True, True),
])
def test_integration_preserves_candidate_partial_and_worker_delta(tmp_path, conflict, compose, repeated):
    runtime, task, context, writer, config, candidate, partial = scenario(
        tmp_path, "conflict" if conflict else ""
    )
    if repeated:
        store = TaskStore(runtime.state_dir / "kanban.json")
        prior = deepcopy(task)
        prior.id, prior.key = "MIDDLE", "FEATURE:MIDDLE"
        store.add(prior)
        task.contract.evidence_contract["supersedes_task_ids"] = [prior.id]
        store.update(task.id, contract=task.contract)
    workspace_base = task_pipeline_workspace_base(runtime, task=task, generation_context=context) if compose else partial
    git(tmp_path, "checkout", "-qb", "worker/next", workspace_base)
    if compose:
        assert (tmp_path / "concurrent.txt").read_text() == "other task\n"
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
                "base_git_head": workspace_base,
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
