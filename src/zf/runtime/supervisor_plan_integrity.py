"""Read-only Plan Integrity projection for Supervisor Inspection."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


PLAN_INTEGRITY_SCHEMA_VERSION = "plan-integrity.v0"


def build_plan_integrity_projection(
    state_dir: Path,
    *,
    project_root: Path,
    tasks: list[Task] | None = None,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    tasks = tasks if tasks is not None else _read_tasks(state_dir)
    events = events if events is not None else _read_events(state_dir)
    now = now or datetime.now(timezone.utc)
    active = [t for t in tasks if t.status not in {"done", "cancelled"}]
    findings: list[dict[str, Any]] = []
    for task in active:
        refs = task_plan_refs(task)
        if not refs:
            findings.append(_finding(
                "task-missing-plan-ref",
                "warn",
                task,
                "Active task has no plan/spec/design ref",
                "task contract lacks plan_ref/spec_ref/source_backlog_task_id",
            ))
        if task.contract.acceptance_criteria and not task.contract.acceptance_evidence:
            findings.append(_finding(
                "acceptance-without-evidence",
                "warn",
                task,
                "Acceptance criteria have no mapped evidence",
                "acceptance_criteria is set but acceptance_evidence is empty",
            ))
        if _weak_acceptance(task.contract.acceptance):
            findings.append(_finding(
                "weak-acceptance",
                "info",
                task,
                "Acceptance text lacks explicit verify step",
                "acceptance does not contain verify/check evidence wording",
            ))
    findings.extend(_scan_task_docs(project_root))
    return redact_obj({
        "schema_version": PLAN_INTEGRITY_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "project_root": str(project_root),
        "summary": {
            "active_tasks": len(active),
            "findings": len(findings),
            "missing_plan_refs": sum(
                1 for f in findings if f.get("kind") == "task-missing-plan-ref"
            ),
            "weak_acceptance": sum(
                1 for f in findings if f.get("kind") == "weak-acceptance"
            ),
            "doc_acceptance_without_verify": sum(
                1 for f in findings if f.get("kind") == "doc-acceptance-without-verify"
            ),
        },
        "findings": findings[:100],
    })


def task_plan_refs(task: Task) -> list[str]:
    contract = task.contract
    values = [
        contract.plan_ref,
        contract.spec_ref,
        contract.source_backlog_task_id,
        contract.tdd_ref,
        contract.critic_gate_ref,
    ]
    values.extend(contract.handoff_artifacts or [])
    return [str(value) for value in values if str(value or "").strip()]


def _read_events(state_dir: Path) -> list[ZfEvent]:
    try:
        return event_log_from_project(state_dir, config=None, warn=False).read_all()
    except Exception:
        return []


def _read_tasks(state_dir: Path) -> list[Task]:
    try:
        return TaskStore(state_dir / "kanban.json").list_all_with_archive(last_days=14)
    except Exception:
        return []


def _weak_acceptance(value: str) -> bool:
    text = str(value or "").lower()
    if not text or text == "exit_code=0":
        return False
    return "verify" not in text and "check" not in text and "evidence" not in text


def _finding(kind: str, severity: str, task: Task, title: str, summary: str) -> dict[str, Any]:
    fingerprint = f"{kind}:{task.id}"
    return {
        "finding_id": hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12],
        "kind": kind,
        "severity": severity,
        "title": title,
        "summary": summary,
        "task_id": task.id,
        "source_ref": f"task:{task.id}",
        "suggested_route": "plan_revision",
    }


def _scan_task_docs(project_root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for directory in ("tasks", "backlogs"):
        root = Path(project_root) / directory
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md"))[-200:]:
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if "Acceptance" in text or "验收" in text:
                if "verify:" not in text and "-> verify" not in text:
                    rel = str(path.relative_to(project_root))
                    fid = hashlib.sha1(f"doc-acceptance:{rel}".encode("utf-8")).hexdigest()[:12]
                    findings.append({
                        "finding_id": fid,
                        "kind": "doc-acceptance-without-verify",
                        "severity": "info",
                        "title": "Backlog/task doc acceptance lacks verify",
                        "summary": "document mentions acceptance but lacks explicit verify step",
                        "task_id": "",
                        "source_ref": rel,
                        "suggested_route": "plan_revision",
                    })
    return findings


__all__ = [
    "PLAN_INTEGRITY_SCHEMA_VERSION",
    "build_plan_integrity_projection",
    "task_plan_refs",
]
