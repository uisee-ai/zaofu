"""Task-scoped writer worktrees for the v4 Task Pipeline."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.safety import (
    PathGuard,
    assert_owned_workdir,
    write_workdir_owner_marker,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.workdirs import WorkdirManager
from zf.runtime.worktree_env import provision_worktree_env, run_project_setup


TASK_WORKSPACE_SCHEMA_VERSION = "task-workspace.v1"


class TaskWorkspaceError(RuntimeError):
    """A task workspace request is unsafe or no longer current."""


@dataclass(frozen=True)
class TaskWorkspacePlan:
    workflow_run_id: str
    task_id: str
    task_map_generation: str
    workspace_generation: int
    base_commit: str
    workdir: str
    project_path: str
    branch: str
    mode: str
    enabled: bool


@dataclass(frozen=True)
class TaskWorkspaceRemovalResult:
    status: str
    project_path: str
    reason: str = ""

    @property
    def removed(self) -> bool:
        return self.status == "removed"


class TaskWorkspaceManager:
    """Own generation-scoped worktrees independently of worker placement."""

    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig,
    ) -> None:
        self.state_dir = state_dir.resolve(strict=False)
        self.project_root = project_root.resolve(strict=False)
        self.config = config
        role_root = WorkdirManager(
            state_dir=state_dir,
            project_root=project_root,
            config=config,
        ).root
        self.root = role_root / "tasks"
        PathGuard.assert_under(self.root, self.state_dir)

    def plan(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        task_map_generation: str,
        workspace_generation: int,
        base_ref: str,
    ) -> TaskWorkspacePlan:
        run_id = _required(workflow_run_id, "workflow_run_id")
        canonical_task_id = _required(task_id, "task_id")
        map_generation = _required(task_map_generation, "task_map_generation")
        if workspace_generation < 1:
            raise TaskWorkspaceError("workspace_generation must be >= 1")
        base_commit = self._resolve_commit(base_ref)
        identity_digest = hashlib.sha256(
            f"{run_id}|{canonical_task_id}|{map_generation}".encode("utf-8")
        ).hexdigest()[:12]
        workdir = (
            self.root
            / _component(run_id)
            / f"{_component(canonical_task_id)}-{identity_digest}"
            / f"g{workspace_generation}"
        )
        branch = (
            f"{self.config.runtime.git.writer_branch_prefix}/task-pipeline/"
            f"{_component(canonical_task_id, limit=36)}-{identity_digest}/"
            f"g{workspace_generation}"
        )
        return TaskWorkspacePlan(
            workflow_run_id=run_id,
            task_id=canonical_task_id,
            task_map_generation=map_generation,
            workspace_generation=workspace_generation,
            base_commit=base_commit,
            workdir=str(workdir),
            project_path=str(workdir / "project"),
            branch=branch,
            mode=self.config.runtime.workdirs.mode,
            enabled=self.config.runtime.workdirs.enabled,
        )

    def prepare(
        self,
        *,
        role: RoleConfig,
        workflow_run_id: str,
        task_id: str,
        task_map_generation: str,
        workspace_generation: int,
        base_ref: str,
    ) -> TaskWorkspacePlan:
        plan = self.plan(
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            task_map_generation=task_map_generation,
            workspace_generation=workspace_generation,
            base_ref=base_ref,
        )
        if not plan.enabled:
            return plan
        workdir = Path(plan.workdir)
        project_path = Path(plan.project_path)
        if workdir.exists():
            self._assert_owned(workdir)
            plan = self._with_persisted_base(plan)
            self._assert_metadata_current(plan)
        else:
            write_workdir_owner_marker(
                workdir,
                project_name=self.config.project.name,
                instance_id=f"task:{plan.task_id}:g{plan.workspace_generation}",
                project_root=self.project_root,
                created_by="zf-task-workspace-manager",
            )
        if plan.mode == "dry-run":
            self._write_metadata(plan, git_worktree_created=False)
            return plan
        if plan.mode != "worktree":
            raise TaskWorkspaceError(f"unsupported workdir mode: {plan.mode}")

        self._require_git_repo()
        if project_path.exists():
            if not (project_path / ".git").exists():
                raise TaskWorkspaceError(
                    f"task workspace is not a git worktree: {project_path}"
                )
            branch = self._git(project_path, "branch", "--show-current").strip()
            if branch != plan.branch:
                raise TaskWorkspaceError(
                    f"task workspace branch {branch!r} != {plan.branch!r}"
                )
        else:
            self._prune_stale_worktrees()
            if self._branch_exists(plan.branch):
                branch_commit = self._git(
                    self.project_root, "rev-parse", f"refs/heads/{plan.branch}"
                ).strip()
                if branch_commit != plan.base_commit:
                    raise TaskWorkspaceError(
                        "existing task workspace branch has unproven base currentness"
                    )
                self._git(
                    self.project_root,
                    "worktree",
                    "add",
                    str(project_path),
                    plan.branch,
                )
            else:
                self._git(
                    self.project_root,
                    "worktree",
                    "add",
                    "-b",
                    plan.branch,
                    str(project_path),
                    plan.base_commit,
                )
        provision_worktree_env(
            project_path,
            self.project_root,
            self.config.runtime.workdirs.provision_paths,
            bootstrap_uv_dev=True,
        )
        setup = self.config.project.setup_script
        if setup:
            result = run_project_setup(project_path, setup, marker_dir=workdir)
            if not result.ok:
                raise TaskWorkspaceError(
                    f"task workspace setup failed for {plan.task_id} "
                    f"(exit {result.exit_code}): {result.detail}"
                )
        self._write_metadata(plan, git_worktree_created=True)
        return plan

    def cleanup(
        self,
        plan: TaskWorkspacePlan,
        *,
        task_terminal: bool,
        integrated_or_archived: bool,
        active_attempts: int,
        active_sessions: int,
    ) -> TaskWorkspaceRemovalResult:
        project_path = Path(plan.project_path)
        workdir = Path(plan.workdir)
        blockers: list[str] = []
        if not task_terminal:
            blockers.append("task_not_terminal")
        if not integrated_or_archived:
            blockers.append("task_not_integrated_or_archived")
        if active_attempts:
            blockers.append("active_attempts")
        if active_sessions:
            blockers.append("active_sessions")
        if blockers:
            return TaskWorkspaceRemovalResult(
                status="blocked",
                project_path=str(project_path),
                reason=",".join(blockers),
            )
        if not project_path.exists():
            return TaskWorkspaceRemovalResult(
                status="skipped",
                project_path=str(project_path),
                reason="project path does not exist",
            )
        self._assert_owned(workdir)
        self._assert_metadata_current(plan)
        status = self._git(project_path, "status", "--porcelain").strip()
        if status:
            return TaskWorkspaceRemovalResult(
                status="dirty",
                project_path=str(project_path),
                reason="task workspace has uncommitted changes",
            )
        from zf.runtime.workspace_retention import workspace_retention_reason

        retained = workspace_retention_reason(project_path,
            provision_paths=self.config.runtime.workdirs.provision_paths, bookkeeping_dir=workdir)
        if retained:
            return TaskWorkspaceRemovalResult(
                status="blocked", project_path=str(project_path), reason=retained,
            )
        try:
            self._git(
                self.project_root,
                "worktree",
                "remove",
                str(project_path),
            )
            shutil.rmtree(workdir, ignore_errors=True)
        except (OSError, RuntimeError) as exc:
            return TaskWorkspaceRemovalResult(
                status="failed",
                project_path=str(project_path),
                reason=str(exc),
            )
        return TaskWorkspaceRemovalResult(
            status="removed",
            project_path=str(project_path),
        )

    def _write_metadata(
        self,
        plan: TaskWorkspacePlan,
        *,
        git_worktree_created: bool,
    ) -> None:
        workdir = Path(plan.workdir)
        if workdir.exists():
            self._assert_owned(workdir)
        meta = {
            "schema_version": TASK_WORKSPACE_SCHEMA_VERSION,
            **asdict(plan),
            "git_worktree_created": git_worktree_created,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_text(
            workdir / "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        )

    def _assert_metadata_current(self, plan: TaskWorkspacePlan) -> None:
        data = self._read_metadata(plan)
        if data is None:
            return
        for field in (
            "workflow_run_id",
            "task_id",
            "task_map_generation",
            "workspace_generation",
            "base_commit",
            "branch",
        ):
            if data.get(field) != getattr(plan, field):
                raise TaskWorkspaceError(
                    f"task workspace currentness mismatch: {field}"
                )

    def _with_persisted_base(
        self,
        plan: TaskWorkspacePlan,
    ) -> TaskWorkspacePlan:
        data = self._read_metadata(plan)
        if data is None:
            return plan
        base_commit = str(data.get("base_commit") or "").strip()
        if not base_commit or self._resolve_commit(base_commit) != base_commit:
            raise TaskWorkspaceError(
                "task workspace currentness mismatch: base_commit"
            )
        return replace(plan, base_commit=base_commit)

    @staticmethod
    def _read_metadata(plan: TaskWorkspacePlan) -> dict[str, object] | None:
        path = Path(plan.workdir) / "meta.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaskWorkspaceError(f"invalid task workspace metadata: {path}") from exc
        if not isinstance(data, dict):
            raise TaskWorkspaceError(f"invalid task workspace metadata: {path}")
        return data

    def _assert_owned(self, workdir: Path) -> None:
        assert_owned_workdir(
            workdir,
            state_dir=self.state_dir,
            workdir_root=self.root,
        )

    def _resolve_commit(self, ref: str) -> str:
        target = _required(ref, "base_ref")
        try:
            return self._git(
                self.project_root, "rev-parse", "--verify", f"{target}^{{commit}}"
            ).strip()
        except RuntimeError as exc:
            raise TaskWorkspaceError(f"task workspace base ref not found: {target}") from exc

    def _require_git_repo(self) -> None:
        try:
            self._git(self.project_root, "rev-parse", "--show-toplevel")
        except RuntimeError as exc:
            raise TaskWorkspaceError(
                f"task workspace requires a git repository: {self.project_root}"
            ) from exc

    def _branch_exists(self, branch: str) -> bool:
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.project_root,
            check=False,
        )
        return result.returncode == 0

    def _prune_stale_worktrees(self) -> None:
        try:
            self._git(self.project_root, "worktree", "prune")
        except RuntimeError:
            pass

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout


def _required(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TaskWorkspaceError(f"{field} is required")
    return text


def _component(value: str, *, limit: int = 64) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in value
    ).strip("-.")
    cleaned = cleaned or "unnamed"
    if len(cleaned) <= limit:
        return cleaned
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[: limit - 11]}-{digest}"


__all__ = [
    "TASK_WORKSPACE_SCHEMA_VERSION",
    "TaskWorkspaceError",
    "TaskWorkspaceManager",
    "TaskWorkspacePlan",
    "TaskWorkspaceRemovalResult",
]
