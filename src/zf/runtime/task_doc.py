"""Kernel-managed Markdown task capsule projection."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.schema import Task


@dataclass(frozen=True)
class TaskDocResult:
    path: Path
    manifest_path: Path
    source_path: Path
    progress_path: Path
    evidence_path: Path
    source_revision: str
    contract_revision: str
    capsule_revision: str


def task_doc_dir(state_dir: Path, task_id: str) -> Path:
    return state_dir / "task_docs" / task_id


def task_doc_path(state_dir: Path, task_id: str) -> Path:
    return task_doc_dir(state_dir, task_id) / "task.md"


def task_source_path(state_dir: Path, task_id: str) -> Path:
    return task_doc_dir(state_dir, task_id) / "source.md"


def task_progress_path(state_dir: Path, task_id: str) -> Path:
    return task_doc_dir(state_dir, task_id) / "progress.md"


def task_evidence_path(state_dir: Path, task_id: str) -> Path:
    return task_doc_dir(state_dir, task_id) / "evidence.md"


def compute_task_capsule_revisions(task: Task) -> dict[str, str]:
    """Return stable revision identifiers for the current canonical task."""
    contract = task.contract
    source_payload = {
        "task_id": task.id,
        "title": task.title,
        "source_key": getattr(contract, "source_key", "") if contract else "",
        "source_ref": getattr(contract, "source_ref", "") if contract else "",
        "source_task_id": getattr(contract, "source_task_id", "") if contract else "",
        "source_index_ref": getattr(contract, "source_index_ref", "") if contract else "",
        "source_mode": getattr(contract, "source_mode", "") if contract else "",
        "source_title": getattr(contract, "source_title", "") if contract else "",
        "source_excerpt": getattr(contract, "source_excerpt", "") if contract else "",
        "source_backlog_task_id": (
            getattr(contract, "source_backlog_task_id", "") if contract else ""
        ),
        "behavior": getattr(contract, "behavior", "") if contract else "",
        "plan_ref": getattr(contract, "plan_ref", "") if contract else "",
        "spec_ref": getattr(contract, "spec_ref", "") if contract else "",
    }
    contract_payload = _canonical_contract_payload(task)
    source_revision = _stable_revision("source", source_payload)
    contract_revision = _stable_revision("contract", contract_payload)
    capsule_revision = _stable_revision(
        "capsule",
        {
            "task_id": task.id,
            "source_revision": source_revision,
            "contract_revision": contract_revision,
            "blocked_by": list(task.blocked_by),
            "priority": task.priority,
        },
    )
    return {
        "source_revision": source_revision,
        "contract_revision": contract_revision,
        "capsule_revision": capsule_revision,
    }


def apply_task_capsule_revisions(task: Task) -> dict[str, str]:
    revisions = compute_task_capsule_revisions(task)
    contract = task.contract
    if contract is not None:
        contract.source_revision = revisions["source_revision"]
        contract.contract_revision = revisions["contract_revision"]
        contract.capsule_revision = revisions["capsule_revision"]
    return revisions


def render_source_doc(
    task: Task,
    *,
    source_revision: str = "",
    generated_at: str = "",
) -> str:
    generated_at = generated_at or _now_iso()
    contract = task.contract
    source_key = _first_nonempty(
        getattr(contract, "source_key", "") if contract else "",
        getattr(contract, "source_ref", "") if contract else "",
        getattr(contract, "plan_ref", "") if contract else "",
        getattr(contract, "source_backlog_task_id", "") if contract else "",
        task.key,
        task.id,
    )
    source_ref = _first_nonempty(
        getattr(contract, "source_ref", "") if contract else "",
        getattr(contract, "plan_ref", "") if contract else "",
        getattr(contract, "spec_ref", "") if contract else "",
    )
    lines = [
        f"# Source for {task.id} - {task.title}",
        "",
        f"> source_key: {source_key}",
        f"> source_ref: {source_ref}",
        f"> source_task_id: {getattr(contract, 'source_task_id', '') if contract else ''}",
        f"> source_index_ref: {getattr(contract, 'source_index_ref', '') if contract else ''}",
        f"> source_mode: {getattr(contract, 'source_mode', '') if contract else ''}",
        f"> source_revision: {source_revision}",
        f"> generated_at: {generated_at}",
        "",
        "## Semantic Source",
        "",
    ]
    source_title = str(getattr(contract, "source_title", "") if contract else "").strip()
    if source_title:
        lines.append(f"### {source_title}")
        lines.append("")
    source_excerpt = str(getattr(contract, "source_excerpt", "") if contract else "").strip()
    behavior = str(getattr(contract, "behavior", "") if contract else "").strip()
    if source_excerpt:
        lines.append(source_excerpt)
    elif behavior:
        lines.append(behavior)
    else:
        lines.append("_No explicit behavior text is available in the task contract._")
    lines.append("")
    _append_field(lines, "Product Contract Ref", getattr(contract, "product_contract_ref", ""))
    _append_field(lines, "Source Index Ref", getattr(contract, "source_index_ref", ""))
    _append_field(lines, "Source Mode", getattr(contract, "source_mode", ""))
    _append_field(lines, "Spec Ref", getattr(contract, "spec_ref", ""))
    _append_field(lines, "Plan Ref", getattr(contract, "plan_ref", ""))
    _append_field(lines, "Spec Skip Reason", getattr(contract, "spec_skip_reason", ""))
    return "\n".join(lines).rstrip() + "\n"


def render_task_doc(
    task: Task,
    *,
    generated_at: str = "",
    source_revision: str = "",
    contract_revision: str = "",
    capsule_revision: str = "",
    resolved_refs: list[dict[str, Any]] | None = None,
) -> str:
    """Render the agent-facing runtime envelope for a task."""
    generated_at = generated_at or _now_iso()
    revisions = compute_task_capsule_revisions(task)
    source_revision = source_revision or revisions["source_revision"]
    contract_revision = contract_revision or revisions["contract_revision"]
    capsule_revision = capsule_revision or revisions["capsule_revision"]
    contract = task.contract
    lines: list[str] = [
        "---",
        "schema_version: task-doc.v1",
        f"task_id: {task.id}",
        f"status_hint: {task.status}",
        f"assigned_to: {task.assigned_to or ''}",
        f"active_dispatch_id: {task.active_dispatch_id or ''}",
        f"source_revision: {source_revision}",
        f"contract_revision: {contract_revision}",
        f"capsule_revision: {capsule_revision}",
        f"generated_at: {generated_at}",
        "source: kernel_projection",
        "---",
        "",
        f"# {task.id} - {task.title}",
        "",
        "> projection only, not runtime truth",
        "> workers must not edit this file to mark completion",
        "> completion flow: emit event -> kernel gate -> task capsule regenerated",
        "",
        "## Required Read",
        "",
        "1. Read `source.md` fully before editing.",
        "2. Read `progress.md` to recover current work state.",
        "3. Follow the runtime binding and execution boundary in this file.",
        "4. If source/task/capsule revision is missing or stale, stop and emit context incomplete.",
        "",
        "## Runtime Binding",
        "",
        f"- **Status hint**: `{task.status}`",
        f"- **Assigned to**: `{task.assigned_to or '(unassigned)'}`",
        f"- **Active dispatch id**: `{task.active_dispatch_id or '(none)'}`",
        f"- **Source revision**: `{source_revision}`",
        f"- **Contract revision**: `{contract_revision}`",
        f"- **Capsule revision**: `{capsule_revision}`",
        "",
    ]

    if task.blocked_by:
        lines.append(
            "- **Blocked by**: "
            + ", ".join(f"`{item}`" for item in task.blocked_by)
        )
    if task.blocked_reason:
        lines.append(f"- **Blocked reason**: {task.blocked_reason}")
    if task.blocked_by or task.blocked_reason:
        lines.append("")

    lines.extend(["## Contract", ""])
    if contract is None:
        lines.append("_(no task contract)_")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    _append_field(lines, "Behavior", contract.behavior)
    _append_field(lines, "Verification", contract.verification, code=True)
    _append_field(lines, "Acceptance", contract.acceptance, code=True)
    _append_list(lines, "Scope", contract.scope)
    _append_list(lines, "Affected Files", contract.affected_files)
    _append_list(lines, "Exclusions", contract.exclusions)
    _append_list(lines, "Explicit Non-goals", contract.explicit_non_goals)
    _append_list(lines, "Verification Tiers", contract.verification_tiers)
    _append_list(lines, "Unknowns", getattr(contract, "unknowns", []))

    refs = _contract_refs(contract)
    if refs:
        lines.append("## Contract References")
        lines.append("")
        for key, value in refs.items():
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    resolved_refs = resolved_refs or []
    if resolved_refs:
        lines.append("## Resolved Source References")
        lines.append("")
        lines.append(
            "Use `resolved_path` when running from a worker worktree; original "
            "refs may be relative to the project root or configured state_dir."
        )
        lines.append("")
        for item in resolved_refs:
            lines.append(f"- `{item.get('key', '')}`")
            lines.append(f"  - original_ref: `{item.get('original_ref', '')}`")
            lines.append(f"  - resolved_path: `{item.get('resolved_path', '')}`")
            lines.append(f"  - readable: `{str(item.get('readable', False)).lower()}`")
            if item.get("repo_relative_ref"):
                lines.append(
                    f"  - repo_relative_ref: `{item.get('repo_relative_ref', '')}`"
                )
            if item.get("state_dir_relative_ref"):
                lines.append(
                    "  - state_dir_relative_ref: "
                    f"`{item.get('state_dir_relative_ref', '')}`"
                )
        lines.append("")

    if contract.acceptance_criteria or contract.acceptance_evidence:
        lines.append("## Acceptance Criteria")
        lines.append("")
        if contract.acceptance_criteria:
            for index, item in enumerate(contract.acceptance_criteria, start=1):
                lines.append(f"{index}. {item}")
        if contract.acceptance_evidence:
            lines.append("")
            _append_json_block(lines, "acceptance_evidence", contract.acceptance_evidence)
        lines.append("")

    if contract.validation or contract.evidence_contract:
        lines.append("## Evidence Contract")
        lines.append("")
        if contract.validation:
            _append_json_block(lines, "validation", contract.validation)
        if contract.evidence_contract:
            _append_json_block(lines, "evidence_contract", contract.evidence_contract)
        lines.append("")

    lines.append("## Completion Rule")
    lines.append("")
    lines.append(
        "Do not mark this task done by editing this file. Emit the role's "
        "completion event with dispatch_id, source_revision, contract_revision, "
        "capsule_revision, and evidence; the kernel will update runtime state "
        "and regenerate this capsule."
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_progress_doc(task: Task, *, generated_at: str = "") -> str:
    generated_at = generated_at or _now_iso()
    return (
        f"# Progress for {task.id}\n\n"
        f"> generated_at: {generated_at}\n"
        "> projection only, not runtime truth\n\n"
        "## Current State\n\n"
        f"- status_hint: `{task.status}`\n"
        f"- assigned_to: `{task.assigned_to or ''}`\n\n"
        "## Completed\n\n"
        "_No projected progress yet._\n\n"
        "## In Progress\n\n"
        "_No projected progress yet._\n\n"
        "## Blockers\n\n"
        f"{task.blocked_reason or '_No blocker projected._'}\n\n"
        "## Next Step\n\n"
        "_Read task.md and source.md, then continue from latest events/evidence._\n"
    )


def render_evidence_doc(task: Task, *, generated_at: str = "") -> str:
    generated_at = generated_at or _now_iso()
    evidence = asdict(task.evidence) if task.evidence else {}
    lines = [
        f"# Evidence for {task.id}",
        "",
        f"> generated_at: {generated_at}",
        "> projection only, not runtime truth",
        "",
    ]
    if evidence:
        _append_json_block(lines, "evidence", evidence)
    else:
        lines.append("_No evidence projected yet._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_task_doc(
    state_dir: Path,
    task: Task,
    *,
    dispatch_id: str = "",
    source_event: str = "",
    project_root: Path | None = None,
) -> TaskDocResult:
    """Write Task Capsule files for the current kernel task state."""
    generated_at = _now_iso()
    revisions = apply_task_capsule_revisions(task)
    base = task_doc_dir(state_dir, task.id)
    base.mkdir(parents=True, exist_ok=True)

    path = task_doc_path(state_dir, task.id)
    source_path = task_source_path(state_dir, task.id)
    progress_path = task_progress_path(state_dir, task.id)
    evidence_path = task_evidence_path(state_dir, task.id)
    manifest_path = base / "manifest.json"
    project_root = project_root or state_dir.parent
    resolved_refs = resolve_task_source_refs(
        state_dir=state_dir,
        project_root=project_root,
        task=task,
    )

    if task.contract is not None:
        task.contract.task_doc_ref = str(path)
        task.contract.source_doc_ref = str(source_path)
        task.contract.progress_doc_ref = str(progress_path)
        task.contract.evidence_doc_ref = str(evidence_path)

    atomic_write_text(
        source_path,
        render_source_doc(
            task,
            source_revision=revisions["source_revision"],
            generated_at=generated_at,
        ),
    )
    atomic_write_text(
        path,
        render_task_doc(
            task,
            generated_at=generated_at,
            source_revision=revisions["source_revision"],
            contract_revision=revisions["contract_revision"],
            capsule_revision=revisions["capsule_revision"],
            resolved_refs=resolved_refs,
        ),
    )
    try:
        from zf.runtime.task_progress_projector import (
            render_projected_evidence_doc,
            render_projected_progress_doc,
        )

        progress_text = render_projected_progress_doc(
            state_dir,
            task,
            generated_at=generated_at,
        )
        evidence_text = render_projected_evidence_doc(
            state_dir,
            task,
            generated_at=generated_at,
        )
    except Exception:
        progress_text = render_progress_doc(task, generated_at=generated_at)
        evidence_text = render_evidence_doc(task, generated_at=generated_at)
    atomic_write_text(progress_path, progress_text)
    atomic_write_text(evidence_path, evidence_text)

    manifest = {
        "schema_version": "task-doc-manifest.v1",
        "task_id": task.id,
        "task_doc": str(path),
        "source_doc": str(source_path),
        "progress_doc": str(progress_path),
        "evidence_doc": str(evidence_path),
        "source_revision": revisions["source_revision"],
        "contract_revision": revisions["contract_revision"],
        "capsule_revision": revisions["capsule_revision"],
        "status": task.status,
        "assigned_to": task.assigned_to or "",
        "active_dispatch_id": task.active_dispatch_id or "",
        "dispatch_id": dispatch_id,
        "source_event": source_event,
        "source_key": getattr(task.contract, "source_key", "") if task.contract else "",
        "source_ref": getattr(task.contract, "source_ref", "") if task.contract else "",
        "source_task_id": (
            getattr(task.contract, "source_task_id", "") if task.contract else ""
        ),
        "source_index_ref": (
            getattr(task.contract, "source_index_ref", "") if task.contract else ""
        ),
        "source_mode": getattr(task.contract, "source_mode", "") if task.contract else "",
        "product_contract_ref": (
            getattr(task.contract, "product_contract_ref", "") if task.contract else ""
        ),
        "resolved_refs": resolved_refs,
        "generated_at": generated_at,
    }
    atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return TaskDocResult(
        path=path,
        manifest_path=manifest_path,
        source_path=source_path,
        progress_path=progress_path,
        evidence_path=evidence_path,
        source_revision=revisions["source_revision"],
        contract_revision=revisions["contract_revision"],
        capsule_revision=revisions["capsule_revision"],
    )


def verify_task_capsule(state_dir: Path, task: Task) -> list[str]:
    """Return freshness/preflight errors for a task capsule."""
    errors: list[str] = []
    base = task_doc_dir(state_dir, task.id)
    required = {
        "source_missing": task_source_path(state_dir, task.id),
        "task_doc_missing": task_doc_path(state_dir, task.id),
        "manifest_missing": base / "manifest.json",
    }
    for reason, path in required.items():
        if not path.exists():
            errors.append(reason)
    if errors:
        return errors
    try:
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return ["manifest_invalid"]
    expected = compute_task_capsule_revisions(task)
    for key in ("source_revision", "contract_revision", "capsule_revision"):
        if str(manifest.get(key) or "") != expected[key]:
            errors.append(f"{key}_stale")
    return errors


def resolve_task_source_refs(
    *,
    state_dir: Path,
    project_root: Path,
    task: Task,
) -> list[dict[str, Any]]:
    """Resolve task source refs for worker worktrees.

    Contract refs are authored relative to either the project root or the
    configured state_dir. Worker processes often run from an isolated worktree,
    so the capsule projects an absolute `resolved_path` for deterministic reads.
    """
    contract = task.contract
    if contract is None:
        return []
    refs = _source_ref_values(contract)
    out: list[dict[str, Any]] = []
    for key, ref in refs.items():
        item = _resolve_one_ref(
            key=key,
            ref=ref,
            state_dir=state_dir,
            project_root=project_root,
        )
        if item:
            out.append(item)
    return out


def _source_ref_values(contract: Any) -> dict[str, str]:
    refs: dict[str, str] = {}
    for key in (
        "source_ref",
        "source_index_ref",
        "product_contract_ref",
        "spec_ref",
        "plan_ref",
        "tdd_ref",
        "critic_gate_ref",
    ):
        value = str(getattr(contract, key, "") or "").strip()
        if value:
            refs[key] = value
    evidence_contract = getattr(contract, "evidence_contract", {})
    if isinstance(evidence_contract, dict):
        for bucket_name in ("source_refs", "artifact_refs"):
            bucket = evidence_contract.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            for key, value in bucket.items():
                text = str(value or "").strip()
                if text:
                    refs.setdefault(str(key), text)
    return refs


def _resolve_one_ref(
    *,
    key: str,
    ref: str,
    state_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    raw = str(ref or "").strip()
    if not raw or "://" in raw or raw.startswith("#"):
        return {}
    if _looks_like_non_file_ref_key(key):
        return {}
    if _looks_like_non_file_ref(raw):
        return {}
    path_text = raw.split("#", 1)[0]
    if not path_text:
        return {}
    ref_path = Path(path_text)
    candidates = _ref_candidates(ref_path, state_dir=state_dir, project_root=project_root)
    resolved = next((path for path in candidates if path.exists()), candidates[0])
    readable = resolved.exists()
    return {
        "key": key,
        "original_ref": raw,
        "repo_relative_ref": _relative_or_empty(resolved, project_root),
        "state_dir_relative_ref": _relative_or_empty(resolved, state_dir),
        "resolved_path": str(resolved),
        "readable": readable,
        "attempted_paths": [str(path) for path in candidates],
    }


def _looks_like_non_file_ref(raw: str) -> bool:
    value = raw.strip()
    normalized = value.strip("()[]{}<>\"'`").strip()
    lower = normalized.lower()
    if lower.startswith(("event:", "task:", "dispatch:", "trace:")):
        return True
    if lower.startswith("evt-") and "/" not in normalized and "\\" not in normalized:
        return True
    if lower.startswith("disp-") and "/" not in normalized and "\\" not in normalized:
        return True
    return False


def _looks_like_non_file_ref_key(key: str) -> bool:
    lower = str(key or "").strip().lower()
    if lower in {
        "event_id",
        "dispatch_id",
        "critic_event",
        "review_event",
        "gate_event",
        "trace_id",
    }:
        return True
    return lower.endswith(("_event_id", "_dispatch_id"))


def _ref_candidates(
    ref_path: Path,
    *,
    state_dir: Path,
    project_root: Path,
) -> list[Path]:
    if ref_path.is_absolute():
        return [ref_path]
    candidates = [
        project_root / ref_path,
        state_dir / ref_path,
    ]
    if ref_path.parts:
        first = ref_path.parts[0]
        if first in {state_dir.name, ".zf"} and len(ref_path.parts) > 1:
            candidates.append(state_dir / Path(*ref_path.parts[1:]))
            candidates.append(state_dir.parent / ref_path)
        if first == "artifacts":
            candidates.append(state_dir / ref_path)
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _relative_or_empty(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_revision(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    token = uuid.uuid5(uuid.NAMESPACE_URL, encoded).hex[:12]
    return f"{prefix}-r{token}"


def _canonical_contract_payload(task: Task) -> dict[str, Any]:
    contract = task.contract
    if contract is None:
        return {"task_id": task.id, "contract": None}
    data = asdict(contract)
    for key in (
        "task_doc_ref",
        "source_doc_ref",
        "progress_doc_ref",
        "evidence_doc_ref",
        "source_revision",
        "contract_revision",
        "capsule_revision",
        "acceptance_evidence",
    ):
        data.pop(key, None)
    return {
        "task_id": task.id,
        "title": task.title,
        "contract": data,
        "blocked_by": list(task.blocked_by),
        "skills_required": list(task.skills_required),
    }


def _append_field(lines: list[str], label: str, value: Any, *, code: bool = False) -> None:
    text = str(value or "").strip()
    if not text:
        return
    if code:
        lines.append(f"- **{label}**: `{text}`")
    else:
        lines.append(f"- **{label}**: {text}")


def _append_list(lines: list[str], label: str, values: list[str]) -> None:
    if not values:
        return
    lines.append(f"- **{label}**:")
    for value in values:
        lines.append(f"  - `{value}`")
    lines.append("")


def _append_json_block(lines: list[str], label: str, value: Any) -> None:
    lines.append(f"### {label}")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")


def _contract_refs(contract: Any) -> dict[str, str]:
    keys = (
        "source_backlog_task_id",
        "source_key",
        "source_ref",
        "source_task_id",
        "source_index_ref",
        "source_mode",
        "product_contract_ref",
        "spec_skip_reason",
        "spec_ref",
        "plan_ref",
        "tdd_ref",
        "critic_gate_ref",
        "critic_event_id",
        "reviewed_arch_event_id",
        "source_arch_dispatch_id",
    )
    refs: dict[str, str] = {}
    for key in keys:
        value = str(getattr(contract, key, "") or "").strip()
        if value:
            refs[key] = value
    return refs


def _first_nonempty(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""
