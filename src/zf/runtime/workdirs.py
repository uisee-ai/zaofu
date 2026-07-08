"""Workdir planning and opt-in git worktree preparation."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.safety import (
    PathGuard,
    PathGuardError,
    assert_owned_workdir,
    write_workdir_owner_marker,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.worktree_env import provision_worktree_env, run_project_setup


@dataclass(frozen=True)
class WorkdirPlan:
    instance_id: str
    role_name: str
    role_kind: str
    backend: str
    workdir: str
    project_path: str
    branch_or_ref: str
    source_ref: str
    mode: str
    enabled: bool


@dataclass(frozen=True)
class WorkdirRemovalResult:
    status: str
    workdir: str
    project_path: str
    reason: str

    @property
    def removed(self) -> bool:
        return self.status == "removed"


class WorkdirManager:
    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig,
    ) -> None:
        self.state_dir = state_dir
        self.project_root = project_root
        self.config = config
        self.root = self._resolve_root(Path(config.runtime.workdirs.root))
        PathGuard.assert_under(self.root, state_dir)

    def _resolve_root(self, root: Path) -> Path:
        if root.is_absolute():
            return root
        if root.parts and root.parts[0] == ".zf":
            return self.state_dir.joinpath(*root.parts[1:])
        return self.project_root / root

    def _run_declared_setup(self, role: RoleConfig, project_path: Path) -> None:
        """执行 project.scripts.setup(若声明)。失败 fail-closed:
        worktree 不可运行时派活只会烧 token,宁可铸造失败走既有补救。"""
        setup = self.config.project.setup_script
        if not setup:
            return
        result = run_project_setup(project_path, setup)
        if not result.ok:
            raise RuntimeError(
                f"workdir setup failed for {role.instance_id}"
                f" (exit {result.exit_code}): {result.detail}"
            )

    def plan(self, role: RoleConfig) -> WorkdirPlan:
        workdir = self.root / role.instance_id
        role_kind = _resolve_role_kind(role)
        source_ref = self._source_ref() if self.config.runtime.workdirs.mode == "worktree" else ""
        return WorkdirPlan(
            instance_id=role.instance_id,
            role_name=role.name,
            role_kind=role_kind,
            backend=role.backend,
            workdir=str(workdir),
            project_path=str(workdir / "project"),
            branch_or_ref=self._branch_or_ref(role, role_kind),
            source_ref=source_ref,
            mode=self.config.runtime.workdirs.mode,
            enabled=self.config.runtime.workdirs.enabled,
        )

    def prepare(self, role: RoleConfig) -> WorkdirPlan:
        plan = self.plan(role)
        if not plan.enabled:
            return plan
        if plan.mode == "dry-run":
            self._write_metadata(role, plan, git_worktree_created=False)
            return plan
        if plan.mode == "worktree":
            # Defensive: drop any stale ``.git/worktrees/<name>`` registry
            # entry whose target path no longer exists. Without this, an
            # operator-initiated ``rm -rf .zf`` (or a non-graceful watcher
            # crash) leaves git's worktree registry stuck on prunable
            # entries, and the subsequent ``git worktree add`` fails with
            # "missing but already registered worktree".
            self._clear_stale_worktree_registration(plan)
            if plan.role_kind != "writer":
                if plan.role_kind == "reader":
                    self._prepare_reader_worktree(role, plan)
                else:
                    self._write_metadata(role, plan, git_worktree_created=False)
                return plan
            self._prepare_writer_worktree(role, plan)
            self._install_local_only_push_guard(plan)
            return plan
        raise NotImplementedError(f"unsupported workdir mode: {plan.mode}")

    def _install_local_only_push_guard(self, plan: WorkdirPlan) -> None:
        """K5(2026-06-11,审计 Q2 高危 prose 补门):remote_policy=local_only
        时给**受管 writer worktree** 装 pre-push 拒绝钩。

        误伤面防护(评估时点名):worktree 默认共享主仓 .git/hooks 与
        config——直接写会拦到操作者本人。故走 `extensions.worktreeConfig`
        + `git config --worktree core.hooksPath`,作用域 = 仅本 worktree;
        主 checkout 与其他 worktree 不受影响。best-effort:失败仅降级回
        prose 提示(briefing 仍有 remote_policy 行),不阻断 spawn。
        """
        try:
            policy = str(
                getattr(self.config.runtime.git, "remote_policy", "") or ""
            )
            if policy != "local_only":
                return
            project_path = Path(plan.project_path)
            if not project_path.exists():
                return
            hooks_dir = project_path / ".zf-hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook = hooks_dir / "pre-push"
            hook.write_text(
                "#!/bin/sh\n"
                "echo 'zf: git push blocked — runtime.git.remote_policy="
                "local_only (本 worktree 受管;如需发布请走 harness 流程"
                "或调整 zf.yaml)' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)
            # 仅本 worktree 生效的 config(主仓只开 extensions 开关,
            # 该开关本身不改变任何行为,worktree 级 config 才生效)。
            self._git(
                self.project_root, "config", "extensions.worktreeConfig", "true",
            )
            self._git(
                project_path, "config", "--worktree",
                "core.hooksPath", str(hooks_dir),
            )
        except Exception:
            return

    def _clear_stale_worktree_registration(self, plan: WorkdirPlan) -> None:
        """Best-effort: prune git worktree registry entries that point at
        non-existent paths, focusing on the path we are about to (re)create.

        Idempotent. Never raises — if pruning fails the caller's
        ``git worktree add`` will surface the real error.
        """
        try:
            self._require_git_repo()
        except RuntimeError:
            return
        project_path = Path(plan.project_path)
        if project_path.exists():
            return
        try:
            listing = self._git(
                self.project_root, "worktree", "list", "--porcelain",
            )
        except RuntimeError:
            return
        target = str(project_path.resolve())
        prunable_target = False
        current_path: str | None = None
        prunable_current = False
        for raw in listing.splitlines():
            if raw.startswith("worktree "):
                if current_path is not None and prunable_current:
                    try:
                        current_resolved = str(Path(current_path).resolve())
                    except (OSError, RuntimeError):
                        current_resolved = current_path
                    if current_resolved == target:
                        prunable_target = True
                current_path = raw[len("worktree "):].strip()
                prunable_current = False
            elif raw == "prunable" or raw.startswith("prunable "):
                prunable_current = True
        if current_path is not None and prunable_current:
            try:
                current_resolved = str(Path(current_path).resolve())
            except (OSError, RuntimeError):
                current_resolved = current_path
            if current_resolved == target:
                prunable_target = True
        if not prunable_target:
            return
        try:
            self._git(self.project_root, "worktree", "prune")
        except RuntimeError:
            return

    def _write_metadata(
        self,
        role: RoleConfig,
        plan: WorkdirPlan,
        *,
        git_worktree_created: bool,
    ) -> None:
        workdir = Path(plan.workdir)
        if workdir.exists():
            assert_owned_workdir(workdir, state_dir=self.state_dir)
        write_workdir_owner_marker(
            workdir,
            project_name=self.config.project.name,
            instance_id=role.instance_id,
            project_root=self.project_root,
            created_by="zf-workdir-manager",
        )
        meta = {
            **asdict(plan),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git_worktree_created": git_worktree_created,
        }
        atomic_write_text(
            workdir / "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        )

    def _prepare_writer_worktree(self, role: RoleConfig, plan: WorkdirPlan) -> None:
        self._require_git_repo()
        workdir = Path(plan.workdir)
        project_path = Path(plan.project_path)
        branch = plan.branch_or_ref

        if workdir.exists():
            assert_owned_workdir(workdir, state_dir=self.state_dir)
        else:
            write_workdir_owner_marker(
                workdir,
                project_name=self.config.project.name,
                instance_id=role.instance_id,
                project_root=self.project_root,
                created_by="zf-workdir-manager",
            )

        if project_path.exists():
            if not (project_path / ".git").exists():
                raise RuntimeError(
                    f"workdir project path exists but is not a git worktree: {project_path}"
                )
            current = self._git(project_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
            if current != branch:
                status = self._git(project_path, "status", "--porcelain").strip()
                if status:
                    self._stash_dirty_worktree(
                        project_path,
                        role.instance_id,
                        reason=f"branch-mismatch:{current}->{branch}",
                    )
                self._ensure_branch(branch)
                self._git(project_path, "checkout", branch)
        else:
            self._ensure_branch(branch)
            self._git(
                self.project_root,
                "worktree",
                "add",
                str(project_path),
                branch,
            )

        provision_worktree_env(
            project_path,
            self.project_root,
            self.config.runtime.workdirs.provision_paths,
            bootstrap_uv_dev=True,
        )
        self._run_declared_setup(role, project_path)
        self._write_metadata(role, plan, git_worktree_created=True)

    def _prepare_reader_worktree(self, role: RoleConfig, plan: WorkdirPlan) -> None:
        self._require_git_repo()
        workdir = Path(plan.workdir)
        project_path = Path(plan.project_path)
        if workdir.exists():
            assert_owned_workdir(workdir, state_dir=self.state_dir)
        else:
            write_workdir_owner_marker(
                workdir,
                project_name=self.config.project.name,
                instance_id=role.instance_id,
                project_root=self.project_root,
                created_by="zf-workdir-manager",
            )
        if project_path.exists():
            if not (project_path / ".git").exists():
                raise RuntimeError(
                    f"workdir project path exists but is not a git worktree: {project_path}"
                )
        else:
            self._git(
                self.project_root,
                "worktree",
                "add",
                "--detach",
                str(project_path),
                "HEAD",
            )
        provision_worktree_env(
            project_path,
            self.project_root,
            self.config.runtime.workdirs.provision_paths,
            bootstrap_uv_dev=True,
        )
        self._run_declared_setup(role, project_path)
        self._write_metadata(role, plan, git_worktree_created=True)

    def checkout_reader_task_ref(self, role: RoleConfig, task_id: str) -> str | None:
        plan = self.plan(role)
        if not plan.enabled or plan.mode != "worktree" or plan.role_kind != "reader":
            return None
        target_ref = f"{self.config.runtime.git.task_ref_prefix}/{task_id}"
        source_commit = self.task_ref_metadata(task_id).get("source_commit", "")
        return self.checkout_reader_ref(role, target_ref, source_commit=source_commit)

    def checkout_reader_ref(
        self,
        role: RoleConfig,
        target_ref: str,
        *,
        source_commit: str = "",
    ) -> str | None:
        plan = self.plan(role)
        if not plan.enabled or plan.mode != "worktree" or plan.role_kind != "reader":
            return None
        self.prepare(role)
        project_path = Path(plan.project_path)
        self._git(project_path, "reset", "--hard", "HEAD")
        self._git(project_path, "clean", "-fd")
        checkout_ref = self._resolve_reader_checkout_ref(target_ref)
        self._git(project_path, "checkout", "--detach", checkout_ref)
        self._write_task_metadata(
            role,
            plan,
            target_ref=target_ref,
            source_commit=source_commit,
        )
        return target_ref

    def pin_reader_target(self, role: RoleConfig, target_ref: str) -> str:
        """FIX-9/15①(bizsim r4 F9):按 commit 锁定 reader 审计对象。

        返回锁定的 commit sha;非 worktree reader 返回 ""(无 workdir 可审计,
        由调用方决定是否放行)。解析/checkout/HEAD 校验任一步失败抛
        RuntimeError——fail-closed 由派发方兑现,禁止静默降级到旧 HEAD。
        """
        plan = self.plan(role)
        if not plan.enabled or plan.mode != "worktree" or plan.role_kind != "reader":
            return ""
        checkout_ref = self._resolve_reader_checkout_ref(target_ref)
        pinned = self._git(
            self.project_root, "rev-parse", f"{checkout_ref}^{{commit}}",
        ).strip()
        self.prepare(role)
        project_path = Path(plan.project_path)
        self._git(project_path, "reset", "--hard", "HEAD")
        self._git(project_path, "clean", "-fd")
        self._git(project_path, "checkout", "--detach", pinned)
        head = self._git(project_path, "rev-parse", "HEAD").strip()
        if head != pinned:
            raise RuntimeError(
                f"reader workdir HEAD {head[:12]} != pinned target {pinned[:12]}",
            )
        self._write_task_metadata(
            role,
            plan,
            target_ref=target_ref,
            source_commit=pinned,
        )
        return pinned

    def _resolve_reader_checkout_ref(self, target_ref: str) -> str:
        ref = str(target_ref or "HEAD").strip() or "HEAD"
        candidates: list[str]
        if ref == "HEAD" or ref.startswith("refs/"):
            candidates = [ref]
        else:
            candidates = [f"refs/heads/{ref}", ref]
        last_error = ""
        attempted: list[str] = []
        for candidate in candidates:
            attempted.append(candidate)
            try:
                self._git(self.project_root, "rev-parse", "--verify", candidate)
                return candidate
            except RuntimeError as exc:
                last_error = str(exc)
        tried = ", ".join(attempted)
        detail = f"; last error: {last_error}" if last_error else ""
        raise RuntimeError(f"git ref not found: {ref}; tried: {tried}{detail}")

    def task_ref_metadata(self, task_id: str) -> dict[str, str]:
        index_path = self.state_dir / "refs" / "task-index.json"
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        entry = data.get(task_id)
        if not isinstance(entry, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in entry.items()
            if value is not None
        }

    def reset_reader_if_dirty(self, role: RoleConfig) -> str:
        plan = self.plan(role)
        if not plan.enabled or plan.mode != "worktree" or plan.role_kind != "reader":
            return ""
        project_path = Path(plan.project_path)
        if not (project_path / ".git").exists():
            return ""
        status = self._git(project_path, "status", "--porcelain")
        status = self._reader_reportable_status(project_path, status)
        if status.strip():
            self._git(project_path, "reset", "--hard", "HEAD")
            self._git(project_path, "clean", "-fd")
        return status

    def _reader_reportable_status(self, project_path: Path, status: str) -> str:
        """Filter runtime-only files from reader dirty checks.

        Codex may write ``.codex/hooks.json`` into a reader worktree while
        approving hooks. That file is runtime projection, not product output,
        so it must not trigger reader reset/clean or dirty diagnostics.
        """
        lines = [
            line
            for line in status.splitlines()
            if not self._is_runtime_codex_hook_status(project_path, line)
            and not self._is_compiled_python_artifact(line)
            and not self._is_runtime_tooling_artifact(line)
        ]
        if not lines:
            return ""
        suffix = "\n" if status.endswith("\n") else ""
        return "\n".join(lines) + suffix

    def _is_runtime_codex_hook_status(self, project_path: Path, line: str) -> bool:
        if len(line) < 4:
            return False
        path = line[3:].strip()
        if not path:
            return False
        if path == ".codex/hooks.json":
            return True
        if " -> " in path:
            parts = [part.strip() for part in path.split(" -> ")]
            return bool(parts) and all(part == ".codex/hooks.json" for part in parts)
        if path != ".codex/":
            return False
        codex_dir = project_path / ".codex"
        if not codex_dir.exists():
            return False
        files = {
            candidate.relative_to(codex_dir).as_posix()
            for candidate in codex_dir.rglob("*")
            if candidate.is_file()
        }
        return bool(files) and files <= {"hooks.json"}

    def _is_compiled_python_artifact(self, line: str) -> bool:
        """Filter compiled-python build artifacts from reader dirty checks.

        Verification gates such as ``python3 -m py_compile`` / ``-m unittest``
        write ``__pycache__/`` + ``*.pyc`` into a reader worktree. Those are
        build artifacts, not product output, so they must not trigger a
        ``reader.write_violation`` / reset (the calc-fullflow read-only-gate
        false positive).
        """
        if len(line) < 4:
            return False
        path = line[3:].strip()
        if not path:
            return False
        parts = (
            [p.strip() for p in path.split(" -> ")] if " -> " in path else [path]
        )

        def _is_pyc(p: str) -> bool:
            return (
                p.endswith((".pyc", ".pyo"))
                or p == "__pycache__/"
                or p.endswith("/__pycache__")
                or p.endswith("/__pycache__/")
                or "__pycache__/" in p
            )

        return bool(parts) and all(_is_pyc(p) for p in parts)

    def _is_runtime_tooling_artifact(self, line: str) -> bool:
        """Filter local tooling artifacts from read-only gate dirty checks."""
        if len(line) < 4:
            return False
        path = line[3:].strip()
        if not path:
            return False
        parts = (
            [p.strip() for p in path.split(" -> ")] if " -> " in path else [path]
        )

        def _is_tooling_path(p: str) -> bool:
            normalized = p.strip().strip("/")
            return (
                normalized == ".venv"
                or normalized.startswith(".venv/")
                or normalized == ".pytest_cache"
                or normalized.startswith(".pytest_cache/")
                or normalized == ".coverage"
                or normalized.startswith(".coverage.")
            )

        return bool(parts) and all(_is_tooling_path(p) for p in parts)

    @staticmethod
    def classify_reader_status(status: str) -> dict[str, object]:
        """Classify a filtered reader dirty status for diagnostics."""
        lines = [line for line in status.splitlines() if line.strip()]
        has_untracked = any(line.startswith("??") for line in lines)
        has_tracked = any(not line.startswith("??") for line in lines)
        if has_tracked and has_untracked:
            classification = "mixed_source_mutation"
        elif has_tracked:
            classification = "tracked_source_mutation"
        elif has_untracked:
            classification = "untracked_source_mutation"
        else:
            classification = "clean"
        return {
            "classification": classification,
            "line_count": len(lines),
            "has_tracked": has_tracked,
            "has_untracked": has_untracked,
        }

    def sync_writer_to_source_ref(
        self,
        role: RoleConfig,
        *,
        source_ref_override: str = "",
    ) -> dict[str, str]:
        """Reset an idle, clean writer worktree to the current project HEAD.

        Writer worktrees use stable branches such as ``worker/dev-1``. After a
        task is accepted and projected into ``main``, the stable worker branch
        may still point at the old task commit. A new dispatch must start from
        the current source ref, not from a stale stacked branch.
        """
        plan = self.plan(role)
        if not plan.enabled or plan.mode != "worktree" or plan.role_kind != "writer":
            return {}
        self.prepare(role)
        source_ref = source_ref_override or plan.source_ref
        if not source_ref:
            return {}
        project_path = Path(plan.project_path)
        if not (project_path / ".git").exists():
            return {}
        before = self._git(project_path, "rev-parse", "HEAD").strip()
        branch = self._git(project_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
        status = self._git(project_path, "status", "--porcelain").strip()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        # If the worker pane was killed mid-task (orphan timeout, crash,
        # operator kill), uncommitted product still sits in the worktree.
        # Don't lose it — stash to a backup ref so operators can recover.
        # Original behavior raised here, causing dispatch retry death loop.
        stashed_ref = ""
        if status:
            stashed_ref = (
                f"refs/zf/workdir-stash/{role.instance_id}/{stamp}"
            )
            # Make a one-shot commit on a detached temp branch that captures
            # both staged and untracked files, then point a permanent ref at
            # it and reset HEAD back so the regular sync logic below can
            # proceed cleanly.
            try:
                self._git(project_path, "add", "-A")
                # author/committer may not be configured per-worktree;
                # rely on git env fallbacks. --allow-empty avoids failing
                # when add -A produced nothing (race against status read).
                self._git(
                    project_path,
                    "-c", "user.email=zf-orphan-stash@zaofu.local",
                    "-c", "user.name=zf-orphan-stash",
                    "commit", "-m",
                    f"zf-orphan-stash: {role.instance_id} {stamp}",
                    "--allow-empty",
                )
                stash_commit = self._git(
                    project_path, "rev-parse", "HEAD",
                ).strip()
                self._git(project_path, "update-ref", stashed_ref, stash_commit)
                # Undo the stash commit but keep the ref pointing at it.
                self._git(project_path, "reset", "--hard", before)
            except RuntimeError:
                # Best-effort: if commit/ref creation failed we still
                # need to clear the dirty state. Reset hard discards
                # the uncommitted changes (last resort).
                self._git(project_path, "reset", "--hard", before)
                self._git(project_path, "clean", "-fd")
                stashed_ref = ""
        if before == source_ref and not stashed_ref:
            return {
                "project_path": str(project_path),
                "branch": branch,
                "before": before,
                "after": before,
                "source_ref": source_ref,
                "synced": "false",
                "backup_ref": "",
                "stashed_ref": "",
            }
        backup_ref = f"refs/zf/workdir-backups/{role.instance_id}/{stamp}"
        self._git(project_path, "update-ref", backup_ref, before)
        self._git(project_path, "reset", "--hard", source_ref)
        self._git(project_path, "clean", "-fd")
        after = self._git(project_path, "rev-parse", "HEAD").strip()
        return {
            "project_path": str(project_path),
            "branch": branch,
            "before": before,
            "after": after,
            "source_ref": source_ref,
            "synced": "true",
            "backup_ref": backup_ref,
            "stashed_ref": stashed_ref,
        }

    def _stash_dirty_worktree(
        self,
        project_path: Path,
        instance_id: str,
        *,
        reason: str,
    ) -> str:
        """Snapshot uncommitted writer changes to a permanent ref, then clean.

        Startup/restart recovery must not let a stale dirty worktree crash the
        whole harness. The content is preserved under refs/zf/workdir-stash so
        operators can inspect or restore it, then the managed worktree becomes
        safe to checkout/reset for the current run.
        """
        status = self._git(project_path, "status", "--porcelain").strip()
        if not status:
            return ""
        before = self._git(project_path, "rev-parse", "HEAD").strip()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        stashed_ref = f"refs/zf/workdir-stash/{instance_id}/{stamp}"
        try:
            self._git(project_path, "add", "-A")
            self._git(
                project_path,
                "-c", "user.email=zf-orphan-stash@zaofu.local",
                "-c", "user.name=zf-orphan-stash",
                "commit", "-m",
                f"zf-orphan-stash: {instance_id} {reason} {stamp}",
                "--allow-empty",
            )
            stash_commit = self._git(project_path, "rev-parse", "HEAD").strip()
            self._git(project_path, "update-ref", stashed_ref, stash_commit)
            self._git(project_path, "reset", "--hard", before)
            self._git(project_path, "clean", "-fd")
            return stashed_ref
        except RuntimeError:
            self._git(project_path, "reset", "--hard", before)
            self._git(project_path, "clean", "-fd")
            return ""

    def apply_dependency_task_refs(
        self,
        role: RoleConfig,
        dependency_task_ids: list[str],
    ) -> dict[str, object]:
        """Apply completed dependency task refs to a writer worktree.

        ``blocked_by`` is a scheduling dependency, but writer worktrees also
        need the completed upstream code before the downstream worker starts.
        This method is called by the deterministic dispatcher after the writer
        branch is reset to its dispatch base and before the briefing is sent.
        """
        plan = self.plan(role)
        if not plan.enabled or plan.mode != "worktree" or plan.role_kind != "writer":
            return {}
        deps = _dedupe_strings(dependency_task_ids)
        if not deps:
            return {}
        self.prepare(role)
        project_path = Path(plan.project_path)
        if not (project_path / ".git").exists():
            return {}
        before = self._git(project_path, "rev-parse", "HEAD").strip()
        branch = self._git(project_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
        status = self._git(project_path, "status", "--porcelain").strip()
        if status:
            raise RuntimeError(
                f"workdir {project_path} is dirty before dependency apply"
            )

        applied: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        for task_id in deps:
            entry = self.task_ref_metadata(task_id)
            target_ref = str(entry.get("task_ref") or "").strip()
            if not target_ref:
                target_ref = f"{self.config.runtime.git.task_ref_prefix}/{task_id}"
            full_ref = f"refs/heads/{target_ref}"
            source_commit = str(entry.get("source_commit") or "").strip()
            try:
                commit = self._git(
                    self.project_root,
                    "rev-parse",
                    "--verify",
                    f"{source_commit or full_ref}^{{commit}}",
                ).strip()
            except RuntimeError as exc:
                raise RuntimeError(
                    f"{task_id}: missing dependency task ref {target_ref}"
                ) from exc
            if self._is_ancestor(project_path, commit, "HEAD"):
                skipped.append({
                    "task_id": task_id,
                    "task_ref": target_ref,
                    "source_commit": commit,
                    "reason": "already_ancestor",
                })
                continue
            try:
                self._git(project_path, "cherry-pick", commit)
            except RuntimeError as exc:
                detail = str(exc)
                if self._cherry_pick_empty(detail):
                    try:
                        self._git(project_path, "cherry-pick", "--skip")
                    except RuntimeError:
                        self._abort_cherry_pick(project_path)
                        raise RuntimeError(
                            f"{task_id}: empty dependency cherry-pick could not skip"
                        ) from exc
                    skipped.append({
                        "task_id": task_id,
                        "task_ref": target_ref,
                        "source_commit": commit,
                        "reason": "empty_cherry_pick",
                    })
                    continue
                self._abort_cherry_pick(project_path)
                raise RuntimeError(
                    f"{task_id}: dependency task ref {target_ref} could not be applied: {detail}"
                ) from exc
            applied.append({
                "task_id": task_id,
                "task_ref": target_ref,
                "source_commit": commit,
            })

        after = self._git(project_path, "rev-parse", "HEAD").strip()
        self._record_dependency_metadata(
            role,
            plan,
            before=before,
            after=after,
            applied=applied,
            skipped=skipped,
        )
        return {
            "project_path": str(project_path),
            "branch": branch,
            "before": before,
            "after": after,
            "applied_dependency_refs": applied,
            "skipped_dependency_refs": skipped,
        }

    def _is_ancestor(self, cwd: Path, ancestor: str, descendant: str) -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    @staticmethod
    def _cherry_pick_empty(detail: str) -> bool:
        text = detail.lower()
        return (
            "previous cherry-pick is now empty" in text
            or "nothing to commit" in text
            or "the previous cherry-pick is empty" in text
        )

    def _abort_cherry_pick(self, cwd: Path) -> None:
        try:
            self._git(cwd, "cherry-pick", "--abort")
        except RuntimeError:
            pass

    def _record_dependency_metadata(
        self,
        role: RoleConfig,
        plan: WorkdirPlan,
        *,
        before: str,
        after: str,
        applied: list[dict[str, str]],
        skipped: list[dict[str, str]],
    ) -> None:
        workdir = Path(plan.workdir)
        meta_path = workdir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                meta = {}
        except (FileNotFoundError, json.JSONDecodeError):
            meta = {**asdict(plan)}
        meta.update({
            "dependency_base_before": before,
            "dependency_after": after,
            "dependency_refs": applied,
            "dependency_refs_skipped": skipped,
            "dependency_refs_updated_at": datetime.now(timezone.utc).isoformat(),
            "instance_id": role.instance_id,
        })
        atomic_write_text(
            meta_path,
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        )

    def _write_task_metadata(
        self,
        role: RoleConfig,
        plan: WorkdirPlan,
        *,
        target_ref: str,
        source_commit: str = "",
    ) -> None:
        workdir = Path(plan.workdir)
        meta = {
            **asdict(plan),
            "branch_or_ref": target_ref,
            "target_ref": target_ref,
            "source_commit": source_commit,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git_worktree_created": True,
        }
        write_workdir_owner_marker(
            workdir,
            project_name=self.config.project.name,
            instance_id=role.instance_id,
            project_root=self.project_root,
            created_by="zf-workdir-manager",
        )
        atomic_write_text(
            workdir / "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        )

    def _require_git_repo(self) -> None:
        try:
            self._git(self.project_root, "rev-parse", "--show-toplevel")
            self._git(self.project_root, "rev-parse", "--verify", "HEAD")
        except RuntimeError as exc:
            raise RuntimeError(
                f"worktree mode requires {self.project_root} to be a git repo with HEAD"
            ) from exc

    def _ensure_branch(self, branch: str) -> None:
        try:
            self._git(self.project_root, "rev-parse", "--verify", f"refs/heads/{branch}")
        except RuntimeError:
            self._git(self.project_root, "branch", branch, "HEAD")

    def _source_ref(self) -> str:
        try:
            return self._git(self.project_root, "rev-parse", "HEAD").strip()
        except RuntimeError:
            return ""

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

    def doctor(self) -> list[str]:
        issues: list[str] = []
        if not self.root.exists():
            if self.config.runtime.workdirs.enabled:
                for role in self.config.roles:
                    issues.append(f"{role.instance_id}: missing workdir")
            return issues
        for role in self.config.roles:
            plan = self.plan(role)
            workdir = self.root / role.instance_id
            if not workdir.exists():
                if self.config.runtime.workdirs.enabled:
                    issues.append(f"{role.instance_id}: missing workdir")
                continue
            try:
                assert_owned_workdir(workdir, state_dir=self.state_dir)
            except PathGuardError as exc:
                issues.append(f"{role.instance_id}: {exc}")
                continue
            meta_path = workdir / "meta.json"
            if not meta_path.is_file():
                issues.append(f"{role.instance_id}: missing meta.json")
            else:
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    issues.append(f"{role.instance_id}: invalid meta.json: {exc}")
                else:
                    if meta.get("instance_id") != role.instance_id:
                        issues.append(f"{role.instance_id}: meta instance_id mismatch")
                    if meta.get("branch_or_ref") and meta.get("branch_or_ref") != plan.branch_or_ref:
                        if plan.role_kind != "reader" or "<task_id>" not in plan.branch_or_ref:
                            issues.append(f"{role.instance_id}: meta branch_or_ref mismatch")
            project_path = Path(plan.project_path)
            if project_path.exists() and (project_path / ".git").exists():
                try:
                    branch = self._git(project_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
                    status = self._git(project_path, "status", "--porcelain").strip()
                except RuntimeError as exc:
                    issues.append(f"{role.instance_id}: git status failed: {exc}")
                    continue
                if plan.role_kind == "writer" and branch != plan.branch_or_ref:
                    issues.append(
                        f"{role.instance_id}: branch {branch!r}, expected {plan.branch_or_ref!r}"
                    )
                if plan.role_kind == "reader" and branch != "HEAD":
                    issues.append(f"{role.instance_id}: reader is not detached")
                if plan.role_kind == "reader":
                    status = self._reader_reportable_status(project_path, status)
                if status:
                    issues.append(f"{role.instance_id}: dirty worktree")
        return issues

    def repair(self, instance_id: str) -> list[str]:
        role = next(
            (candidate for candidate in self.config.roles if candidate.instance_id == instance_id),
            None,
        )
        if role is None:
            raise RuntimeError(f"unknown role instance: {instance_id}")
        workdir = self.root / role.instance_id
        if workdir.exists():
            assert_owned_workdir(workdir, state_dir=self.state_dir)
        before_meta = (workdir / "meta.json").exists()
        before_project = (workdir / "project").exists()
        plan = self.prepare(role)
        actions: list[str] = []
        if not before_meta and (workdir / "meta.json").exists():
            actions.append(f"restored meta.json for {role.instance_id}")
        if not before_project and Path(plan.project_path).exists():
            actions.append(f"restored project worktree for {role.instance_id}")
        if not actions:
            actions.append(f"{role.instance_id} already healthy")
        return actions

    def remove(self, role: RoleConfig) -> "WorkdirRemovalResult":
        """Tear down a previously prepared worktree for a retired worker.

        Returns a structured result describing what happened so callers can
        emit a ``workdir.retired`` event and surface failures without
        breaking the retire main path. ``status`` is one of:

        * ``"removed"``    — worktree existed and was removed cleanly
        * ``"skipped"``    — workdirs disabled, not a worktree, or path missing
        * ``"dirty"``      — refused to remove because the worktree had
                              uncommitted changes
        * ``"failed"``     — git/filesystem error during removal
        """
        plan = self.plan(role)
        workdir = Path(plan.workdir)
        project_path = Path(plan.project_path)
        if not plan.enabled or plan.mode != "worktree":
            return WorkdirRemovalResult(
                status="skipped",
                workdir=str(workdir),
                project_path=str(project_path),
                reason="workdir disabled or non-worktree mode",
            )
        if not project_path.exists():
            return WorkdirRemovalResult(
                status="skipped",
                workdir=str(workdir),
                project_path=str(project_path),
                reason="project path does not exist",
            )

        # Refuse to remove a worktree with uncommitted changes — caller
        # must ensure clean state before retire.
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_path,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            return WorkdirRemovalResult(
                status="failed",
                workdir=str(workdir),
                project_path=str(project_path),
                reason=f"git status failed: {exc}",
            )
        if status.returncode != 0:
            return WorkdirRemovalResult(
                status="failed",
                workdir=str(workdir),
                project_path=str(project_path),
                reason=(status.stderr or status.stdout or "git status nonzero").strip(),
            )
        if status.stdout.strip():
            return WorkdirRemovalResult(
                status="dirty",
                workdir=str(workdir),
                project_path=str(project_path),
                reason="workdir has uncommitted changes",
            )

        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(project_path)],
                cwd=self.project_root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            return WorkdirRemovalResult(
                status="failed",
                workdir=str(workdir),
                project_path=str(project_path),
                reason=(exc.stderr or exc.stdout or str(exc)).strip(),
            )
        except OSError as exc:
            return WorkdirRemovalResult(
                status="failed",
                workdir=str(workdir),
                project_path=str(project_path),
                reason=str(exc),
            )

        # Drop the workdir bookkeeping directory if it still exists
        # (project/ has been removed by git worktree; meta.json may remain).
        if workdir.exists():
            try:
                import shutil as _shutil

                _shutil.rmtree(workdir, ignore_errors=True)
            except OSError:
                pass

        return WorkdirRemovalResult(
            status="removed",
            workdir=str(workdir),
            project_path=str(project_path),
            reason="",
        )

    def _branch_or_ref(self, role: RoleConfig, role_kind: str) -> str:
        git = self.config.runtime.git
        if role_kind == "writer":
            return f"{git.writer_branch_prefix}/{role.instance_id}"
        if role_kind == "reader":
            return f"{git.task_ref_prefix}/<task_id>"
        return "auto"


def _resolve_role_kind(role: RoleConfig) -> str:
    if role.role_kind != "auto":
        return role.role_kind
    if role.name in {"review", "test", "judge", "verify"}:
        return "reader"
    return "writer"


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
