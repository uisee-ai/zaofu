"""Authorize a committed failed Impl as a successor's isolated starting point."""

import subprocess
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
    events = runtime.event_log.read_all()
    old_id = predecessors[0]
    visited = {task_id}
    proof: dict = {}
    # A later successor may have stopped before changing the inherited base.
    # Follow only explicit single-parent links that retain this exact commit.
    # The original admitted failure is still required; names are never lineage.
    for _ in range(16):
        if old_id in visited:
            return {}
        visited.add(old_id)
        old = store.get(old_id)
        if old is None:
            return {}
        identity = current_task_contract_identity(old)
        if identity.get("workflow_run_id") != workflow_run_id:
            return {}
        failures = {e.id for e in events if e.type == "dev.blocked"
                    and e.task_id == old.id and e.payload.get("source_commit") == partial_head}
        proof = task_pipeline_failure_continuation(
            state_dir=state, project_root=root, events=events, task_id=old.id,
            workflow_run_id=workflow_run_id, identity=identity,
            failure_event_ids=failures,
        )
        if proof.get("continuation_commit") == partial_head:
            break
        links = (old.contract.evidence_contract or {}).get("supersedes_task_ids")
        if old.contract.source_ref != f"git:{partial_head}" or not isinstance(links, list) or len(links) != 1:
            return {}
        old_id = links[0]
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
        return _compose_partial_baseline(
            Path(runtime.project_root), task_id=task_id,
            partial_head=partial_head, candidate_head=candidate_head,
        )
    from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError

    raise TaskPipelineRuntimeError(
        "Task Pipeline workspace base diverged from the current Candidate: "
        f"dispatch_base={partial_head}, candidate_head={candidate_head}"
    )


def _compose_partial_baseline(
    root: Path, *, task_id: str, partial_head: str, candidate_head: str,
) -> str:
    """Build an unpublished merge object; never move a ref or edit a worktree.

    Both immutable parents remain reachable. Candidate integration can still
    identify the original bounded partial patches, while Impl sees dependencies
    that completed after the failed attempt's original base was frozen.
    """
    from zf.runtime.git_capture import git_env
    from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError

    env = git_env()

    def run(*args: str, body: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args], input=body, capture_output=True,
                text=True, timeout=30, check=False, env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TaskPipelineRuntimeError("partial baseline Git preparation failed") from exc

    merged = run("merge-tree", "--write-tree", candidate_head, partial_head)
    if merged.returncode or not merged.stdout.strip():
        raise TaskPipelineRuntimeError(
            "partial successor baseline conflict or merge-tree failure: "
            f"candidate={candidate_head}, partial={partial_head}; "
            "preserve both sources and request bounded conflict resolution"
        )
    tree = merged.stdout.splitlines()[0].strip()
    dates = [run("show", "-s", "--format=%ct", parent)
             for parent in (candidate_head, partial_head)]
    if any(result.returncode for result in dates):
        raise TaskPipelineRuntimeError("partial baseline parent timestamp is unavailable")
    # Stable metadata makes repeated dispatch preparation resolve the same SHA.
    stamp = f"{max(int(result.stdout.strip()) for result in dates)} +0000"
    env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    commit = run(
        "commit-tree", tree, "-p", candidate_head, "-p", partial_head,
        body=f"chore: compose verified partial baseline for {task_id}\n\n"
             f"Candidate: {candidate_head}\nPartial: {partial_head}\n",
    )
    if commit.returncode or not commit.stdout.strip():
        raise TaskPipelineRuntimeError("partial baseline commit object could not be created")
    return commit.stdout.strip()


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
