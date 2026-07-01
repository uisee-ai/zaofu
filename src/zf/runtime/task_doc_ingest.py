"""Controlled Task Capsule ingest from generated task.md."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_doc import TaskDocResult, task_doc_path, write_task_doc


@dataclass(frozen=True)
class TaskDocIngestResult:
    task_id: str
    updated_fields: list[str]
    active_dispatch_cleared: bool
    task_doc: TaskDocResult


def ingest_task_doc(
    state_dir: Path,
    task_id: str,
    *,
    event_writer: EventWriter | None = None,
) -> TaskDocIngestResult:
    store = TaskStore(state_dir / "kanban.json")
    task = store.get(task_id)
    if task is None:
        raise ValueError(f"task not found: {task_id}")
    path = task_doc_path(state_dir, task_id)
    if not path.exists():
        raise ValueError(f"task doc not found: {path}")
    parsed = parse_task_doc(path.read_text(encoding="utf-8"))
    parsed_task_id = parsed.get("task_id", "")
    if parsed_task_id and parsed_task_id != task_id:
        raise ValueError(
            f"task doc task_id mismatch: expected {task_id}, got {parsed_task_id}"
        )

    updated_fields = _apply_parsed_contract(task, parsed)
    active_dispatch_cleared = bool(task.active_dispatch_id)
    if active_dispatch_cleared:
        task.active_dispatch_id = ""
    updated = store.update(
        task.id,
        contract=task.contract,
        active_dispatch_id=task.active_dispatch_id,
    ) or task
    result = write_task_doc(
        state_dir,
        updated,
        dispatch_id="",
        source_event="task_doc_ingest",
    )
    store.update(updated.id, contract=updated.contract)
    if event_writer is not None:
        event_writer.append(ZfEvent(
            type="task.doc.updated",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "source_event": "task_doc_ingest",
                "updated_fields": list(updated_fields),
                "active_dispatch_cleared": active_dispatch_cleared,
                "task_doc": str(result.path),
                "source_revision": result.source_revision,
                "contract_revision": result.contract_revision,
                "capsule_revision": result.capsule_revision,
            },
        ))
    return TaskDocIngestResult(
        task_id=task_id,
        updated_fields=updated_fields,
        active_dispatch_cleared=active_dispatch_cleared,
        task_doc=result,
    )


def parse_task_doc(text: str) -> dict[str, object]:
    lines = text.splitlines()
    parsed: dict[str, object] = {}
    parsed.update(_parse_frontmatter(lines))
    parsed.update(_parse_contract(lines))
    return parsed


def _parse_frontmatter(lines: list[str]) -> dict[str, str]:
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _parse_contract(lines: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- **Behavior**:"):
            out["behavior"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- **Verification**:"):
            out["verification"] = _strip_backticks(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("- **Scope**:"):
            out["scope"] = _parse_backtick_list(lines[index + 1:])
        elif stripped == "## Acceptance Criteria":
            out["acceptance_criteria"] = _parse_numbered_list(lines[index + 1:])
    return out


def _parse_backtick_list(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        if not line.startswith("  - "):
            break
        value = _strip_backticks(line.strip()[2:].strip())
        if value:
            values.append(value)
    return values


def _parse_numbered_list(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if values:
                break
            continue
        match = re.match(r"^\d+\.\s+(.*)$", stripped)
        if match is None:
            if values:
                break
            continue
        values.append(match.group(1).strip())
    return values


def _strip_backticks(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == "`" and text[-1] == "`":
        return text[1:-1]
    return text


def _apply_parsed_contract(task: Task, parsed: dict[str, object]) -> list[str]:
    contract = task.contract
    updated: list[str] = []
    if contract is None:
        return updated
    for field_name in ("behavior", "verification"):
        value = parsed.get(field_name)
        if isinstance(value, str) and value.strip():
            current = str(getattr(contract, field_name, "") or "")
            if value != current:
                setattr(contract, field_name, value)
                updated.append(field_name)
    for field_name in ("scope", "acceptance_criteria"):
        value = parsed.get(field_name)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            current = list(getattr(contract, field_name, []) or [])
            if value != current:
                setattr(contract, field_name, list(value))
                updated.append(field_name)
    return updated
