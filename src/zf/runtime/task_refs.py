"""Task handoff refs for git-backed writer completion events."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.safety import PathGuard
from zf.core.state.locks import locked_path
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.store import TaskStore
from zf.runtime.artifact_manifest import (
    contract_refs_from_manifest,
    is_taskless_workflow_manifest_payload,
    load_manifest_from_payload,
    normalize_artifact_kind,
)
from zf.runtime.recovery_sufficiency import verify_artifact_ref


_IGNORABLE_HANDOFF_DIRTY_PATHS = frozenset({
    ".codex/hooks.json",
})

# Backend runtime dirs the harness materializes INTO each worker worktree
# (claude-code skills + local settings under .claude/, codex hooks/runtime
# under .codex/). They are never task deliverables but show up as untracked at
# handoff. The ledger e2e (2026-06-20) hit integration.failed because dev-api /
# dev-web committed their real work yet the untracked .claude/ skills dir
# tripped "uncommitted changes; commit or clean before handoff" — and the
# worker-declared ignorable path does not apply unless the worker itself
# reports worktree_dirty. These prefixes are always excluded from the handoff
# dirty-check, independent of that flag.
_RUNTIME_MATERIALIZED_DIRTY_PREFIXES = (".claude/", ".codex/")
_RUNTIME_MATERIALIZED_DIRTY_PATHS = frozenset({
    ".zf-setup.done",
})

_NON_FILE_EVIDENCE_REF_PREFIXES = (
    "git:",
    "branch:",
    "commit:",
    "briefing:",
    "event:",
    "task:",
    "trace:",
    "dispatch:",
)


@dataclass(frozen=True)
class TaskRefResult:
    status: str
    payload: dict[str, Any]


class TaskRefManager:
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
        self.refs_dir = state_dir / "refs"
        self.index_path = self.refs_dir / "task-index.json"

    def process_dev_build_done(self, event: ZfEvent) -> TaskRefResult | None:
        if event.type not in {"dev.build.done", "impl.child.completed"} or not event.task_id:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        relevant_keys = {
            "source_commit",
            "source_branch",
            "workdir",
            "files_touched",
            "verification",
        }
        if not any(key in payload for key in relevant_keys):
            if self._requires_git_handoff():
                snapshot = self._snapshot_dev_artifacts_if_available(
                    event,
                    payload,
                )
                if snapshot is not None:
                    return snapshot
                return self._reject(
                    event,
                    "missing git handoff payload in worktree mode",
                    payload,
                )
            return TaskRefResult(
                status="legacy",
                payload={
                    "task_id": event.task_id,
                    "trigger_event_id": event.id,
                    "reason": "legacy dev.build.done without git handoff payload",
                },
            )

        if not str(payload.get("source_commit") or "").strip():
            if self._requires_git_handoff():
                snapshot = self._snapshot_dev_artifacts_if_available(
                    event,
                    payload,
                )
                if snapshot is not None:
                    return snapshot

        source_commit = str(payload.get("source_commit") or "").strip()
        source_branch = str(payload.get("source_branch") or "").strip()
        workdir = str(payload.get("workdir") or "").strip()
        if not source_commit:
            return self._reject(event, "missing source_commit", payload)
        handoff_diagnostics: dict[str, Any] = {}
        allow_ignorable_workdir_dirty = False
        if self._requires_git_handoff() and payload.get("worktree_dirty") is True:
            dirty_payload = self._ignorable_handoff_dirty_payload(payload)
            if dirty_payload is None:
                return self._reject(
                    event,
                    "worktree_dirty handoff is not allowed in worktree mode",
                    payload,
                )
            handoff_diagnostics.update(dirty_payload)
            allow_ignorable_workdir_dirty = True

        branch_head_mismatch = False
        branch_head = ""

        def _branch_mismatch_message() -> str:
            return (
                f"source_commit {full_commit} is not HEAD of {expected_branch} "
                f"({branch_head})"
            )

        expected_branch = source_branch or self._expected_branch(event)
        if not expected_branch:
            return self._reject(event, "missing source_branch and actor", payload)

        try:
            full_commit = self._git("rev-parse", "--verify", f"{source_commit}^{{commit}}").strip()
            branch_head = self._git(
                "rev-parse",
                "--verify",
                f"refs/heads/{expected_branch}^{{commit}}",
            ).strip()
            if branch_head != full_commit:
                if not self._accepts_rewound_branch_handoff(
                    full_commit=full_commit,
                    branch_head=branch_head,
                    payload=payload,
                ):
                    raise RuntimeError(_branch_mismatch_message())
                branch_head_mismatch = True
                handoff_diagnostics["source_branch_head_mismatch"] = {
                    "source_branch": expected_branch,
                    "branch_head": branch_head,
                    "source_commit": full_commit,
                    "accepted_reason": (
                        "source_commit remains reachable from the rewound "
                        "branch base and will be scoped before handoff"
                    ),
                }
            else:
                self._git("merge-base", "--is-ancestor", full_commit, expected_branch)
            if workdir:
                workdir_diagnostics = self._validate_workdir(
                    workdir,
                    full_commit,
                    allow_ignorable_dirty=allow_ignorable_workdir_dirty,
                    allow_head_mismatch=branch_head_mismatch,
                )
                handoff_diagnostics.update(workdir_diagnostics)
            changed_files = self._handoff_changed_files(
                event,
                payload,
                source_commit=full_commit,
            )
            reported_files = self._artifact_paths_from_payload(payload)
            reported_mismatch = _reported_files_mismatch(
                reported_files=reported_files,
                changed_files=changed_files,
            )
            if reported_mismatch:
                handoff_diagnostics["reported_files_mismatch"] = reported_mismatch
            scope_reject = self._reject_out_of_scope_task_ref(
                event,
                payload,
                source_commit=full_commit,
                changed_files=changed_files,
            )
            if scope_reject is not None:
                return scope_reject
            task_ref = f"{self.config.runtime.git.task_ref_prefix}/{event.task_id}"
            self._git("check-ref-format", f"refs/heads/{task_ref}")
            self._git("update-ref", f"refs/heads/{task_ref}", full_commit)
            entry = self._upsert_index(
                task_id=event.task_id,
                source_commit=full_commit,
                source_branch=expected_branch,
                task_ref=task_ref,
                actor=event.actor or "",
                trigger_event_id=event.id,
                trace_id=event.correlation_id or "",
                run_id=str(payload.get("run_id") or ""),
                workdir=workdir,
                pdd_id=str(payload.get("pdd_id") or ""),
                feature_id=str(payload.get("feature_id") or ""),
                diagnostics=handoff_diagnostics,
                changed_files=changed_files,
                reported_files=reported_files,
            )
        except Exception as exc:
            return self._reject(event, str(exc), payload)

        return TaskRefResult(status="updated", payload=entry)

    def _snapshot_dev_artifacts_if_available(
        self,
        event: ZfEvent,
        payload: dict[str, Any],
    ) -> TaskRefResult | None:
        """Snapshot a dev dirty worktree when it declares exact artifacts.

        Codex workers may finish with task-scope changes in the worktree
        instead of creating a git commit. In worktree mode the downstream
        reader roles still require a replayable task ref, so accept only the
        explicitly declared paths and commit those paths in the actor worktree.
        """
        paths = self._artifact_paths_from_payload(payload)
        if not paths:
            return None
        try:
            workdir = self._actor_workdir(event)
            visible_paths = [
                path for path in paths
                if (workdir / path).exists()
            ]
            path_reject = self._reject_out_of_scope_paths(
                event,
                payload,
                changed_files=visible_paths or paths,
            )
            if path_reject is not None:
                return path_reject
            if not visible_paths:
                return self._reject(
                    event,
                    "dev artifact refs not found in actor workdir",
                    payload,
                )
            commit = self._snapshot_artifacts(
                event=event,
                workdir=workdir,
                paths=visible_paths,
            )
            source_branch = (
                self._git_in(workdir, "branch", "--show-current").strip()
                or f"detached/{event.actor or 'unknown'}"
            )
            task_ref = f"{self.config.runtime.git.task_ref_prefix}/{event.task_id}"
            self._git("check-ref-format", f"refs/heads/{task_ref}")
            self._git("update-ref", f"refs/heads/{task_ref}", commit)
            entry = self._upsert_index(
                task_id=event.task_id or "",
                source_commit=commit,
                source_branch=source_branch,
                task_ref=task_ref,
                actor=event.actor or "",
                trigger_event_id=event.id,
                trace_id=event.correlation_id or "",
                run_id=str(payload.get("run_id") or ""),
                workdir=str(workdir),
                pdd_id=str(payload.get("pdd_id") or ""),
                feature_id=str(payload.get("feature_id") or ""),
                changed_files=visible_paths,
                reported_files=paths,
            )
        except Exception as exc:
            return self._reject(event, str(exc), payload)
        return TaskRefResult(status="updated", payload=entry)

    def _requires_git_handoff(self) -> bool:
        workdirs = getattr(getattr(self.config, "runtime", None), "workdirs", None)
        return bool(
            getattr(workdirs, "enabled", False)
            and getattr(workdirs, "mode", "") == "worktree"
        )

    def process_arch_proposal_done(self, event: ZfEvent) -> TaskRefResult | None:
        """Create a replayable task ref for design artifacts.

        Arch/design roles often produce docs-only handoff artifacts before any
        dev branch exists. Downstream reader roles still need a deterministic
        ref to checkout; otherwise they see the base repo and fail relative
        `artifact_refs`. Snapshot only the artifact paths declared by the arch
        event and leave unrelated worktree changes alone.
        """
        if event.type != "arch.proposal.done" or not event.task_id:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        paths = self._artifact_paths_from_payload(payload)
        if paths and not self._requires_git_handoff():
            return TaskRefResult(
                status="legacy",
                payload={
                    "task_id": event.task_id,
                    "trigger_event_id": event.id,
                    "reason": (
                        "arch.proposal.done artifact refs do not require "
                        "git task ref when workdirs are disabled"
                    ),
                    "artifact_refs": paths,
                },
            )
        if not paths:
            detected = self._detect_plan_artifacts_without_manifest(event)
            legacy_payload: dict[str, Any] = {
                "task_id": event.task_id,
                "trigger_event_id": event.id,
                "reason": "arch.proposal.done without artifact refs",
            }
            if detected:
                legacy_payload.update({
                    "fallback_warning": (
                        "plan artifacts were found in the actor workdir but no "
                        "artifact manifest or artifact_refs were emitted"
                    ),
                    "detected_artifacts": detected,
                    "required_action": (
                        "emit artifact.manifest.published with accepted contract refs"
                    ),
                })
            return TaskRefResult(
                status="rejected" if detected else "legacy",
                payload=legacy_payload,
            )
        try:
            workdir = self._actor_workdir(event)
            visible_paths = [
                path for path in paths
                if (workdir / path).exists()
            ]
            if not visible_paths:
                commit = self._git_in(workdir, "rev-parse", "HEAD").strip()
            else:
                commit = self._snapshot_artifacts(
                    event=event,
                    workdir=workdir,
                    paths=visible_paths,
                )
            source_branch = (
                self._git_in(workdir, "branch", "--show-current").strip()
                or f"detached/{event.actor or 'unknown'}"
            )
            task_ref = f"{self.config.runtime.git.task_ref_prefix}/{event.task_id}"
            self._git("check-ref-format", f"refs/heads/{task_ref}")
            self._git("update-ref", f"refs/heads/{task_ref}", commit)
            entry = self._upsert_index(
                task_id=event.task_id,
                source_commit=commit,
                source_branch=source_branch,
                task_ref=task_ref,
                actor=event.actor or "",
                trigger_event_id=event.id,
                trace_id=event.correlation_id or "",
                run_id=str(payload.get("run_id") or ""),
                workdir=str(workdir),
                pdd_id=str(payload.get("pdd_id") or ""),
                feature_id=str(payload.get("feature_id") or ""),
                changed_files=visible_paths,
                reported_files=paths,
            )
            if paths and not visible_paths:
                entry["missing_artifact_refs"] = paths
        except Exception as exc:
            return self._reject(event, str(exc), payload)

        return TaskRefResult(status="updated", payload=entry)

    def _detect_plan_artifacts_without_manifest(self, event: ZfEvent) -> list[str]:
        try:
            workdir = self._actor_workdir(event)
        except Exception:
            return []
        candidates = (
            "SPEC.md",
            "tasks/plan.md",
            "tasks/todo.md",
            "tasks/backlog.md",
            "docs/plans/plan.md",
            "docs/plans/backlog.md",
        )
        return [
            path
            for path in candidates
            if (workdir / path).is_file()
        ]

    def process_artifact_manifest_published(
        self,
        event: ZfEvent,
    ) -> TaskRefResult | None:
        if event.type != "artifact.manifest.published":
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not event.task_id and is_taskless_workflow_manifest_payload(payload):
            return None
        result = load_manifest_from_payload(
            payload,
            project_root=self.project_root,
            state_dir=self.state_dir,
            default_role=event.actor or "",
            default_task_id=event.task_id or "",
        )
        if not result.ok or result.manifest is None:
            return TaskRefResult(
                status="rejected",
                payload={
                    "task_id": event.task_id or payload.get("task_id", ""),
                    "trigger_event_id": event.id,
                    "reason": "; ".join(result.errors) or "invalid manifest",
                    "errors": list(result.errors),
                    "source": "artifact.manifest.published",
                },
            )
        manifest = result.manifest
        if event.task_id and manifest.task_id != event.task_id:
            return TaskRefResult(
                status="rejected",
                payload={
                    "task_id": event.task_id,
                    "trigger_event_id": event.id,
                    "reason": (
                        "manifest.task_id does not match event task_id "
                        f"({manifest.task_id} != {event.task_id})"
                    ),
                    "errors": ["manifest.task_id mismatch"],
                    "source": "artifact.manifest.published",
                },
            )
        return TaskRefResult(
            status="updated",
            payload=self._upsert_artifact_manifest_index(
                task_id=manifest.task_id,
                actor=event.actor or "",
                trigger_event_id=event.id,
                trace_id=event.correlation_id or "",
                manifest=manifest,
            ),
        )

    def _expected_branch(self, event: ZfEvent) -> str:
        if event.actor:
            return f"{self.config.runtime.git.writer_branch_prefix}/{event.actor}"
        return ""

    def _actor_workdir(self, event: ZfEvent) -> Path:
        payload = event.payload if isinstance(event.payload, dict) else {}
        raw = str(payload.get("workdir") or "").strip()
        if raw:
            path = Path(raw)
            if not path.is_absolute():
                path = self.project_root / path
        else:
            if not event.actor:
                raise RuntimeError("missing actor for artifact workdir")
            path = self.state_dir / "workdirs" / event.actor / "project"
        PathGuard.assert_under(path, self.state_dir / "workdirs")
        if not (path / ".git").exists():
            raise RuntimeError(f"artifact workdir is not a git worktree: {path}")
        return path

    def _reject_out_of_scope_task_ref(
        self,
        event: ZfEvent,
        payload: dict[str, Any],
        *,
        source_commit: str,
        changed_files: list[str] | None = None,
    ) -> TaskRefResult | None:
        allowed = self._task_contract_paths(event.task_id or "")
        if not allowed:
            return None
        changed = list(changed_files or [])
        if not changed:
            changed = self._changed_files_for_source_commit(
                source_commit,
                payload,
                task_id=event.task_id or "",
            )
        if not changed:
            changed = self._artifact_paths_from_payload(payload)
        return self._reject_out_of_scope_paths(
            event,
            payload,
            changed_files=changed,
            allowed_paths=allowed,
        )

    def _handoff_changed_files(
        self,
        event: ZfEvent,
        payload: dict[str, Any],
        *,
        source_commit: str,
    ) -> list[str]:
        if (
            isinstance(payload.get("changed_files"), list)
            and not payload.get("changed_files")
            and not any(
                isinstance(payload.get(key), list) and payload.get(key)
                for key in ("artifact_refs", "file_plan", "files", "files_touched")
            )
        ):
            return []
        changed = self._changed_files_for_source_commit(
            source_commit,
            payload,
            task_id=event.task_id or "",
        )
        if not changed:
            changed = self._artifact_paths_from_payload(payload)
        return _dedupe_paths(changed)

    def _reject_out_of_scope_paths(
        self,
        event: ZfEvent,
        payload: dict[str, Any],
        *,
        changed_files: list[str],
        allowed_paths: list[str] | None = None,
    ) -> TaskRefResult | None:
        allowed = allowed_paths if allowed_paths is not None else self._task_contract_paths(
            event.task_id or "",
        )
        if not allowed:
            return None
        normalized = [
            path for path in (
                self._normalize_artifact_path(item) for item in changed_files
            )
            if path
        ]
        if not normalized:
            return None
        out_of_scope = [
            path for path in normalized
            if not _path_allowed_by_scope(path, allowed)
        ]
        if not out_of_scope:
            return None
        if len(out_of_scope) == len(normalized) and _scope_matches_under_common_root(
            normalized, allowed,
        ):
            # Contract scope globs can be authored relative to a project
            # sub-root (refactor task_map dialect) while git reports
            # repo-root-relative paths; when EVERY changed file fails yet all
            # of them match the scope under one shared leading directory, the
            # mismatch is root anchoring, not contamination (HIC-137BD6E031).
            return None
        result = self._reject(
            event,
            "source_commit changes outside task contract scope",
            payload,
        )
        result.payload.update({
            "scope": list(allowed),
            "changed_files": normalized,
            "out_of_scope_files": out_of_scope,
        })
        return result

    def _task_contract_paths(self, task_id: str) -> list[str]:
        if not task_id:
            return []
        try:
            task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        except Exception:
            return []
        if task is None or task.contract is None:
            return []
        contract = task.contract
        paths: list[str] = []
        for field in (
            "scope",
            "affected_files",
            "exclusive_files",
            "shared_files",
            "handoff_artifacts",
        ):
            value = getattr(contract, field, [])
            if not isinstance(value, list):
                continue
            for item in value:
                normalized = self._normalize_artifact_path(item)
                if normalized and normalized not in paths:
                    paths.append(normalized)
        return paths

    def _changed_files_for_source_commit(
        self,
        source_commit: str,
        payload: dict[str, Any],
        *,
        task_id: str = "",
    ) -> list[str]:
        base = self._workdir_dependency_base(payload, source_commit)
        if not base:
            base = str(payload.get("base_git_head") or "").strip()
        if not base:
            base = self._nearest_dependency_task_ref_base(task_id, source_commit)
        if not base:
            base = self._nearest_accepted_task_ref_base(source_commit)
        if not base:
            try:
                base = self._git("rev-parse", "--verify", "HEAD^{commit}").strip()
            except Exception:
                base = ""
        if not base:
            return []
        try:
            base_commit = self._git("rev-parse", "--verify", f"{base}^{{commit}}").strip()
            source = self._git(
                "rev-parse",
                "--verify",
                f"{source_commit}^{{commit}}",
            ).strip()
            raw = self._git("diff", "--name-only", f"{base_commit}..{source}")
        except Exception:
            return []
        return [
            self._normalize_artifact_path(line)
            for line in raw.splitlines()
            if self._normalize_artifact_path(line)
        ]

    def _workdir_dependency_base(
        self,
        payload: dict[str, Any],
        source_commit: str,
    ) -> str:
        raw = str(payload.get("workdir") or "").strip()
        if not raw:
            return ""
        path = Path(raw)
        if not path.is_absolute():
            path = self.project_root / path
        try:
            PathGuard.assert_under(path, self.state_dir / "workdirs")
        except Exception:
            return ""
        meta_path = path.parent / "meta.json" if path.name == "project" else path / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(meta, dict):
            return ""
        dependency_after = str(meta.get("dependency_after") or "").strip()
        if not dependency_after:
            return ""
        try:
            base = self._git("rev-parse", "--verify", f"{dependency_after}^{{commit}}").strip()
            self._git("rev-parse", "--verify", f"{source_commit}^{{commit}}")
        except Exception:
            return ""
        return base

    def _nearest_dependency_task_ref_base(self, task_id: str, source_commit: str) -> str:
        if not task_id:
            return ""
        try:
            task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        except Exception:
            return ""
        if task is None:
            return ""
        task_index = self._read_index_unlocked()
        best = ""
        best_distance = -1
        for blocker_id in task.blocked_by or []:
            entry = task_index.get(blocker_id)
            if not isinstance(entry, dict):
                continue
            candidates = [
                str(entry.get("source_commit") or "").strip(),
            ]
            task_ref = str(entry.get("task_ref") or "").strip()
            if task_ref:
                candidates.append(f"refs/heads/{task_ref}")
            for candidate in candidates:
                if not candidate:
                    continue
                try:
                    commit = self._git(
                        "rev-parse",
                        "--verify",
                        f"{candidate}^{{commit}}",
                    ).strip()
                    raw = self._git("diff", "--name-only", f"{commit}..{source_commit}")
                except Exception:
                    continue
                distance = len([line for line in raw.splitlines() if line.strip()])
                if best_distance < 0 or distance < best_distance:
                    best = commit
                    best_distance = distance
        return best

    def _nearest_accepted_task_ref_base(self, source_commit: str) -> str:
        """Nearest accepted-handoff task ref that is an ancestor of source_commit.

        On a shared lane branch, commits reachable from a previously accepted
        task ref already passed that task's scope gate and are not part of the
        current handoff's changes.
        """
        prefix = self.config.runtime.git.task_ref_prefix
        try:
            raw = self._git(
                "for-each-ref",
                "--format=%(objectname)",
                f"refs/heads/{prefix}",
            )
        except Exception:
            return ""
        best = ""
        best_distance = -1
        for line in raw.splitlines():
            candidate = line.strip()
            if not candidate or not self._is_ancestor(candidate, source_commit):
                continue
            try:
                distance = int(
                    self._git(
                        "rev-list",
                        "--count",
                        f"{candidate}..{source_commit}",
                    ).strip(),
                )
            except Exception:
                continue
            if best_distance < 0 or distance < best_distance:
                best = candidate
                best_distance = distance
        return best

    def _artifact_paths_from_payload(self, payload: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for key in (
            "artifact_refs",
            "changed_files",
            "file_plan",
            "files",
            "files_touched",
        ):
            value = payload.get(key)
            if isinstance(value, str):
                candidates: list[Any] = [value]
            elif isinstance(value, list):
                candidates = value
            else:
                continue
            for item in candidates:
                path = self._normalize_artifact_path(item)
                if path and path not in out:
                    out.append(path)
        return out

    @staticmethod
    def _normalize_artifact_path(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith(_NON_FILE_EVIDENCE_REF_PREFIXES):
            return ""
        path = Path(text)
        if path.is_absolute() or ".." in path.parts:
            return ""
        if text.startswith(("arch.proposal.done:", "dev.build.done:", "review.")):
            return ""
        return path.as_posix()

    def _snapshot_artifacts(
        self,
        *,
        event: ZfEvent,
        workdir: Path,
        paths: list[str],
    ) -> str:
        self._git_in(workdir, "add", "--force", "--", *paths)
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", *paths],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode not in (0, 1):
            detail = diff.stderr.strip() or diff.stdout.strip()
            raise RuntimeError(f"git diff --cached failed in {workdir}: {detail}")
        if diff.returncode == 1:
            self._git_in(
                workdir,
                "-c",
                "user.name=ZaoFu",
                "-c",
                "user.email=zaofu@example.invalid",
                "commit",
                "-m",
                f"zf handoff {event.task_id}",
                "--only",
                "--",
                *paths,
            )
        commit = self._git_in(
            workdir,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ).strip()
        for path in paths:
            self._git_in(workdir, "cat-file", "-e", f"HEAD:{path}")
        return commit

    def _validate_workdir(
        self,
        workdir: str,
        full_commit: str,
        *,
        allow_ignorable_dirty: bool = False,
        allow_head_mismatch: bool = False,
    ) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {}
        path = Path(workdir)
        if not path.is_absolute():
            path = self.project_root / path
        PathGuard.assert_under(path, self.state_dir / "workdirs")
        seen = self._git_in(path, "rev-parse", "--verify", f"{full_commit}^{{commit}}").strip()
        if seen != full_commit:
            raise RuntimeError(f"commit {full_commit} is not visible from workdir {path}")
        head = self._git_in(path, "rev-parse", "--verify", "HEAD^{commit}").strip()
        if head != full_commit:
            if not allow_head_mismatch:
                raise RuntimeError(
                    f"workdir HEAD {head} does not match source_commit {full_commit}"
                )
            diagnostics["workdir_head_mismatch"] = {
                "workdir_head": head,
                "source_commit": full_commit,
                "accepted_reason": "source_commit is visible and source branch was rewound",
            }
        status = self._git_in(path, "status", "--porcelain", "--untracked-files=all").strip()
        if status:
            dirty_files = _dirty_files_from_git_status(status)
            runtime_dirty = _runtime_materialized_dirty_files(dirty_files)
            if runtime_dirty:
                diagnostics["ignored_runtime_dirty_files"] = runtime_dirty
                dirty_files = [f for f in dirty_files if f not in runtime_dirty]
            if dirty_files:
                ignored = _ignorable_handoff_dirty_files(dirty_files)
                if allow_ignorable_dirty and len(ignored) == len(dirty_files):
                    diagnostics["ignored_dirty_files"] = ignored
                    return diagnostics
                raise RuntimeError(
                    f"workdir {path} has uncommitted changes; commit or clean before handoff"
                )
        return diagnostics

    def _ignorable_handoff_dirty_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        dirty_files = _dirty_files_from_payload(payload.get("dirty_files"))
        ignored = _ignorable_handoff_dirty_files(dirty_files)
        if not dirty_files or len(ignored) != len(dirty_files):
            return None
        diagnostics: dict[str, Any] = {"ignored_dirty_files": ignored}
        note = str(payload.get("dirty_scope_note") or "").strip()
        if note:
            diagnostics["dirty_scope_note"] = note
        return diagnostics

    def _accepts_rewound_branch_handoff(
        self,
        *,
        full_commit: str,
        branch_head: str,
        payload: dict[str, Any],
    ) -> bool:
        if self._is_ancestor(branch_head, full_commit):
            return True
        for base in self._declared_base_candidates(payload):
            if self._is_ancestor(base, full_commit):
                return True
        return False

    def _declared_base_candidates(self, payload: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("base_git_head", "base_commit", "target_base_commit"):
            value = str(payload.get(key) or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        anchors = payload.get("git_anchors")
        if isinstance(anchors, dict):
            for key in ("candidate_base_commit", "base_git_head", "base_commit"):
                value = str(anchors.get(key) or "").strip()
                if value and value not in candidates:
                    candidates.append(value)
        try:
            head = self._git("rev-parse", "--verify", "HEAD^{commit}").strip()
        except Exception:
            head = ""
        if head and head not in candidates:
            candidates.append(head)
        return candidates

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        if not ancestor or not descendant:
            return False
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _upsert_index(
        self,
        *,
        task_id: str,
        source_commit: str,
        source_branch: str,
        task_ref: str,
        actor: str,
        trigger_event_id: str,
        trace_id: str,
        run_id: str,
        workdir: str,
        pdd_id: str,
        feature_id: str,
        diagnostics: dict[str, Any] | None = None,
        changed_files: list[str] | None = None,
        reported_files: list[str] | None = None,
    ) -> dict[str, Any]:
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "task_id": task_id,
            "source_commit": source_commit,
            "source_branch": source_branch,
            "task_ref": task_ref,
            "actor": actor,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "trace_id": trace_id,
            "trigger_event_id": trigger_event_id,
            "workdir": workdir,
            "pdd_id": pdd_id,
            "feature_id": feature_id,
        }
        normalized_changed = _dedupe_paths(changed_files or [])
        normalized_reported = _dedupe_paths(reported_files or [])
        if normalized_changed:
            entry["changed_files"] = normalized_changed
        if normalized_reported:
            entry["reported_files"] = normalized_reported
        if diagnostics:
            entry["diagnostics"] = diagnostics
        with locked_path(self.index_path):
            data = self._read_index_unlocked()
            data[task_id] = entry
            atomic_write_text(
                self.index_path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        return entry

    def _upsert_artifact_manifest_index(
        self,
        *,
        task_id: str,
        actor: str,
        trigger_event_id: str,
        trace_id: str,
        manifest,
    ) -> dict[str, Any]:
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        contract_refs = contract_refs_from_manifest(
            manifest,
            event_id=trigger_event_id,
        )
        candidate_contract_refs = _candidate_contract_refs_from_manifest(
            manifest,
            event_id=trigger_event_id,
        )
        with locked_path(self.index_path):
            data = self._read_index_unlocked()
            entry = dict(data.get(task_id) or {"task_id": task_id})
            existing_refs = [
                item for item in entry.get("artifact_refs", [])
                if isinstance(item, dict)
            ]
            manifest_refs = [
                _artifact_ref_with_ledger_metadata(
                    ref.to_dict(),
                    task_id=task_id,
                    existing_refs=existing_refs,
                    trigger_event_id=trigger_event_id,
                )
                for ref in manifest.artifact_refs
            ]
            existing_refs = _mark_superseded_artifact_refs(
                existing_refs,
                manifest_refs,
            )
            merged_refs = _dedupe_artifact_refs([*existing_refs, *manifest_refs])
            refs_by_kind = _artifact_refs_by_kind_from_refs(merged_refs)
            hash_status = [
                verify_artifact_ref(
                    ref,
                    project_root=self.project_root,
                    state_dir=self.state_dir,
                )
                for ref in merged_refs
            ]
            existing_contract_refs = entry.get("contract_refs")
            if not isinstance(existing_contract_refs, dict):
                existing_contract_refs = {}
            existing_contract_refs.update(contract_refs)
            entry.update({
                "task_id": task_id,
                "actor": actor or entry.get("actor", ""),
                "trace_id": trace_id or entry.get("trace_id", ""),
                "trigger_event_id": trigger_event_id,
                "manifest_event_id": trigger_event_id,
                "manifest_role": manifest.role,
                "skills_used": list(manifest.skills_used),
                "artifact_refs": merged_refs,
                "artifact_refs_by_kind": refs_by_kind,
                "hash_status": hash_status,
                "contract_refs": existing_contract_refs,
                "candidate_contract_refs": candidate_contract_refs,
                "handoff_contract": dict(manifest.handoff_contract),
                "feature_id": manifest.feature_id or entry.get("feature_id", ""),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            data[task_id] = entry
            atomic_write_text(
                self.index_path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        if manifest.feature_id:
            self._upsert_feature_artifact_index(
                feature_id=manifest.feature_id,
                task_id=task_id,
                task_entry=entry,
            )
        return entry

    def _upsert_feature_artifact_index(
        self,
        *,
        feature_id: str,
        task_id: str,
        task_entry: dict[str, Any],
    ) -> None:
        feature_index_path = self.refs_dir / "feature-index.json"
        with locked_path(feature_index_path):
            try:
                data = json.loads(feature_index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            entry = dict(data.get(feature_id) or {"feature_id": feature_id})
            tasks = entry.get("tasks")
            if not isinstance(tasks, dict):
                tasks = {}
            tasks[task_id] = {
                "task_id": task_id,
                "artifact_refs_by_kind": task_entry.get("artifact_refs_by_kind", {}),
                "hash_status": task_entry.get("hash_status", []),
                "contract_refs": task_entry.get("contract_refs", {}),
                "manifest_event_id": task_entry.get("manifest_event_id", ""),
                "manifest_role": task_entry.get("manifest_role", ""),
            }
            current_bundle = _feature_delivery_bundle_from_task_entry(
                feature_id=feature_id,
                task_id=task_id,
                task_entry=task_entry,
            )
            if current_bundle:
                history = entry.get("bundle_history")
                if not isinstance(history, list):
                    history = []
                task_map_ref = str(current_bundle.get("current_task_map_ref") or "")
                manifest_event_id = str(current_bundle.get("manifest_event_id") or "")
                deduped_history: list[dict[str, Any]] = []
                replaced = False
                for item in history:
                    if not isinstance(item, dict):
                        continue
                    existing_ref = str(item.get("current_task_map_ref") or "")
                    existing_event = str(item.get("manifest_event_id") or "")
                    row = dict(item)
                    row["is_current"] = False
                    if existing_ref == task_map_ref and existing_event == manifest_event_id:
                        row.update(current_bundle)
                        row["is_current"] = True
                        replaced = True
                    deduped_history.append(row)
                if not replaced:
                    current_bundle["is_current"] = True
                    deduped_history.append(dict(current_bundle))
                else:
                    current_bundle["is_current"] = True
                entry["current_bundle"] = current_bundle
                entry["bundle_history"] = deduped_history
                entry["current_task_map_ref"] = current_bundle.get("current_task_map_ref", "")
                entry["current_source_index_ref"] = current_bundle.get(
                    "current_source_index_ref", "",
                )
                entry["current_coverage_report_ref"] = current_bundle.get(
                    "current_coverage_report_ref", "",
                )
            entry["feature_id"] = feature_id
            entry["tasks"] = tasks
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            data[feature_id] = entry
            atomic_write_text(
                feature_index_path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )

    def _read_index_unlocked(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _reject(
        self,
        event: ZfEvent,
        reason: str,
        payload: dict[str, Any],
    ) -> TaskRefResult:
        return TaskRefResult(
            status="rejected",
            payload={
                "task_id": event.task_id,
                "trigger_event_id": event.id,
                "reason": reason,
                "source_commit": payload.get("source_commit", ""),
                "source_branch": payload.get("source_branch", ""),
                "workdir": payload.get("workdir", ""),
            },
        )

    def _git(self, *args: str) -> str:
        return self._git_in(self.project_root, *args)

    @staticmethod
    def _git_in(cwd: Path, *args: str) -> str:
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


def _dedupe_artifact_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[tuple[str, str, str]] = []
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref in refs:
        key = (
            str(ref.get("kind") or ""),
            str(ref.get("path") or ""),
            str(ref.get("sha256") or ""),
        )
        if key not in by_key:
            order.append(key)
        by_key[key] = dict(ref)
    return [by_key[key] for key in order]


def _path_allowed_by_scope(path: str, scope: list[str]) -> bool:
    normalized = TaskRefManager._normalize_artifact_path(path)
    if not normalized:
        return True
    # Refactor task_maps author scope globs in the target-project frame
    # (packages/pi-core/**) while git diff reports host-repo paths
    # (cj-min/packages/pi-core/...). Retry the match once with the leading
    # component stripped; a sibling slice's files still cannot match this
    # task's globs after the strip, so slice isolation holds.
    candidates = [normalized]
    if "/" in normalized:
        candidates.append(normalized.split("/", 1)[1])
    for raw_allowed in scope:
        allowed = TaskRefManager._normalize_artifact_path(raw_allowed)
        if not allowed:
            continue
        if allowed in {"*", "**"}:
            return True
        directory = allowed.rstrip("/")
        for candidate in candidates:
            if candidate == allowed:
                return True
            if fnmatch(candidate, allowed):
                return True
            if directory and candidate.startswith(f"{directory}/"):
                return True
    return False


def _scope_matches_under_common_root(paths: list[str], scope: list[str]) -> bool:
    """True when ALL paths pass the scope check once a common leading
    directory prefix is stripped.

    A partial failure is real contamination and never qualifies (callers must
    only invoke this when every path failed the direct check). Stripping is
    only attempted on prefixes shared by every path, so a slice writer leaking
    into a sibling slice (`<root>/packages/agent/...` against
    `packages/gateway/**`) still rejects.
    """
    if not paths or not scope:
        return False
    split = [path.split("/") for path in paths]
    common = min(len(parts) for parts in split) - 1
    for depth in range(1, common + 1):
        head = split[0][:depth]
        if any(parts[:depth] != head for parts in split[1:]):
            return False
        rebased = ["/".join(parts[depth:]) for parts in split]
        if all(_path_allowed_by_scope(path, scope) for path in rebased):
            return True
    return False


def _dedupe_paths(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _reported_files_mismatch(
    *,
    reported_files: list[str],
    changed_files: list[str],
) -> dict[str, Any]:
    reported = set(_dedupe_paths(reported_files))
    changed = set(_dedupe_paths(changed_files))
    if not reported or not changed:
        return {}
    missing = sorted(changed - reported)
    extra = sorted(reported - changed)
    if not missing and not extra:
        return {}
    return {
        "missing_from_report": missing,
        "extra_reported": extra,
        "accepted_reason": "candidate integration uses git-derived changed_files",
    }


def _dirty_files_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    out: list[str] = []
    for item in raw_items:
        normalized = TaskRefManager._normalize_artifact_path(item)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _dirty_files_from_git_status(status: str) -> list[str]:
    out: list[str] = []
    for raw_line in status.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        # Callers .strip() the whole porcelain blob, so the first unstaged
        # line (" M path") lands as "M path"; slice from col 2 (not 3) so its
        # path parses whole (else ".zf/x" -> "zf/x" defeats runtime ignore).
        path = line[2:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        normalized = TaskRefManager._normalize_artifact_path(path)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _ignorable_handoff_dirty_files(paths: list[str]) -> list[str]:
    return [
        path for path in paths
        if path in _IGNORABLE_HANDOFF_DIRTY_PATHS
    ]


def runtime_materialized_dirty_files(paths: list[str]) -> list[str]:
    return [
        path for path in paths
        if path in _RUNTIME_MATERIALIZED_DIRTY_PATHS
        or path.startswith(_RUNTIME_MATERIALIZED_DIRTY_PREFIXES)
    ]


def _runtime_materialized_dirty_files(paths: list[str]) -> list[str]:
    return runtime_materialized_dirty_files(paths)


def _artifact_ref_with_ledger_metadata(
    ref: dict[str, Any],
    *,
    task_id: str,
    existing_refs: list[dict[str, Any]],
    trigger_event_id: str,
) -> dict[str, Any]:
    """Upgrade a worker-authored artifact ref into a ledger entry."""
    entry = dict(ref)
    exact = _exact_existing_artifact_ref(entry, existing_refs)
    if exact is not None:
        reused = dict(exact)
        for key in ("summary", "workdir_path", "commit"):
            if str(entry.get(key) or "").strip():
                reused[key] = entry[key]
        incoming_status = str(entry.get("status") or "").strip()
        if incoming_status:
            reused["status"] = incoming_status
        if reused.get("status") == "accepted" and not str(reused.get("accepted_event_id") or ""):
            reused["accepted_event_id"] = trigger_event_id
        return reused
    prior = _latest_prior_artifact_ref(entry, existing_refs)
    try:
        version = int(entry.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    prior_version = _artifact_version(prior) if prior is not None else 0
    if version <= prior_version:
        version = prior_version + 1 if prior is not None else 1
    prior_artifact_id = str(prior.get("artifact_id") or "") if prior else ""
    artifact_id = str(entry.get("artifact_id") or "").strip()
    if not artifact_id:
        artifact_id = (
            f"{_artifact_slug(str(entry.get('kind') or 'artifact'))}-"
            f"{_artifact_slug(task_id)}-v{version}"
        )
    status = str(entry.get("status") or "accepted")
    accepted_event_id = str(entry.get("accepted_event_id") or "")
    if status == "accepted" and not accepted_event_id:
        accepted_event_id = trigger_event_id
    entry.update({
        "artifact_id": artifact_id,
        "version": version,
        "supersedes": str(entry.get("supersedes") or prior_artifact_id),
        "status": status,
        "source_event_id": str(entry.get("source_event_id") or trigger_event_id),
        "accepted_event_id": accepted_event_id,
    })
    return entry


def _mark_superseded_artifact_refs(
    existing_refs: list[dict[str, Any]],
    manifest_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    superseded_ids = {
        str(ref.get("supersedes") or "").strip()
        for ref in manifest_refs
        if str(ref.get("supersedes") or "").strip()
    }
    superseded_paths = set(superseded_ids)
    if not superseded_ids:
        return [dict(ref) for ref in existing_refs]
    out: list[dict[str, Any]] = []
    for ref in existing_refs:
        item = dict(ref)
        if (
            str(item.get("artifact_id") or "").strip() in superseded_ids
            or str(item.get("path") or "").strip() in superseded_paths
        ):
            item["status"] = "superseded"
        out.append(item)
    return out


def _exact_existing_artifact_ref(
    ref: dict[str, Any],
    existing_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    kind = normalize_artifact_kind(str(ref.get("kind") or ""))
    path = str(ref.get("path") or "")
    sha256 = str(ref.get("sha256") or "")
    for item in reversed(existing_refs):
        if normalize_artifact_kind(str(item.get("kind") or "")) != kind:
            continue
        if str(item.get("path") or "") != path:
            continue
        if sha256 and str(item.get("sha256") or "") != sha256:
            continue
        return item
    return None


def _latest_prior_artifact_ref(
    ref: dict[str, Any],
    existing_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    kind = normalize_artifact_kind(str(ref.get("kind") or ""))
    candidates = [
        (idx, item) for idx, item in enumerate(existing_refs)
        if normalize_artifact_kind(str(item.get("kind") or "")) == kind
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (_artifact_version(item[1]), item[0]))[1]


def _artifact_version(ref: dict[str, Any] | None) -> int:
    if ref is None:
        return 0
    try:
        return int(ref.get("version") or 0)
    except (TypeError, ValueError):
        return 0


def _artifact_refs_by_kind_from_refs(
    refs: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        kind = str(ref.get("kind") or "").strip()
        if kind:
            out.setdefault(kind, []).append(ref)
    return out


def _feature_delivery_bundle_from_task_entry(
    *,
    feature_id: str,
    task_id: str,
    task_entry: dict[str, Any],
) -> dict[str, Any]:
    task_map = _latest_accepted_artifact_ref(task_entry, "task_map")
    if not task_map:
        return {}
    source_index = _latest_accepted_artifact_ref(task_entry, "source_index")
    coverage = _latest_accepted_artifact_ref(task_entry, "coverage_report")
    plan = _latest_accepted_artifact_ref(task_entry, "implementation_plan")
    tdd = _latest_accepted_artifact_ref(task_entry, "tdd")
    contract_refs = task_entry.get("contract_refs")
    if not isinstance(contract_refs, dict):
        contract_refs = {}
    version = _artifact_version(task_map)
    accepted_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "feature-delivery-bundle.v1",
        "feature_id": feature_id,
        "handoff_task_id": task_id,
        "current_task_map_ref": str(task_map.get("path") or ""),
        "current_source_index_ref": str(
            (source_index or {}).get("path")
            or _artifact_ref_from_evidence_contract(contract_refs, "source_index_ref")
        ),
        "current_coverage_report_ref": str(
            (coverage or {}).get("path")
            or _artifact_ref_from_evidence_contract(contract_refs, "coverage_report_ref")
        ),
        "plan_ref": str(
            (plan or {}).get("path")
            or contract_refs.get("plan_ref", "")
        ),
        "tdd_ref": str(
            (tdd or {}).get("path")
            or contract_refs.get("tdd_ref", "")
        ),
        "version": version,
        "supersedes": str(task_map.get("supersedes") or ""),
        "artifact_id": str(task_map.get("artifact_id") or ""),
        "manifest_event_id": str(task_entry.get("manifest_event_id") or ""),
        "manifest_role": str(task_entry.get("manifest_role") or ""),
        "accepted_event_id": str(task_map.get("accepted_event_id") or ""),
        "accepted_at": accepted_at,
        "hash_status": task_entry.get("hash_status", []),
    }


def _latest_accepted_artifact_ref(
    task_entry: dict[str, Any],
    normalized_kind: str,
) -> dict[str, Any]:
    refs = [
        ref for ref in task_entry.get("artifact_refs", [])
        if isinstance(ref, dict)
        and normalize_artifact_kind(str(ref.get("kind") or "")) == normalized_kind
        and str(ref.get("status") or "accepted") == "accepted"
    ]
    if not refs:
        return {}
    _, latest = max(
        enumerate(refs),
        key=lambda item: (_artifact_version(item[1]), item[0]),
    )
    return latest


def _artifact_ref_from_evidence_contract(
    contract_refs: dict[str, Any],
    key: str,
) -> str:
    evidence_contract = contract_refs.get("evidence_contract")
    if not isinstance(evidence_contract, dict):
        return ""
    artifact_refs = evidence_contract.get("artifact_refs")
    if not isinstance(artifact_refs, dict):
        return ""
    return str(artifact_refs.get(key) or "")


def _artifact_slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    text = "-".join(part for part in text.split("-") if part)
    return text or "artifact"


def _candidate_contract_refs_from_manifest(manifest, *, event_id: str) -> dict[str, Any]:
    """Map draft/proposed refs separately from accepted dispatch refs."""
    refs: dict[str, Any] = {}
    evidence_refs: dict[str, str] = {}
    for ref in manifest.artifact_refs:
        if str(getattr(ref, "status", "") or "") not in {"draft", "proposed"}:
            continue
        kind = normalize_artifact_kind(str(getattr(ref, "kind", "") or ""))
        path = str(getattr(ref, "path", "") or "").strip()
        if not path:
            continue
        if kind in {"spec"} and not refs.get("spec_ref"):
            refs["spec_ref"] = path
        elif kind in {"implementation_plan", "process_plan", "backlog_plan"} and not refs.get("plan_ref"):
            refs["plan_ref"] = path
        elif kind in {"tdd", "test_plan"} and not refs.get("tdd_ref"):
            refs["tdd_ref"] = path
        elif kind == "critic_gate" and not refs.get("critic_gate_ref"):
            refs["critic_gate_ref"] = path
            refs.setdefault("critic_event_id", event_id)
        if kind == "task_map" and not evidence_refs.get("task_map_ref"):
            evidence_refs["task_map_ref"] = path
        elif kind == "backlog_plan" and not evidence_refs.get("backlog_plan_ref"):
            evidence_refs["backlog_plan_ref"] = path
    if refs or evidence_refs:
        refs["artifact_manifest_event_id"] = event_id
    if evidence_refs:
        refs["artifact_refs"] = evidence_refs
    return refs
