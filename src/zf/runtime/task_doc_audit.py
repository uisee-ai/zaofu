"""Read-only audit for task capsule drift."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_doc import (
    compute_task_capsule_revisions,
    resolve_task_source_refs,
    task_progress_path,
    verify_task_capsule,
)


@dataclass(frozen=True)
class TaskDocFinding:
    task_id: str
    code: str
    severity: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
        }


def audit_task_docs(
    state_dir: Path,
    *,
    project_root: Path | None = None,
    mode: str = "all",
) -> dict[str, Any]:
    root = project_root or state_dir.parent
    findings: list[TaskDocFinding] = []
    try:
        tasks = TaskStore(state_dir / "kanban.json").list_all_with_archive()
    except Exception as exc:
        return {
            "ok": False,
            "task_count": 0,
            "findings": [{
                "task_id": "",
                "code": "kanban_unreadable",
                "severity": "critical",
                "detail": str(exc),
            }],
        }
    for task in tasks:
        if not _include_task(task, mode):
            continue
        findings.extend(_audit_one_task(state_dir, root, task))
    serialized = [finding.to_dict() for finding in findings]
    return {
        "ok": not any(item["severity"] == "critical" for item in serialized),
        "task_count": len(tasks),
        "findings": serialized,
    }


def _audit_one_task(state_dir: Path, project_root: Path, task: Task) -> list[TaskDocFinding]:
    findings: list[TaskDocFinding] = []
    for error in verify_task_capsule(state_dir, task):
        code = error
        severity = "critical" if "missing" in error or "stale" in error else "warning"
        if _is_lazy_backlog_task(task) and "missing" in error:
            code = "capsule_not_materialized_until_dispatch"
            severity = "warning"
        findings.append(TaskDocFinding(
            task_id=task.id,
            code=code,
            severity=severity,
            detail=f"task capsule verification failed: {error}",
        ))
    findings.extend(_source_ref_findings(state_dir, project_root, task))
    findings.extend(_briefing_revision_findings(state_dir, task))
    findings.extend(_progress_projection_findings(state_dir, task))
    return findings


def _source_ref_findings(state_dir: Path, project_root: Path, task: Task) -> list[TaskDocFinding]:
    contract = task.contract
    if contract is None:
        return []
    findings: list[TaskDocFinding] = []
    if str(getattr(contract, "source_mode", "") or "").strip() == "degraded":
        findings.append(TaskDocFinding(
            task_id=task.id,
            code="source_degraded",
            severity="warning",
            detail="task capsule source was materialized without canonical source_index coverage",
        ))
    for resolved in resolve_task_source_refs(
        state_dir=state_dir,
        project_root=project_root,
        task=task,
    ):
        if resolved.get("readable"):
            continue
        findings.append(TaskDocFinding(
            task_id=task.id,
            code="source_missing",
            severity="critical",
            detail=(
                f"source ref is not readable: {resolved.get('original_ref', '')}; "
                f"attempted_paths={resolved.get('attempted_paths', [])}"
            ),
        ))
    return findings


def _briefing_revision_findings(state_dir: Path, task: Task) -> list[TaskDocFinding]:
    briefing_dir = state_dir / "briefings"
    if not briefing_dir.exists():
        return []
    current = compute_task_capsule_revisions(task)
    findings: list[TaskDocFinding] = []
    for path in briefing_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("id") or "") != task.id:
            continue
        task_doc = data.get("task_doc")
        if not isinstance(task_doc, dict):
            continue
        stale_keys = [
            key for key in ("source_revision", "contract_revision", "capsule_revision")
            if str(task_doc.get(key) or "") and str(task_doc.get(key) or "") != current[key]
        ]
        if stale_keys:
            findings.append(TaskDocFinding(
                task_id=task.id,
                code="briefing_stale",
                severity="critical",
                detail=f"{path} has stale revisions: {', '.join(stale_keys)}",
            ))
    return findings


def _progress_projection_findings(state_dir: Path, task: Task) -> list[TaskDocFinding]:
    path = task_progress_path(state_dir, task.id)
    if not path.exists():
        return [TaskDocFinding(
            task_id=task.id,
            code="progress_unprojected_or_stale",
            severity="warning",
            detail="progress.md is missing",
        )]
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return [TaskDocFinding(
            task_id=task.id,
            code="progress_unprojected_or_stale",
            severity="warning",
            detail=f"progress.md unreadable: {exc}",
        )]
    status_line = f"status_hint: `{task.status}`"
    if status_line not in text:
        return [TaskDocFinding(
            task_id=task.id,
            code="progress_unprojected_or_stale",
            severity="warning",
            detail=f"progress.md does not contain current {status_line}",
        )]
    return []


def _include_task(task: Task, mode: str) -> bool:
    normalized = str(mode or "all").strip().lower()
    if normalized in {"", "all"}:
        return True
    if normalized == "active":
        return task.status not in {"backlog", "done", "cancelled"}
    if normalized == "dispatched":
        return bool(task.active_dispatch_id)
    if normalized == "ready":
        return task.status == "backlog" and not task.blocked_by
    return True


def _is_lazy_backlog_task(task: Task) -> bool:
    return (
        task.status == "backlog"
        and not task.active_dispatch_id
    )
