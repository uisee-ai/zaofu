"""Candidate branch rebuild from approved task refs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.task.store import TaskStore
from zf.core.verification.evidence import command_evidence
from zf.runtime.git_capture import git_env
from zf.runtime.worktree_env import provision_worktree_env, run_project_setup
from zf.runtime.verification_commands import task_contract_verification_commands


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_FEATURE_KEY_RE = re.compile(r"^(F-[0-9A-Fa-f]+)[:\-]")
_DIFF_CHECK_LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>[0-9]+): (?P<message>.+)$"
)
_TERMINAL_CANDIDATES = ("judge.passed", "verify.passed", "test.passed", "review.approved")
_QUALITY_TIMEOUT_SECONDS = 300
_AUTOFIXABLE_DIFF_MESSAGES = frozenset({
    "trailing whitespace.",
    "new blank line at EOF.",
})


@dataclass(frozen=True)
class CandidateTask:
    task_id: str
    task_ref: str
    source_commit: str
    approval_event_id: str
    approval_event_type: str


@dataclass(frozen=True)
class CandidateResult:
    status: str
    event_type: str
    payload: dict


class CandidateRebuilder:
    """Rebuild ``candidate/<pdd_id>`` as a reproducible projection.

    Truth remains in events + ``.zf/refs/task-index.json``. The candidate
    branch and manifest are derived artifacts.
    """

    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig,
        event_log: EventLog,
    ) -> None:
        self.state_dir = state_dir
        self.project_root = project_root
        self.config = config
        self.event_log = event_log
        self.task_store = TaskStore(state_dir / "kanban.json")

    def rebuild_for_event(
        self,
        event: ZfEvent,
        *,
        event_writer: EventWriter | None = None,
    ) -> CandidateResult | None:
        if event.type != self.terminal_gate() or not event.task_id:
            return None
        pdd_id = self.pdd_id_for_task(event.task_id, event)
        return self.rebuild(
            pdd_id,
            event_writer=event_writer,
            trigger_event=event,
        )

    def rebuild(
        self,
        pdd_id: str,
        *,
        event_writer: EventWriter | None = None,
        trigger_event: ZfEvent | None = None,
        task_ids: list[str] | None = None,
    ) -> CandidateResult | None:
        pdd_id = self._validate_id(pdd_id)
        tasks = (
            self.approved_tasks(pdd_id)
            if task_ids is None
            else self.tasks_from_index(pdd_id, task_ids)
        )
        if not tasks:
            return None
        tasks = self._sync_tasks_with_latest_completions(
            tasks,
            event_writer=event_writer,
        )
        requested_tasks = list(tasks)
        tasks = self._with_dependency_task_refs(pdd_id, tasks)

        branch = f"{self.config.runtime.git.candidate_branch_prefix}/{pdd_id}"
        requested_base_ref = self.config.runtime.git.candidate_base_ref
        base_ref = self._resolve_candidate_base_ref(
            requested_base_ref,
            pdd_id=pdd_id,
            branch=branch,
            task_ids=task_ids,
        )
        strategy = self.config.runtime.git.candidate_strategy
        manifest_path = self._manifest_path(pdd_id)
        task_refs = [task.task_ref for task in tasks]

        started = None
        integration_started = None
        if event_writer is not None:
            started = event_writer.append(ZfEvent(
                type="candidate.started",
                actor="zf-cli",
                payload={
                    "pdd_id": pdd_id,
                    "branch": branch,
                    "base": base_ref,
                    "requested_base": requested_base_ref,
                    "strategy": strategy,
                    "task_refs": task_refs,
                },
                causation_id=trigger_event.id if trigger_event else None,
                correlation_id=trigger_event.correlation_id if trigger_event else None,
            ))
            integration_started = event_writer.append(ZfEvent(
                type="candidate.integration.started",
                actor="zf-cli",
                payload={
                    "pdd_id": pdd_id,
                    "branch": branch,
                    "base": base_ref,
                    "requested_base": requested_base_ref,
                    "strategy": strategy,
                    "task_refs": task_refs,
                    "merger_worktree": str(self._worktree_path(pdd_id)),
                },
                causation_id=started.id,
                correlation_id=trigger_event.correlation_id if trigger_event else None,
            ))

        with locked_path(manifest_path):
            result = self._rebuild_locked(
                pdd_id=pdd_id,
                branch=branch,
                base_ref=base_ref,
                strategy=strategy,
                tasks=tasks,
                requested_tasks=requested_tasks,
                manifest_path=manifest_path,
                event_writer=event_writer,
                causation_id=integration_started.id if integration_started else (
                    started.id if started else None
                ),
                correlation_id=trigger_event.correlation_id if trigger_event else None,
            )

        if event_writer is not None:
            # canonical-dag v1/v2/v3 require fanout_id on candidate.conflict,
            # but manifest-derived payloads never carried it — under a blocking
            # discriminator the real conflict signal would be rejected (same
            # wedge class as refactor.scan.failed, 2026-07-10 audit). Enrich
            # from the trigger event when the manifest payload lacks it.
            result_payload = dict(result.payload)
            if not result_payload.get("fanout_id") and trigger_event is not None:
                trigger_payload = (
                    trigger_event.payload
                    if isinstance(trigger_event.payload, dict) else {}
                )
                trigger_fanout_id = str(trigger_payload.get("fanout_id") or "")
                if trigger_fanout_id:
                    result_payload["fanout_id"] = trigger_fanout_id
            final = event_writer.append(ZfEvent(
                type=result.event_type,
                actor="zf-cli",
                payload=result_payload,
                causation_id=started.id if started else (
                    trigger_event.id if trigger_event else None
                ),
                correlation_id=trigger_event.correlation_id if trigger_event else None,
            ))
            event_writer.append(ZfEvent(
                type="candidate.integration.completed",
                actor="zf-cli",
                payload={
                    "pdd_id": pdd_id,
                    "branch": branch,
                    "status": result.status,
                    "event_type": result.event_type,
                    "commit": str(result.payload.get("commit") or ""),
                    "quality_status": str(result.payload.get("quality_status") or ""),
                    "failed_task_id": str(result.payload.get("task_id") or ""),
                    "failed_task_ref": str(result.payload.get("task_ref") or ""),
                },
                causation_id=final.id,
                correlation_id=trigger_event.correlation_id if trigger_event else None,
            ))
        return result

    def terminal_gate(self) -> str:
        publishes = {
            event_type
            for role in self.config.roles
            for event_type in role.publishes
        }
        for candidate in _TERMINAL_CANDIDATES:
            if candidate in publishes:
                return candidate
        return "review.approved"

    def _resolve_candidate_base_ref(
        self,
        requested_ref: str,
        *,
        pdd_id: str = "",
        branch: str = "",
        task_ids: list[str] | None = None,
    ) -> str:
        incremental_base = self._incremental_candidate_base_ref(
            pdd_id=pdd_id,
            branch=branch,
            task_ids=task_ids,
        )
        if incremental_base:
            return incremental_base
        ref = str(requested_ref or "").strip() or "main"
        if self._git_ref_exists(ref):
            return ref
        if ref != "main":
            return ref
        fallback = self._current_branch()
        if fallback and self._git_ref_exists(fallback):
            return fallback
        if self._git_ref_exists("HEAD"):
            return "HEAD"
        return ref

    def _incremental_candidate_base_ref(
        self,
        *,
        pdd_id: str,
        branch: str,
        task_ids: list[str] | None,
    ) -> str:
        """Use the previous candidate as the base for subset rebuilds.

        ``task_ids`` is used by candidate-integration fanout when only a subset
        of tasks is being resumed, such as module-parity gap amends. Starting
        from ``main`` in that case drops the already validated assembly/root
        layer and produces a partial candidate. Prefer the existing candidate
        branch, then the last manifest commit, while full rebuilds keep the
        configured base. A failed/stale/conflicted candidate is never a
        validated baseline and must be rebuilt from the configured base.
        """
        if not task_ids:
            return ""
        manifest = self._read_manifest(pdd_id)
        if str(manifest.get("status") or "") != "updated":
            return ""
        branch_ref = f"refs/heads/{branch}" if branch else ""
        if branch_ref and self._git_ref_exists(branch_ref):
            return branch_ref
        commit = str(manifest.get("commit") or "").strip()
        if commit and self._git_ref_exists(commit):
            return commit
        return ""

    def _git_ref_exists(self, ref: str) -> bool:
        if not ref:
            return False
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        return result.returncode == 0

    def _current_branch(self) -> str:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def approved_tasks(self, pdd_id: str) -> list[CandidateTask]:
        pdd_id = self._validate_id(pdd_id)
        index = self._task_index()
        approvals: dict[str, ZfEvent] = {}
        terminal_gate = self.terminal_gate()
        for event in self.event_log.read_all():
            if event.type != terminal_gate or not event.task_id:
                continue
            if self.pdd_id_for_task(event.task_id, event) != pdd_id:
                continue
            approvals[event.task_id] = event

        creation_order = self._task_creation_order()
        task_map_order = self._task_map_order(pdd_id)
        ordered_ids = self._ordered_task_ids(
            approvals.keys(),
            task_map_order=task_map_order,
            creation_order=creation_order,
        )

        out: list[CandidateTask] = []
        for task_id in ordered_ids:
            entry = index.get(task_id)
            approval = approvals.get(task_id)
            if not isinstance(entry, dict) or approval is None:
                continue
            task_ref = str(entry.get("task_ref") or "")
            source_commit = str(entry.get("source_commit") or "")
            if not task_ref or not source_commit:
                continue
            out.append(CandidateTask(
                task_id=task_id,
                task_ref=task_ref,
                source_commit=source_commit,
                approval_event_id=approval.id,
                approval_event_type=approval.type,
            ))
        return out

    def tasks_from_index(self, pdd_id: str, task_ids: list[str]) -> list[CandidateTask]:
        pdd_id = self._validate_id(pdd_id)
        index = self._task_index()
        creation_order = self._task_creation_order()
        task_map_order = self._task_map_order(pdd_id)
        ordered_ids = self._ordered_task_ids(
            task_ids,
            task_map_order=task_map_order,
            creation_order=creation_order,
        )
        out: list[CandidateTask] = []
        for task_id in ordered_ids:
            entry = index.get(task_id)
            if not isinstance(entry, dict):
                continue
            entry_pdd = str(entry.get("pdd_id") or entry.get("feature_id") or "")
            if entry_pdd and entry_pdd != pdd_id:
                continue
            task_ref = str(entry.get("task_ref") or "")
            source_commit = str(entry.get("source_commit") or "")
            if not task_ref or not source_commit:
                continue
            out.append(CandidateTask(
                task_id=task_id,
                task_ref=task_ref,
                source_commit=source_commit,
                approval_event_id=str(entry.get("trigger_event_id") or ""),
                approval_event_type="task.ref.updated",
            ))
        return out

    def _with_dependency_task_refs(
        self,
        pdd_id: str,
        tasks: list[CandidateTask],
    ) -> list[CandidateTask]:
        """Restore accepted task refs that are Git ancestors of this batch.

        A resumed/replanned run can rotate its active event ledger while the
        canonical task-ref index and writer branch ancestry remain intact. In
        that case ``approved_tasks`` sees only the residual generation. A
        candidate built from those leaf refs alone loses previously accepted
        scaffold/core files because per-task scope filtering intentionally
        excludes unrelated ancestor patches.

        Only refs accepted by ``TaskRefManager`` for the same PDD are eligible.
        Arbitrary worker-branch ancestors therefore remain excluded.
        """
        if not tasks:
            return tasks
        requested_ids = {task.task_id for task in tasks}
        descendants = [task.source_commit for task in tasks]
        dependencies: list[CandidateTask] = []
        for task_id, entry in self._task_index().items():
            if task_id in requested_ids or not isinstance(entry, dict):
                continue
            entry_pdd = str(entry.get("pdd_id") or entry.get("feature_id") or "")
            if entry_pdd != pdd_id:
                continue
            task_ref = str(entry.get("task_ref") or "").strip()
            source_commit = str(entry.get("source_commit") or "").strip()
            if not task_ref or not source_commit:
                continue
            if not any(
                source_commit != descendant
                and self._git_is_ancestor(source_commit, descendant)
                for descendant in descendants
            ):
                continue
            dependencies.append(CandidateTask(
                task_id=task_id,
                task_ref=task_ref,
                source_commit=source_commit,
                approval_event_id=str(entry.get("trigger_event_id") or ""),
                approval_event_type="task.ref.dependency",
            ))

        combined = [*dependencies, *tasks]
        return sorted(combined, key=self._commit_ancestry_size)

    def _git_is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        return result.returncode == 0

    def _commit_ancestry_size(self, task: CandidateTask) -> int:
        result = subprocess.run(
            ["git", "rev-list", "--count", task.source_commit],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        try:
            return int(result.stdout.strip()) if result.returncode == 0 else 0
        except ValueError:
            return 0

    def _sync_tasks_with_latest_completions(
        self,
        tasks: list[CandidateTask],
        *,
        event_writer: EventWriter | None = None,
    ) -> list[CandidateTask]:
        """U1(缝上传值/顺序绑定):集成前把 task ref 与最新有效完成对齐。

        ref 链挂 run_once wake 路径,滞后 fanout 补扫分钟级;集成直接读
        索引会永远慢一拍(r6.1 断点续跑实弹:空 diff 3 轮 + 慢一拍 2 轮,
        含触发停机的第 12 轮伪拒)。此处以事件流中该 task 最新的 worker
        完成事件为准,同步驱动 TaskRefManager——复用其全部握手校验
        (分支头一致/脏工作区),不绕过任何检查;wake 路径事后重复处理
        为幂等。kernel 回声(actor=zf-cli)携带 manifest 旧值,不作数。
        """
        if not tasks:
            return tasks
        latest: dict[str, ZfEvent] = {}
        for event in self.event_log.read_all():
            if event.type != "dev.build.done" or not event.task_id:
                continue
            if str(event.actor or "") == "zf-cli":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("source_commit") or "").strip():
                latest[event.task_id] = event
        out: list[CandidateTask] = []
        for task in tasks:
            event = latest.get(task.task_id)
            if event is None:
                out.append(task)
                continue
            commit = str(event.payload.get("source_commit") or "").strip()
            if not commit or commit == task.source_commit:
                out.append(task)
                continue
            try:
                from zf.runtime.task_refs import TaskRefManager

                result = TaskRefManager(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    config=self.config,
                ).process_dev_build_done(event)
            except Exception:
                out.append(task)
                continue
            if result is not None and result.status == "updated":
                synced = str(result.payload.get("source_commit") or commit)
                if event_writer is not None:
                    event_writer.append(ZfEvent(
                        type="task.ref.updated",
                        actor="zf-cli",
                        task_id=task.task_id,
                        payload={
                            **result.payload,
                            "source": "candidate_integration_sync",
                        },
                        causation_id=event.id,
                        correlation_id=event.correlation_id,
                    ))
                out.append(replace(task, source_commit=synced))
            else:
                out.append(task)
        return out

    def pdd_id_for_task(self, task_id: str, event: ZfEvent | None = None) -> str:
        candidates: list[str] = []
        if event is not None and isinstance(event.payload, dict):
            candidates.extend([
                str(event.payload.get("pdd_id") or ""),
                str(event.payload.get("feature_id") or ""),
            ])
        entry = self._task_index().get(task_id, {})
        if isinstance(entry, dict):
            candidates.extend([
                str(entry.get("pdd_id") or ""),
                str(entry.get("feature_id") or ""),
            ])
        task = self.task_store.get(task_id)
        if task is not None and task.key:
            match = _FEATURE_KEY_RE.match(task.key)
            if match:
                candidates.append(match.group(1))
        for historical in self.event_log.read_all():
            if historical.task_id != task_id or not isinstance(historical.payload, dict):
                continue
            candidates.extend([
                str(historical.payload.get("pdd_id") or ""),
                str(historical.payload.get("feature_id") or ""),
            ])
            contract = historical.payload.get("contract")
            if isinstance(contract, dict):
                candidates.extend([
                    str(contract.get("pdd_id") or ""),
                    str(contract.get("feature_id") or ""),
                ])
        for candidate in candidates:
            if candidate:
                return self._validate_id(candidate)
        return "default"

    def _rebuild_locked(
        self,
        *,
        pdd_id: str,
        branch: str,
        base_ref: str,
        strategy: str,
        tasks: list[CandidateTask],
        requested_tasks: list[CandidateTask] | None = None,
        manifest_path: Path,
        event_writer: EventWriter | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> CandidateResult:
        started_at = _now()
        worktree = self._worktree_path(pdd_id)
        requested_task_models = requested_tasks if requested_tasks is not None else tasks
        requested_task_ids = {task.task_id for task in requested_task_models}
        requested_task_payloads = [asdict(task) for task in requested_task_models]
        dependency_task_payloads = [
            asdict(task) for task in tasks if task.task_id not in requested_task_ids
        ]
        manifest_base = {
            "pdd_id": pdd_id,
            "branch": branch,
            "base_ref": base_ref,
            "requested_base_ref": self.config.runtime.git.candidate_base_ref,
            "strategy": strategy,
            "requested_tasks": requested_task_payloads,
            "dependency_tasks": dependency_task_payloads,
            "included_tasks": [asdict(task) for task in tasks],
            "skipped_tasks": [],
            "started_at": started_at,
            "manifest_path": str(manifest_path),
            "merger_worktree": str(worktree),
        }
        try:
            self._require_strategy(strategy)
            stale = self._stale_task_refs(tasks)
            if stale:
                payload = {
                    **manifest_base,
                    "status": "stale",
                    "reason": "stale_task_ref",
                    "stale_tasks": stale,
                    "updated_at": _now(),
                }
                self._write_manifest(manifest_path, payload)
                return CandidateResult(
                    status="stale",
                    event_type="candidate.stale",
                    payload=payload,
                )
            base_commit = self._git(self.project_root, "rev-parse", base_ref)
            overlap = self._changed_file_overlap_conflict(tasks)
            if overlap is not None:
                payload = {
                    **manifest_base,
                    "status": "conflict",
                    "base_commit": base_commit,
                    "task_ref": overlap["task_ref"],
                    "task_id": overlap["task_id"],
                    "conflicting_task_id": overlap["conflicting_task_id"],
                    "conflicting_task_ref": overlap["conflicting_task_ref"],
                    "conflict_files": overlap["conflict_files"],
                    "changed_files_by_task": overlap["changed_files_by_task"],
                    "error": (
                        "overlapping git-derived changed_files between candidate "
                        "tasks"
                    ),
                    "updated_at": _now(),
                }
                self._write_manifest(manifest_path, payload)
                return CandidateResult(
                    status="conflict",
                    event_type="candidate.conflict",
                    payload=payload,
                )
            self._prepare_worktree(pdd_id, base_ref)
            included_tasks: list[dict[str, Any]] = []
            skipped_tasks: list[dict[str, Any]] = []
            manifest_base["included_tasks"] = included_tasks
            manifest_base["skipped_tasks"] = skipped_tasks
            seen_source_commits: set[str] = set()
            for task in tasks:
                declared_files = self._candidate_task_scope_files(base_ref, task)
                task_commits, scope_skipped_commits = self._task_commits(
                    base_ref,
                    task,
                    declared_files=declared_files,
                )
                if not task_commits:
                    skipped_commits = scope_skipped_commits or [task.source_commit]
                    skipped = {
                        **asdict(task),
                        "reason": "base_equivalent_task_ref",
                        "metadata_only": True,
                        "skipped_commits": skipped_commits,
                    }
                    skipped_tasks.append(skipped)
                    seen_source_commits.update(skipped_commits)
                    continue
                included_tasks.append(asdict(task))
                duplicate_skipped_commits = [
                    commit for commit in task_commits if commit in seen_source_commits
                ]
                task_commits = [
                    commit for commit in task_commits if commit not in seen_source_commits
                ]
                applied_commits: list[str] = []
                skipped_commits: list[str] = [
                    *scope_skipped_commits,
                    *duplicate_skipped_commits,
                ]
                try:
                    for commit in task_commits:
                        status = self._apply_task_commit(
                            worktree,
                            commit,
                            declared_files=declared_files,
                        )
                        if status == "applied":
                            applied_commits.append(commit)
                        else:
                            skipped_commits.append(commit)
                except RuntimeError as exc:
                    conflict_files = self._conflict_files(worktree)
                    self._abort_cherry_pick(worktree)
                    payload = {
                        **manifest_base,
                        "status": "conflict",
                        "base_commit": base_commit,
                        "task_ref": task.task_ref,
                        "task_id": task.task_id,
                        "task_commits": task_commits,
                        "applied_commits": applied_commits,
                        "skipped_commits": skipped_commits,
                        "scope_skipped_commits": scope_skipped_commits,
                        "duplicate_skipped_commits": duplicate_skipped_commits,
                        "conflict_files": conflict_files,
                        "error": str(exc),
                        "updated_at": _now(),
                    }
                    self._write_manifest(manifest_path, payload)
                    return CandidateResult(
                        status="conflict",
                        event_type="candidate.conflict",
                        payload=payload,
                    )
                commit_after_task = self._git(worktree, "rev-parse", "HEAD")
                if event_writer is not None:
                    event_writer.append(ZfEvent(
                        type="candidate.task_ref.applied",
                        actor="zf-cli",
                        task_id=task.task_id,
                        payload={
                            "pdd_id": pdd_id,
                            "branch": branch,
                            "task_ref": task.task_ref,
                            "source_commit": task.source_commit,
                            "commit": commit_after_task,
                            "task_commits": task_commits,
                            "applied_commits": applied_commits,
                            "skipped_commits": skipped_commits,
                            "scope_skipped_commits": scope_skipped_commits,
                            "duplicate_skipped_commits": duplicate_skipped_commits,
                            "commit_count": (
                                len(task_commits)
                                + len(scope_skipped_commits)
                                + len(duplicate_skipped_commits)
                            ),
                            "selected_commit_count": len(task_commits),
                            "merger_worktree": str(worktree),
                        },
                        causation_id=causation_id,
                        correlation_id=correlation_id,
                    ))
                seen_source_commits.update(task_commits)
                seen_source_commits.update(scope_skipped_commits)
                seen_source_commits.update(duplicate_skipped_commits)
            materialized_config_refs = (
                self._materialize_stage_criteria_config_refs(worktree)
            )
            commit = self._git(worktree, "rev-parse", "HEAD")
            self._git(self.project_root, "update-ref", f"refs/heads/{branch}", commit)
            closure_payload = self._candidate_closure_check(
                pdd_id=pdd_id,
                worktree=worktree,
                tasks=tasks,
            )
            if closure_payload["status"] == "failed":
                quality_payload = {
                    "status": "failed",
                    "failure": "candidate_required_source_outputs_missing",
                    "checks": [{
                        "name": "candidate_required_source_outputs",
                        "passed": False,
                        "missing": closure_payload["missing_required_source_outputs"],
                        "rework_owner_hint": "assembly",
                    }],
                }
                payload = {
                    **manifest_base,
                    "status": "quality_failed",
                    "base_commit": base_commit,
                    "commit": commit,
                    "quality_status": "failed",
                    "quality": quality_payload,
                    "candidate_closure": closure_payload,
                    "missing_required_source_outputs": closure_payload[
                        "missing_required_source_outputs"
                    ],
                    "materialized_config_refs": materialized_config_refs,
                    "rework_owner_hint": "assembly",
                    "updated_at": _now(),
                }
                self._write_manifest(manifest_path, payload)
                return CandidateResult(
                    status="quality_failed",
                    event_type="candidate.quality.failed",
                    payload=payload,
                )
            candidate_environment = self._prepare_candidate_environment(
                worktree=worktree,
                commit=commit,
            )
            if candidate_environment["status"] == "failed":
                quality_payload = {
                    "status": "failed",
                    "failure": "candidate_environment_setup_failed",
                    "checks": [{
                        "name": "project_setup",
                        "passed": False,
                        "detail": candidate_environment["detail"],
                        "rework_owner_hint": "harness_environment",
                    }],
                    "gates_run": [],
                    "gates_passed": [],
                    "gates_failed": ["candidate_environment"],
                    "gate_checks": {},
                    "failure_details": {
                        "candidate_environment": [candidate_environment["detail"]],
                    },
                }
                payload = {
                    **manifest_base,
                    "status": "quality_failed",
                    "base_commit": base_commit,
                    "commit": commit,
                    "quality_status": "failed",
                    "quality": quality_payload,
                    "candidate_environment": candidate_environment,
                    "materialized_config_refs": materialized_config_refs,
                    "rework_owner_hint": "harness_environment",
                    "updated_at": _now(),
                }
                self._write_manifest(manifest_path, payload)
                return CandidateResult(
                    status="quality_failed",
                    event_type="candidate.quality.failed",
                    payload=payload,
                )
            quality_payload = self._run_quality_gates(
                pdd_id=pdd_id,
                branch=branch,
                base_commit=base_commit,
                commit=commit,
                worktree=worktree,
                tasks=tasks,
                event_writer=event_writer,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            repair = self._candidate_diff_autofix(
                pdd_id=pdd_id,
                branch=branch,
                base_commit=base_commit,
                commit=commit,
                worktree=worktree,
                tasks=tasks,
                quality_payload=quality_payload,
                event_writer=event_writer,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            if repair and repair.get("status") == "applied":
                commit = str(repair.get("commit") or commit)
                self._git(self.project_root, "update-ref", f"refs/heads/{branch}", commit)
                quality_payload = self._run_quality_gates(
                    pdd_id=pdd_id,
                    branch=branch,
                    base_commit=base_commit,
                    commit=commit,
                    worktree=worktree,
                    tasks=tasks,
                    event_writer=event_writer,
                    causation_id=causation_id,
                    correlation_id=correlation_id,
                )
                quality_payload["mechanical_repairs"] = [repair]
            if quality_payload["status"] == "failed":
                payload = {
                    **manifest_base,
                    "status": "quality_failed",
                    "base_commit": base_commit,
                    "commit": commit,
                    "quality_status": "failed",
                    "quality": quality_payload,
                    "candidate_environment": candidate_environment,
                    "materialized_config_refs": materialized_config_refs,
                    "updated_at": _now(),
                }
                self._write_manifest(manifest_path, payload)
                return CandidateResult(
                    status="quality_failed",
                    event_type="candidate.quality.failed",
                    payload=payload,
                )
            payload = {
                **manifest_base,
                "status": "updated",
                "base_commit": base_commit,
                "commit": commit,
                "quality_status": quality_payload["status"],
                "quality": quality_payload,
                "candidate_environment": candidate_environment,
                "materialized_config_refs": materialized_config_refs,
                "updated_at": _now(),
            }
            self._write_manifest(manifest_path, payload)
            return CandidateResult(
                status="updated",
                event_type="candidate.updated",
                payload=payload,
            )
        except RuntimeError as exc:
            payload = {
                **manifest_base,
                "status": "conflict",
                "error": str(exc),
                "updated_at": _now(),
            }
            self._write_manifest(manifest_path, payload)
            return CandidateResult(
                status="conflict",
                event_type="candidate.conflict",
                payload=payload,
            )

    def _run_quality_gates(
        self,
        *,
        pdd_id: str,
        branch: str,
        base_commit: str,
        commit: str,
        worktree: Path,
        tasks: list[CandidateTask],
        event_writer: EventWriter | None,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        checks, gate_source = self._quality_checks(tasks)
        base_payload = {
            "pdd_id": pdd_id,
            "branch": branch,
            "commit": commit,
            "merger_worktree": str(worktree),
            "gate_source": gate_source,
            "gate_count": len({gate for gate, _ in checks}),
            "check_count": len(checks),
        }
        if not checks:
            if gate_source == "task_contract_missing":
                return {
                    **base_payload,
                    "status": "failed",
                    "gates_run": ["task_contract_required"],
                    "gates_passed": [],
                    "gates_failed": ["task_contract_required"],
                    "gate_checks": {},
                    "failure_details": {
                        "task_contract_required": [
                            "one or more candidate tasks have no executable "
                            "contract.verification"
                        ],
                    },
                }
            return {
                **base_payload,
                "status": "skipped",
                "gates_run": [],
                "gates_passed": [],
                "gates_failed": [],
                "gate_checks": {},
                "failure_details": {},
            }

        if event_writer is not None:
            event_writer.append(ZfEvent(
                type="candidate.quality.started",
                actor="zf-cli",
                payload=base_payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            ))

        gates_run: list[str] = []
        gates_passed: list[str] = []
        gates_failed: list[str] = []
        failure_details: dict[str, list[str]] = {}
        gate_checks: dict[str, list[dict[str, Any]]] = {}
        expected_red_checks: list[dict[str, Any]] = []

        by_gate: dict[str, list[str]] = {}
        for gate_name, command in checks:
            by_gate.setdefault(gate_name, []).append(command)

        for gate_name, commands in by_gate.items():
            gates_run.append(gate_name)
            gate_ok = True
            for command in commands:
                started = time.monotonic()
                try:
                    result = subprocess.run(
                        command,
                        shell=True,
                        cwd=worktree,
                        capture_output=True,
                        text=True,
                        timeout=_QUALITY_TIMEOUT_SECONDS,
                        check=False,
                        env=_quality_gate_env(worktree),
                    )
                    evidence = command_evidence(
                        command=command,
                        exit_code=result.returncode,
                        stdout=result.stdout or "",
                        stderr=result.stderr or "",
                    )
                    if result.returncode != 0:
                        expected_red = self._latest_expected_red_quality_evidence(
                            tasks,
                            command=command,
                            returncode=result.returncode,
                        )
                        if expected_red:
                            evidence["status"] = "RED_expected"
                            evidence["expected_red_evidence"] = expected_red
                            expected_red_checks.append({
                                "gate": gate_name,
                                "command": command,
                                "exit_code": result.returncode,
                                "evidence": expected_red,
                            })
                        else:
                            gate_ok = False
                            failure_details.setdefault(gate_name, []).append(command)
                except subprocess.TimeoutExpired as exc:
                    gate_ok = False
                    evidence = command_evidence(
                        command=command,
                        exit_code=None,
                        stdout=exc.stdout or "",
                        stderr=exc.stderr or "",
                        timed_out=True,
                    )
                    failure_details.setdefault(gate_name, []).append(
                        f"{command}: timed out after {_QUALITY_TIMEOUT_SECONDS}s"
                    )
                except Exception as exc:
                    gate_ok = False
                    evidence = command_evidence(
                        command=command,
                        exit_code=None,
                        error=str(exc),
                    )
                    failure_details.setdefault(gate_name, []).append(
                        f"{command}: {exc}"
                    )
                evidence["duration_ms"] = int((time.monotonic() - started) * 1000)
                gate_checks.setdefault(gate_name, []).append(evidence)
            (gates_passed if gate_ok else gates_failed).append(gate_name)

        diff_evidence = self._candidate_diff_check(
            base_commit=base_commit,
            commit=commit,
            worktree=worktree,
        )
        if diff_evidence.get("exit_code") not in (0, "0"):
            gates_failed.append("candidate_diff")
            failure_details.setdefault("candidate_diff", []).append(
                diff_evidence.get("command", "git diff --check <base>..<head>")
            )
            gate_checks.setdefault("candidate_diff", []).append(diff_evidence)

        clean_evidence = self._candidate_worktree_clean_check(worktree)
        if "reportable_status" in clean_evidence:
            dirty_output = str(clean_evidence.get("reportable_status") or "")
        else:
            dirty_output = str(
                clean_evidence.get("stdout") or clean_evidence.get("stdout_tail") or ""
            )
        if clean_evidence.get("exit_code") not in (0, "0") or dirty_output:
            gates_failed.append("candidate_worktree_clean")
            failure_details.setdefault("candidate_worktree_clean", []).append(
                clean_evidence.get("command", "git status --porcelain --untracked-files=all")
            )
            gate_checks.setdefault("candidate_worktree_clean", []).append(clean_evidence)

        status = "failed" if gates_failed else "passed"
        payload = {
            **base_payload,
            "status": status,
            "gates_run": gates_run,
            "gates_passed": gates_passed,
            "gates_failed": gates_failed,
            "gate_checks": gate_checks,
            "intrinsic_checks": {
                "candidate_diff": diff_evidence,
                "candidate_worktree_clean": clean_evidence,
            },
            "expected_red_checks": expected_red_checks,
            "failure_details": failure_details,
        }
        if event_writer is not None and status == "passed":
            event_writer.append(ZfEvent(
                type="candidate.quality.passed",
                actor="zf-cli",
                payload=payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            ))
        return payload

    def _materialize_stage_criteria_config_refs(self, worktree: Path) -> list[dict[str, str]]:
        """Copy local workflow gate configs into the candidate checkout.

        Reader/judge workers inspect the candidate branch, not necessarily the
        source checkout that owns ``zf.yaml``. Literal relative ``config_ref``
        files used by stage success criteria are therefore part of the
        candidate evaluation closure. Gate configs may also declare governance
        artifacts that are produced by scan/plan workers rather than by code
        slices; copy only those artifacts when they live in an explicit
        candidate-governance bundle under runtime artifacts.
        """
        refs = self._stage_criteria_config_refs()
        materialized: list[dict[str, str]] = []
        if not refs:
            return materialized
        project_root = self.project_root.resolve()
        changed: list[str] = []
        for ref in refs:
            source = (self.project_root / ref).resolve()
            try:
                source.relative_to(project_root)
            except ValueError:
                continue
            if not source.is_file():
                continue
            target = _safe_worktree_path(worktree, ref)
            if target is None:
                continue
            if _copy_materialized_candidate_path(source=source, target=target):
                changed.append(ref)
                materialized.append({
                    "path": ref,
                    "source": str(source),
                    "reason": "workflow_stage_criteria_config_ref",
                })
            for artifact_ref, artifact_source in (
                self._stage_gate_candidate_governance_sources(source)
            ):
                artifact_target = _safe_worktree_path(worktree, artifact_ref)
                if artifact_target is None:
                    continue
                if not _copy_materialized_candidate_path(
                    source=artifact_source,
                    target=artifact_target,
                ):
                    continue
                changed.append(artifact_ref)
                materialized.append({
                    "path": artifact_ref,
                    "source": str(artifact_source),
                    "reason": "workflow_stage_criteria_required_artifact",
                })
        if not changed:
            return materialized
        self._git(worktree, "add", "--", *changed)
        if not self._git_has_staged_changes(worktree):
            return materialized
        self._git(
            worktree,
            "commit",
            "-q",
            "-m",
            "chore: include workflow stage gate configs",
        )
        return materialized

    def _stage_criteria_config_refs(self) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        for stage in self.config.workflow.stages:
            for criterion in stage.criteria.success_criteria:
                if not isinstance(criterion, dict):
                    continue
                ref = str(
                    criterion.get("config_ref")
                    or criterion.get("gate_config_ref")
                    or ""
                ).strip()
                if not ref or _dynamic_or_external_ref(ref):
                    continue
                if ref in seen:
                    continue
                seen.add(ref)
                refs.append(ref)
        return refs

    def _stage_gate_candidate_governance_sources(
        self,
        config_path: Path,
    ) -> list[tuple[str, Path]]:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(config, dict):
            return []
        artifacts = config.get("required_artifacts")
        if not isinstance(artifacts, list):
            return []
        out: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for item in artifacts:
            ref = str(item or "").strip().rstrip("/")
            if not ref or ref in seen or _dynamic_or_external_ref(ref):
                continue
            source = self._candidate_governance_source_for_ref(ref)
            if source is None:
                continue
            seen.add(ref)
            out.append((ref, source))
        return out

    def _candidate_governance_source_for_ref(self, ref: str) -> Path | None:
        relative = Path(ref)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            return None
        root = self.state_dir / "artifacts"
        if not root.exists():
            return None
        candidates: list[Path] = []
        for bundle in root.glob("*/candidate-governance"):
            source = bundle.joinpath(relative)
            if source.exists():
                candidates.append(source)
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _git_has_staged_changes(self, worktree: Path) -> bool:
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        return result.returncode == 1

    def _candidate_worktree_clean_check(self, worktree: Path) -> dict[str, Any]:
        command = "git status --porcelain --untracked-files=all"
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=_QUALITY_TIMEOUT_SECONDS,
                check=False,
                env=_quality_gate_env(worktree),
            )
            evidence = command_evidence(
                command=command,
                exit_code=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
            )
            evidence["reportable_status"] = _candidate_reportable_status(
                result.stdout or ""
            )
        except subprocess.TimeoutExpired as exc:
            evidence = command_evidence(
                command=command,
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                timed_out=True,
            )
            evidence["reportable_status"] = str(exc.stdout or "")
        except Exception as exc:
            evidence = command_evidence(
                command=command,
                exit_code=None,
                error=str(exc),
            )
            evidence["reportable_status"] = ""
        evidence["duration_ms"] = int((time.monotonic() - started) * 1000)
        return evidence

    def _latest_expected_red_quality_evidence(
        self,
        tasks: list[CandidateTask],
        *,
        command: str,
        returncode: int,
    ) -> dict[str, Any]:
        if returncode == 0:
            return {}
        command = str(command or "").strip()
        if not command:
            return {}
        task_ids = {task.task_id for task in tasks}
        if not task_ids:
            return {}
        for event in reversed(self.event_log.read_all()):
            if event.task_id not in task_ids:
                continue
            if event.type not in _TERMINAL_CANDIDATES:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            for check in _iter_gate_checks(payload):
                if str(check.get("status") or "") != "RED_expected":
                    continue
                try:
                    exit_code = int(check.get("exit_code"))
                except (TypeError, ValueError):
                    continue
                if exit_code != returncode:
                    continue
                check_command = str(check.get("command") or "").strip()
                if check_command != command:
                    continue
                return {
                    "task_id": event.task_id,
                    "source_event_id": event.id,
                    "source_event_type": event.type,
                    "check": check,
                }
        return {}

    def _candidate_diff_check(
        self,
        *,
        base_commit: str,
        commit: str,
        worktree: Path,
    ) -> dict[str, Any]:
        command = f"git diff --check {base_commit}..{commit}"
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["git", "diff", "--check", f"{base_commit}..{commit}"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=_QUALITY_TIMEOUT_SECONDS,
                check=False,
                env=_quality_gate_env(worktree),
            )
            evidence = command_evidence(
                command=command,
                exit_code=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            evidence = command_evidence(
                command=command,
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                timed_out=True,
            )
        except Exception as exc:
            evidence = command_evidence(
                command=command,
                exit_code=None,
                error=str(exc),
            )
        evidence["duration_ms"] = int((time.monotonic() - started) * 1000)
        evidence["kind"] = "candidate_diff_whitespace"
        return evidence

    def _candidate_diff_autofix(
        self,
        *,
        pdd_id: str,
        branch: str,
        base_commit: str,
        commit: str,
        worktree: Path,
        tasks: list[CandidateTask],
        quality_payload: dict[str, Any],
        event_writer: EventWriter | None,
        causation_id: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any] | None:
        diff_evidence = _quality_candidate_diff_evidence(quality_payload)
        if not diff_evidence or diff_evidence.get("exit_code") in (0, "0"):
            return None
        issues = _parse_diff_check_issues(diff_evidence)
        if not issues:
            return None
        unsupported = [
            issue for issue in issues
            if issue["message"] not in _AUTOFIXABLE_DIFF_MESSAGES
        ]
        if unsupported:
            return None
        changed_files = set(self._git(
            worktree,
            "diff",
            "--name-only",
            f"{base_commit}..{commit}",
        ).splitlines())
        issue_paths = {str(issue["path"]) for issue in issues}
        if not issue_paths or any(path not in changed_files for path in issue_paths):
            return None
        declared_scope: set[str] = set()
        for task in tasks:
            declared_scope.update(self._declared_task_files(task.task_id))
        if declared_scope and any(
            not _paths_overlap({path}, declared_scope) for path in issue_paths
        ):
            return None

        repaired_paths: list[str] = []
        for path in sorted(issue_paths):
            target = _safe_worktree_path(worktree, path)
            if target is None or not target.is_file():
                return None
            if _repair_diff_check_text_file(target):
                repaired_paths.append(path)
        if not repaired_paths:
            return None

        self._git(worktree, "add", "--", *repaired_paths)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", *repaired_paths],
            cwd=worktree,
            check=False,
            env=git_env(),
        )
        if staged.returncode == 0:
            return None
        self._git(worktree, "commit", "-q", "-m", "chore: fix candidate whitespace")
        fixed_commit = self._git(worktree, "rev-parse", "HEAD")
        post_check = self._candidate_diff_check(
            base_commit=base_commit,
            commit=fixed_commit,
            worktree=worktree,
        )
        payload = {
            "pdd_id": pdd_id,
            "branch": branch,
            "base_commit": base_commit,
            "previous_commit": commit,
            "commit": fixed_commit,
            "kind": "candidate_diff_whitespace",
            "status": (
                "applied"
                if post_check.get("exit_code") in (0, "0")
                else "failed"
            ),
            "repaired_paths": repaired_paths,
            "issue_count": len(issues),
            "precheck": diff_evidence,
            "postcheck": post_check,
            "merger_worktree": str(worktree),
        }
        if event_writer is not None:
            event_writer.append(ZfEvent(
                type="candidate.mechanical_fix.applied",
                actor="zf-cli",
                payload=payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            ))
        return payload

    def _quality_checks(
        self,
        tasks: list[CandidateTask],
    ) -> tuple[list[tuple[str, str]], str]:
        """Resolve project checks from this run before legacy config fallback.

        Task contracts are plan-owned, run-scoped project facts. The config
        remains a compatibility fallback for legacy/manual flows that do not
        produce verification contracts; it must not impose one technology
        stack on every generated project.
        """

        quality_source = str(getattr(
            getattr(self.config, "workflow", None),
            "candidate_quality_source",
            "auto",
        ) or "auto")
        checks: list[tuple[str, str]] = []
        seen_commands: set[tuple[str, str]] = set()
        missing_task_ids: list[str] = []
        for candidate_task in tasks:
            task = self.task_store.get(candidate_task.task_id)
            commands = (
                task_contract_verification_commands(task.contract)
                if task is not None
                else []
            )
            if not commands:
                missing_task_ids.append(candidate_task.task_id)
                continue
            for command in commands:
                identity = (str(command["id"]), str(command["command_digest"]))
                if identity in seen_commands:
                    continue
                seen_commands.add(identity)
                checks.append((
                    f"task_contract:{candidate_task.task_id}:{command['id']}",
                    str(command["command"]),
                ))
        if quality_source == "task_contract_required" and missing_task_ids:
            return [], "task_contract_missing"
        if checks:
            return checks, "task_contract"

        if quality_source == "task_contract_required":
            return [], "task_contract_missing"

        checks: list[tuple[str, str]] = []
        for gate_name, gate_cfg in self.config.quality_gates.items():
            if not getattr(gate_cfg, "enabled", True):
                continue
            for command in getattr(gate_cfg, "required_checks", []) or []:
                command_text = str(command).strip()
                if command_text:
                    checks.append((gate_name, command_text))
        return checks, "zf_config_fallback" if checks else "intrinsic_only"

    def _prepare_candidate_environment(
        self,
        *,
        worktree: Path,
        commit: str,
    ) -> dict[str, Any]:
        script = str(getattr(self.config.project, "setup_script", "") or "")
        result = run_project_setup(
            worktree,
            script,
            marker_dir=worktree.parent,
            force=True,
        )
        return {
            "schema_version": "candidate-environment.v1",
            "status": "ready" if result.ok else "failed",
            "candidate_commit": commit,
            "setup_declared": bool(script.strip()),
            "setup_script_digest": (
                hashlib.sha256(script.encode("utf-8")).hexdigest()
                if script.strip() else ""
            ),
            "setup_ran": result.ran,
            "exit_code": result.exit_code,
            "detail": result.detail,
        }


    def _prepare_worktree(self, pdd_id: str, base_ref: str) -> None:
        worktree = self._worktree_path(pdd_id)
        if worktree.exists():
            if not (worktree / ".git").exists():
                raise RuntimeError(
                    f"candidate worktree exists but is not a git worktree: {worktree}"
                )
            self._abort_cherry_pick(worktree)
            self._git(worktree, "reset", "--hard", "HEAD")
            self._git(worktree, "clean", "-fd")
            self._git(worktree, "checkout", "--detach", base_ref)
        else:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            self._git(
                self.project_root,
                "worktree",
                "add",
                "--detach",
                str(worktree),
                base_ref,
            )
        self._git(worktree, "reset", "--hard", base_ref)
        self._git(worktree, "clean", "-fd")
        provision_worktree_env(
            worktree,
            self.project_root,
            self.config.runtime.workdirs.provision_paths,
        )

    def _task_index(self) -> dict[str, dict]:
        path = self.state_dir / "refs" / "task-index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _stale_task_refs(self, tasks: list[CandidateTask]) -> list[dict[str, str]]:
        index = self._task_index()
        stale: list[dict[str, str]] = []
        for task in tasks:
            entry = index.get(task.task_id)
            if not isinstance(entry, dict):
                stale.append({
                    "task_id": task.task_id,
                    "reason": "missing_task_index_entry",
                    "candidate_source_commit": task.source_commit,
                    "candidate_task_ref": task.task_ref,
                })
                continue
            current_commit = str(entry.get("source_commit") or "")
            current_ref = str(entry.get("task_ref") or "")
            if (
                current_commit != task.source_commit
                or current_ref != task.task_ref
            ):
                stale.append({
                    "task_id": task.task_id,
                    "reason": "task_index_mismatch",
                    "candidate_source_commit": task.source_commit,
                    "current_source_commit": current_commit,
                    "candidate_task_ref": task.task_ref,
                    "current_task_ref": current_ref,
                })
        return stale

    def _task_creation_order(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for event in self.event_log.read_all():
            if event.type == "task.created" and event.task_id and event.task_id not in seen:
                seen.add(event.task_id)
                out.append(event.task_id)
        for task in sorted(
            self.task_store.list_all_with_archive(),
            key=lambda task: (task.created_at, task.id),
        ):
            if task.id not in seen:
                seen.add(task.id)
                out.append(task.id)
        return out

    def _task_map_order(self, pdd_id: str) -> list[str]:
        path = self.state_dir / "artifacts" / pdd_id / "task_map.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return _extract_task_ids(data)

    @staticmethod
    def _ordered_task_ids(
        task_ids: object,
        *,
        task_map_order: list[str],
        creation_order: list[str],
    ) -> list[str]:
        approved = {str(task_id) for task_id in task_ids}
        out: list[str] = []
        for source in (task_map_order, creation_order, sorted(approved)):
            for task_id in source:
                if task_id in approved and task_id not in out:
                    out.append(task_id)
        return out

    def _worktree_path(self, pdd_id: str) -> Path:
        return self.state_dir / "candidates" / pdd_id / "worktree"

    def _manifest_path(self, pdd_id: str) -> Path:
        return self.state_dir / "candidates" / pdd_id / "manifest.json"

    def _read_manifest(self, pdd_id: str) -> dict[str, Any]:
        try:
            data = json.loads(self._manifest_path(pdd_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_manifest(path: Path, manifest: dict) -> None:
        atomic_write_text(
            path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )

    @staticmethod
    def _require_strategy(strategy: str) -> None:
        if strategy != "cherry-pick":
            raise RuntimeError(f"unsupported candidate strategy: {strategy}")

    def _task_commits(
        self,
        base_ref: str,
        task: CandidateTask,
        *,
        declared_files: set[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        # FIX-10(bizsim r4 F10):--cherry-pick 按 patch-id 排除 base 侧已含
        # 等价补丁的提交。增量 base(旧 candidate)里是 cherry-pick 拷贝、
        # hash 不同,朴素 base..ref 会把同一补丁再集成一遍——r4 churn 期
        # candidate 树即因重复系列而 typecheck 断裂。
        out = self._git(
            self.project_root,
            "rev-list",
            "--reverse",
            "--cherry-pick",
            "--right-only",
            "--no-merges",
            f"{base_ref}...refs/heads/{task.task_ref}",
        )
        commits = [line.strip() for line in out.splitlines() if line.strip()]
        if not commits:
            resolved = self._git(
                self.project_root,
                "rev-parse",
                f"refs/heads/{task.task_ref}",
            )
            return [], [resolved]
        # E5(prd-goal e2e finding-13):子集重建以旧 candidate 为基,
        # task 分支补丁(cherry-pick 后 SHA 不同)对 rev-list 永远是
        # "新提交" → 同批修复被重复堆叠 ×3。按 patch-id(git cherry)
        # 过滤 base 已含的等价补丁。
        try:
            cherry_out = self._git(
                self.project_root,
                "cherry",
                base_ref,
                f"refs/heads/{task.task_ref}",
            )
            equivalent = {
                line.split()[1]
                for line in cherry_out.splitlines()
                if line.startswith("-") and len(line.split()) > 1
            }
        except Exception:
            equivalent = set()
        already_in_base: list[str] = []
        if equivalent:
            already_in_base = [c for c in commits if c in equivalent]
            commits = [c for c in commits if c not in equivalent]
            if not commits:
                return [], already_in_base
        if declared_files is None:
            declared_files = self._declared_task_files(task.task_id)
        if not declared_files:
            return commits, already_in_base
        selected: list[str] = []
        skipped: list[str] = list(already_in_base)
        for commit in commits:
            commit_files = self._commit_files(commit)
            if not commit_files or _paths_overlap(commit_files, declared_files):
                selected.append(commit)
            else:
                skipped.append(commit)
        if not selected:
            return commits, already_in_base
        return selected, skipped

    def _candidate_task_scope_files(
        self,
        base_ref: str,
        task: CandidateTask,
    ) -> set[str]:
        declared_files = self._declared_task_files(task.task_id)
        if not declared_files:
            return set()
        out = self._git(
            self.project_root,
            "rev-list",
            "--reverse",
            f"{base_ref}..refs/heads/{task.task_ref}",
        )
        commits = [line.strip() for line in out.splitlines() if line.strip()]
        if not commits:
            return declared_files
        commit_files = [self._commit_files(commit) for commit in commits]
        detected_package_roots = {
            str(Path(path).parent)
            for files in commit_files
            for path in files
            if Path(path).name == "package.json" and str(Path(path).parent) != "."
        }
        present_package_roots = {
            root
            for root in detected_package_roots
            if self._git_path_exists(
                task.source_commit,
                f"{root}/package.json",
            )
        }
        return _expand_package_scope_closure(
            declared_files,
            commit_files,
            package_roots=present_package_roots,
        )

    def _git_path_exists(self, commit: str, path: str) -> bool:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}:{path}"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        return result.returncode == 0

    def _candidate_closure_check(
        self,
        *,
        pdd_id: str,
        worktree: Path,
        tasks: list[CandidateTask],
    ) -> dict[str, Any]:
        required = self._required_source_outputs_by_task(pdd_id, tasks)
        missing: list[dict[str, str]] = []
        present: list[dict[str, str]] = []
        for task in tasks:
            for item in required.get(task.task_id, []):
                path = str(item.get("path") or "").strip().rstrip("/")
                if not path:
                    continue
                target = _safe_worktree_path(worktree, path)
                if target is not None and target.exists():
                    present.append(item)
                    continue
                missing.append({
                    **item,
                    "reason": (
                        "invalid_candidate_path"
                        if target is None
                        else "missing_from_candidate_worktree"
                    ),
                    "rework_owner_hint": "assembly",
                })
        status = "failed" if missing else "passed"
        return {
            "status": status,
            "required_source_outputs": [
                item
                for task in tasks
                for item in required.get(task.task_id, [])
            ],
            "present_required_source_outputs": present,
            "missing_required_source_outputs": missing,
            "rework_owner_hint": "assembly" if missing else "",
        }

    def _required_source_outputs_by_task(
        self,
        pdd_id: str,
        tasks: list[CandidateTask],
    ) -> dict[str, list[dict[str, str]]]:
        task_map_entries = self._task_map_entries(pdd_id)
        index = self._task_index()
        out: dict[str, list[dict[str, str]]] = {}
        seen: dict[str, set[str]] = {}

        def add(task_id: str, path: str, source: str) -> None:
            normalized = path.strip().rstrip("/")
            if not normalized or _dynamic_or_external_ref(normalized):
                return
            if normalized.startswith(".zf/"):
                return
            bucket = seen.setdefault(task_id, set())
            if normalized in bucket:
                return
            bucket.add(normalized)
            out.setdefault(task_id, []).append({
                "task_id": task_id,
                "path": normalized,
                "source": source,
            })

        task_ids = {task.task_id for task in tasks}
        for task in tasks:
            entry = index.get(task.task_id)
            if isinstance(entry, dict):
                for path in _required_source_outputs_from_payload(entry):
                    add(task.task_id, path, "task_index")
            task_map_entry = task_map_entries.get(task.task_id)
            if isinstance(task_map_entry, dict):
                for path in _required_source_outputs_from_payload(task_map_entry):
                    add(task.task_id, path, "task_map")
        for event in self.event_log.read_all():
            if event.task_id not in task_ids or not isinstance(event.payload, dict):
                continue
            for path in _required_source_outputs_from_payload(event.payload):
                add(event.task_id, path, event.type)
        return out

    def _task_map_entries(self, pdd_id: str) -> dict[str, dict[str, Any]]:
        path = self.state_dir / "artifacts" / pdd_id / "task_map.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        entries: list[Any] = []
        if isinstance(data, dict):
            raw_tasks = data.get("tasks")
            if isinstance(raw_tasks, list):
                entries.extend(raw_tasks)
            elif isinstance(raw_tasks, dict):
                entries.extend(raw_tasks.values())
            for key in ("task_map", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    entries.extend(value)
        elif isinstance(data, list):
            entries.extend(data)
        out: dict[str, dict[str, Any]] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            task_id = str(
                item.get("task_id")
                or item.get("id")
                or item.get("key")
                or ""
            ).strip()
            if task_id:
                out[task_id] = item
        return out

    def _declared_task_files(self, task_id: str) -> set[str]:
        entry = self._task_index().get(task_id, {})
        if not isinstance(entry, dict):
            entry = {}
        index_files = _declared_files_from_payload(entry)
        if index_files:
            return index_files
        accepted_event_id = str(entry.get("trigger_event_id") or "")
        accepted_source_commit = str(entry.get("source_commit") or "")
        exact_files: set[str] = set()
        source_files: set[str] = set()
        fallback_files: set[str] = set()
        saw_source_scoped_event = False
        for event in self.event_log.read_all():
            if event.task_id != task_id or not isinstance(event.payload, dict):
                continue
            if event.type == "task.ref.rejected":
                continue
            paths = _declared_files_from_payload(event.payload)
            if not paths:
                continue
            source_commit = str(event.payload.get("source_commit") or "")
            if accepted_event_id and event.id == accepted_event_id:
                exact_files.update(paths)
            elif accepted_source_commit and source_commit == accepted_source_commit:
                source_files.update(paths)
            elif source_commit:
                saw_source_scoped_event = True
            else:
                fallback_files.update(paths)
        if exact_files:
            return exact_files
        if source_files:
            return source_files
        if accepted_source_commit and saw_source_scoped_event:
            return set()
        return fallback_files

    def _changed_file_overlap_conflict(
        self,
        tasks: list[CandidateTask],
    ) -> dict[str, Any] | None:
        """Detect cross-task write overlap before relying on cherry-pick.

        Git can auto-apply two same-path commits when the textual patch is
        mechanically replayable. Writer fanout still treats two task refs
        claiming the same changed path as a plan decomposition conflict, so the
        task-index git-derived changed_files are the deterministic source here.
        """

        owners: list[tuple[str, str, str]] = []
        files_by_task: dict[str, list[str]] = {}
        for task in tasks:
            files = sorted(
                {
                    path.strip().rstrip("/")
                    for path in self._declared_task_files(task.task_id)
                    if path.strip().rstrip("/")
                }
            )
            if not files:
                continue
            files_by_task[task.task_id] = files
            for path in files:
                for owner_task_id, owner_task_ref, owner_path in owners:
                    if not _paths_overlap({path}, {owner_path}):
                        continue
                    return {
                        "task_id": task.task_id,
                        "task_ref": task.task_ref,
                        "conflicting_task_id": owner_task_id,
                        "conflicting_task_ref": owner_task_ref,
                        "conflict_files": sorted({owner_path, path}),
                        "changed_files_by_task": {
                            owner_task_id: files_by_task.get(
                                owner_task_id,
                                [owner_path],
                            ),
                            task.task_id: files,
                        },
                    }
                owners.append((task.task_id, task.task_ref, path))
        return None

    def _commit_files(self, commit: str) -> set[str]:
        out = self._git(
            self.project_root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "--root",
            commit,
        )
        return {line.strip() for line in out.splitlines() if line.strip()}

    def _apply_task_commit(
        self,
        worktree: Path,
        commit: str,
        *,
        declared_files: set[str],
    ) -> str:
        if declared_files:
            paths = _matching_commit_paths(self._commit_files(commit), declared_files)
            return self._apply_scoped_commit(worktree, commit, paths)
        return self._cherry_pick_commit(worktree, commit)

    def _apply_scoped_commit(
        self,
        worktree: Path,
        commit: str,
        paths: list[str],
    ) -> str:
        if not paths:
            return "skipped"
        existing_paths = self._paths_existing_at_commit(commit, paths)
        deleted_paths = [path for path in paths if path not in existing_paths]
        if existing_paths:
            result = subprocess.run(
                ["git", "checkout", commit, "--", *existing_paths],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = "\n".join(
                    part.strip()
                    for part in (result.stderr, result.stdout)
                    if part.strip()
                )
                raise RuntimeError(
                    f"git checkout {commit} -- scoped paths failed in {worktree}: {detail}"
                )
        if deleted_paths:
            result = subprocess.run(
                ["git", "rm", "-q", "--ignore-unmatch", "--", *deleted_paths],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = "\n".join(
                    part.strip()
                    for part in (result.stderr, result.stdout)
                    if part.strip()
                )
                raise RuntimeError(
                    f"git rm -- scoped paths failed in {worktree}: {detail}"
                )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", *paths],
            cwd=worktree,
            check=False,
        )
        if staged.returncode == 0:
            return "skipped"
        message = self._git(self.project_root, "log", "-1", "--format=%s", commit)
        result = subprocess.run(
            ["git", "commit", "-q", "-m", message],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(allow_large_commit=True),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"git commit -q -m {message} failed in {worktree}: {detail}"
            )
        return "applied"

    def _paths_existing_at_commit(self, commit: str, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        out = self._git(
            self.project_root,
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            *paths,
        )
        return {line.strip() for line in out.splitlines() if line.strip()}

    def _cherry_pick_commit(self, worktree: Path, commit: str) -> str:
        result = subprocess.run(
            ["git", "cherry-pick", commit],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(allow_large_commit=True),
        )
        if result.returncode == 0:
            return "applied"
        detail = "\n".join(
            part.strip()
            for part in (result.stderr, result.stdout)
            if part.strip()
        )
        if self._is_empty_cherry_pick(worktree, detail):
            self._git(worktree, "cherry-pick", "--skip")
            return "skipped"
        raise RuntimeError(
            f"git cherry-pick {commit} failed in {worktree}: {detail}"
        )

    def _is_empty_cherry_pick(self, worktree: Path, detail: str) -> bool:
        if "previous cherry-pick is now empty" in detail:
            return True
        if "nothing to commit" not in detail:
            return False
        head = worktree / ".git" / "CHERRY_PICK_HEAD"
        if head.exists():
            return True
        try:
            git_dir = self._git(worktree, "rev-parse", "--git-dir")
        except RuntimeError:
            return False
        return (worktree / git_dir / "CHERRY_PICK_HEAD").exists()

    @staticmethod
    def _validate_id(value: str) -> str:
        if not value or not _SAFE_ID_RE.match(value):
            raise RuntimeError(f"invalid candidate id: {value!r}")
        return value

    def _conflict_files(self, worktree: Path) -> list[str]:
        try:
            out = self._git(worktree, "diff", "--name-only", "--diff-filter=U")
        except RuntimeError:
            return []
        return [line for line in out.splitlines() if line.strip()]

    def _abort_cherry_pick(self, worktree: Path) -> None:
        subprocess.run(
            ["git", "cherry-pick", "--abort"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout.strip()


def _copy_materialized_candidate_path(*, source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    if source.is_file():
        try:
            if target.exists() and target.is_file() and target.read_bytes() == source.read_bytes():
                return False
        except OSError:
            pass
        if target.exists() and not target.is_file():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return True
    if source.is_dir():
        if target.exists() and not target.is_dir():
            return False
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        return True
    return False


def _quality_gate_env(worktree: Path) -> dict[str, str]:
    """Run candidate quality checks against the candidate worktree checkout."""
    env = os.environ.copy()
    project_src = worktree / "src"
    if not project_src.exists():
        env.pop("PYTHONPATH", None)
        env["ZF_PROJECT_ROOT"] = str(worktree)
        return env

    project_src_resolved = project_src.resolve()
    inherited_parts = []
    for part in env.get("PYTHONPATH", "").split(os.pathsep):
        if not part:
            continue
        if Path(part).resolve() == project_src_resolved:
            continue
        inherited_parts.append(part)
    env["PYTHONPATH"] = os.pathsep.join([str(project_src), *inherited_parts])
    env["ZF_PROJECT_ROOT"] = str(worktree)
    return env


def _extract_task_ids(data: object) -> list[str]:
    if isinstance(data, list):
        return [_task_id_from_item(item) for item in data if _task_id_from_item(item)]
    if not isinstance(data, dict):
        return []
    for key in ("tasks", "task_order", "order"):
        value = data.get(key)
        if isinstance(value, list):
            return [
                task_id
                for item in value
                if (task_id := _task_id_from_item(item))
            ]
    return [
        str(key)
        for key in data.keys()
        if isinstance(key, str) and key.startswith("TASK-")
    ]


def _task_id_from_item(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("task_id", "id", "task"):
            value = item.get(key)
            if isinstance(value, str):
                return value
    return ""


def _paths_overlap(commit_files: set[str], declared_files: set[str]) -> bool:
    for commit_file in commit_files:
        commit_path = commit_file.rstrip("/")
        for declared_file in declared_files:
            declared_path = declared_file.rstrip("/")
            if commit_path == declared_path:
                return True
            if commit_path.startswith(f"{declared_path}/"):
                return True
            if declared_path.startswith(f"{commit_path}/"):
                return True
    return False


def _matching_commit_paths(commit_files: set[str], declared_files: set[str]) -> list[str]:
    matches: list[str] = []
    for commit_file in sorted(commit_files):
        commit_path = commit_file.rstrip("/")
        for declared_file in declared_files:
            declared_path = declared_file.rstrip("/")
            if commit_path == declared_path or commit_path.startswith(f"{declared_path}/"):
                matches.append(commit_file)
                break
    return matches


def _expand_package_scope_closure(
    declared_files: set[str],
    commit_file_sets: list[set[str]],
    *,
    package_roots: set[str] | None = None,
) -> set[str]:
    """Keep workspace package roots whole when a scoped task creates packages.

    Task refs are often updated multiple times. The final task-index entry may
    only contain the latest small diff, but the task branch still needs earlier
    package boundary files (package.json, tsconfig, tests) to materialize.
    Expanding to package roots only when those roots are touched by selected
    task commits preserves the existing protection against unrelated stacked
    commits outside package workspaces.
    """

    scope = {path.strip().rstrip("/") for path in declared_files if path.strip()}
    roots = {
        root
        for path in scope
        if (root := _workspace_package_root(path))
    }
    package_roots = package_roots or set()
    roots.update(
        root
        for root in package_roots
        if any(path == root or path.startswith(f"{root}/") for path in scope)
    )
    scope.update(roots)
    changed = True
    while changed:
        changed = False
        for commit_files in commit_file_sets:
            normalized_files = {
                path.strip().rstrip("/") for path in commit_files if path.strip()
            }
            if not normalized_files or not _paths_overlap(normalized_files, scope):
                continue
            for path in normalized_files:
                root = _workspace_package_root(path)
                if root and root not in roots:
                    roots.add(root)
                    scope.add(root)
                    changed = True
    return scope


def _workspace_package_root(path: str) -> str:
    parts = Path(path).parts
    if len(parts) < 2:
        return ""
    if parts[0] not in {"packages", "apps", "services", "libs"}:
        return ""
    return f"{parts[0]}/{parts[1]}"


def _declared_files_from_payload(payload: dict[str, Any]) -> set[str]:
    files: set[str] = set()
    for key in ("changed_files", "files_touched"):
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            path = str(item).strip()
            if path:
                files.add(path)
    return files


def _required_source_outputs_from_payload(payload: dict[str, Any]) -> set[str]:
    outputs: set[str] = set()
    for key in ("required_source_outputs",):
        value = payload.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                path = str(
                    item.get("path")
                    or item.get("file")
                    or item.get("source")
                    or ""
                ).strip()
            else:
                path = str(item).strip()
            if path:
                outputs.add(path)
    return outputs


def _quality_candidate_diff_evidence(
    quality_payload: dict[str, Any],
) -> dict[str, Any]:
    intrinsic = quality_payload.get("intrinsic_checks")
    if isinstance(intrinsic, dict):
        candidate = intrinsic.get("candidate_diff")
        if isinstance(candidate, dict):
            return candidate
    gate_checks = quality_payload.get("gate_checks")
    if isinstance(gate_checks, dict):
        checks = gate_checks.get("candidate_diff")
        if isinstance(checks, list):
            for check in checks:
                if isinstance(check, dict):
                    return check
    return {}


def _candidate_reportable_status(status: str) -> str:
    """Reduce ``git status --porcelain`` to lines that gate candidate cleanliness.

    Parity with ship.py ``_dirty_files`` (B-NEW-13, commit 96ba665): untracked
    files (porcelain ``??``) never affect what ``git merge candidate/<id>``
    ships — they were never committed, so they cannot violate the
    "candidate == verified tree" invariant. Quality-gate commands routinely
    leave untracked byproducts (``npm install`` → ``package-lock.json``,
    build caches, ``.venv``/``node_modules``); gating the candidate clean
    check on them fails legitimate dependency-installing gates even though the
    shipped tree is unaffected. Only tracked modifications/deletions/renames
    (``M``/``A``/``D``/``R``/``C``) count — those change committed content and
    therefore what ships. This mirrors the ship-side guard so the two clean
    checks cannot drift (the drift that let this pass ship.py but fail here).
    """
    lines = [
        line
        for line in str(status or "").splitlines()
        if line.strip() and line[:2] != "??"
    ]
    if not lines:
        return ""
    suffix = "\n" if str(status or "").endswith("\n") else ""
    return "\n".join(lines) + suffix


def _parse_diff_check_issues(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    text = "\n".join(
        str(evidence.get(key) or "")
        for key in ("stdout_tail", "stderr_tail", "stdout", "stderr")
    )
    issues: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("+", "-")):
            continue
        match = _DIFF_CHECK_LINE_RE.match(line)
        if not match:
            return []
        issues.append({
            "path": match.group("path"),
            "line": int(match.group("line")),
            "message": match.group("message").strip(),
        })
    return issues


def _dynamic_or_external_ref(ref: str) -> bool:
    return (
        "://" in ref
        or ref.startswith(("/", "$", "~"))
        or "${" in ref
        or ref.startswith("env:")
        or ref.startswith("git:")
    )


def _safe_worktree_path(worktree: Path, path: str) -> Path | None:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = worktree.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _repair_diff_check_text_file(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    repaired_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        newline = ""
        body = line
        if line.endswith("\r\n"):
            body = line[:-2]
            newline = "\r\n"
        elif line.endswith("\n"):
            body = line[:-1]
            newline = "\n"
        repaired_lines.append(body.rstrip(" \t") + newline)
    repaired = "".join(repaired_lines)
    if repaired.endswith(("\n", "\r\n")):
        stripped = repaired.rstrip(" \t\r\n")
        repaired = f"{stripped}\n" if stripped else ""
    if repaired == text:
        return False
    path.write_text(repaired, encoding="utf-8")
    return True


def _iter_gate_checks(value: Any):
    if isinstance(value, dict):
        if "status" in value and "exit_code" in value:
            yield value
        for child in value.values():
            yield from _iter_gate_checks(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_gate_checks(child)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
