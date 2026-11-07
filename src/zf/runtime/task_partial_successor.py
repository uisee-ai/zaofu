"""Authorize a committed failed Impl as a successor's isolated starting point."""

from pathlib import Path
from typing import Any

from zf.core.task.store import TaskStore
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_pipeline_failure_continuation import (
    _git,
    task_pipeline_failure_continuation,
)
from zf.runtime.task_refs import _path_allowed_by_scope


def partial_successor_proof(
    runtime: Any,
    *,
    task_id: str,
    partial_head: str,
    candidate_head: str,
    workflow_run_id: str,
) -> dict:
    """Require admitted failure provenance, shared ancestry and complete scope.

    This authorizes carrying code, never completion or publication. Ordinary
    Verify, rolling smoke and Candidate CAS remain responsible for admission.
    """
    if not getattr(runtime, "state_dir", None):
        return {}
    state = Path(runtime.state_dir)
    root = Path(runtime.project_root)
    store = TaskStore(state / "kanban.json")
    task = store.get(task_id)
    if task is None or task.contract.source_ref != f"git:{partial_head}":
        return {}
    evidence = task.contract.evidence_contract or {}
    if evidence.get("workflow_run_id") != workflow_run_id:
        return {}
    predecessors = evidence.get("supersedes_task_ids")
    if not isinstance(predecessors, list) or len(predecessors) != 1:
        return {}
    old = store.get(predecessors[0])
    if old is None:
        return {}
    identity = current_task_contract_identity(old)
    if identity.get("workflow_run_id") != workflow_run_id:
        return {}
    events = runtime.event_log.read_all()
    failures = {
        e.id
        for e in events
        if e.type == "dev.blocked"
        and e.task_id == old.id
        and e.payload.get("source_commit") == partial_head
    }
    proof = task_pipeline_failure_continuation(
        state_dir=state,
        project_root=root,
        events=events,
        task_id=old.id,
        workflow_run_id=workflow_run_id,
        identity=identity,
        failure_event_ids=failures,
    )
    if proof.get("continuation_commit") != partial_head:
        return {}
    failure = next(e for e in events if e.id == proof["continuation_source_event_id"])
    base = failure.payload["base_commit"]
    if _git(root, "merge-base", "--is-ancestor", base, candidate_head) is None:
        return {}
    revision = f"{base}..{partial_head}"
    if _git(root, "rev-list", "--merges", revision) != "":
        return {}
    commits = _git(root, "rev-list", "--reverse", revision)
    if not commits or not task.contract.scope:
        return {}
    paths = set()
    for commit in commits.splitlines():
        changed = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
        if changed is None:
            return {}
        paths.update(changed.splitlines())
    if not paths or any(
        not _path_allowed_by_scope(path, task.contract.scope) for path in paths
    ):
        return {}
    return {
        "partial_commits": commits.splitlines(),
        "partial_paths": sorted(paths),
        "supersedes_task_ids": predecessors,
        "failure_event_id": failure.id,
    }


def partial_successor_workspace_base(
    runtime: Any,
    *,
    task_id: str,
    partial_head: str,
    candidate_head: str,
    workflow_run_id: str,
) -> str:
    """Resolve proven partial work or retain the ordinary divergence rejection."""
    if partial_successor_proof(
        runtime,
        task_id=task_id,
        partial_head=partial_head,
        candidate_head=candidate_head,
        workflow_run_id=workflow_run_id,
    ):
        return partial_head
    from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError

    raise TaskPipelineRuntimeError(
        "Task Pipeline workspace base diverged from the current Candidate: "
        f"dispatch_base={partial_head}, candidate_head={candidate_head}"
    )


def apply_partial_successor_commit(
    rebuilder: Any, *, task_id: str, worktree: Path, commit: str
) -> str:
    """Replay a bounded patch; scoped file checkout would overwrite concurrent edits."""
    task = rebuilder.task_store.get(task_id)
    paths = rebuilder._commit_files(commit)
    if task is None or any(
        not _path_allowed_by_scope(path, task.contract.scope) for path in paths
    ):
        raise ValueError("partial successor commit exceeds its admitted scope")
    return rebuilder._cherry_pick_commit(worktree, commit)
