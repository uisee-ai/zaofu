"""ZF-TR-CTXMAN-001 — per-dispatch context manifest (doc 39 §2.1.2).

For each dispatch, generate a structured listing of the files the
worker should load: state_packet / task_contract / spec_ref /
research / skill / git / quality_gate / artifact. Written to:

    .zf/briefings/<task_id>/<dispatch_id>/context.jsonl
    .zf/briefings/<task_id>/<dispatch_id>/context.md

Recovery / Agent View / audit all read this manifest to know
exactly what the worker was loaded with — closing the
"did the worker actually have the spec?" debugging gap.

Discipline:
- The manifest is a **projection / artifact**. Worker doesn't write
  it; the kernel does at dispatch time.
- Manifest does not declare *that* a worker read the files; only
  *which* files the briefing pointed it to. Read confirmation is a
  separate concern.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from zf.core.state.atomic_io import atomic_write_text


_VALID_KINDS: frozenset[str] = frozenset({
    "state_packet",
    "task_contract",
    "spec_ref",
    "research",
    "skill",
    "git",
    "quality_gate",
    "artifact",
})


@dataclass(frozen=True)
class ContextRef:
    """One entry in the per-dispatch context manifest."""

    kind: str
    path: str
    summary: str = ""
    required: bool = True
    role: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"ContextRef.kind {self.kind!r} not in {sorted(_VALID_KINDS)}"
            )


def write_context_manifest(
    *,
    state_dir: Path,
    task_id: str,
    dispatch_id: str,
    refs: Iterable[ContextRef],
) -> tuple[Path, Path]:
    """Write context.jsonl + context.md for one dispatch.

    Returns (jsonl_path, md_path). Empty ``refs`` still produces both
    files (with a header) so downstream tooling can rely on their
    existence after every dispatch.
    """
    if not task_id or not dispatch_id:
        raise ValueError(
            "write_context_manifest requires both task_id and dispatch_id"
        )
    target_dir = state_dir / "briefings" / task_id / dispatch_id
    target_dir.mkdir(parents=True, exist_ok=True)

    refs_list = list(refs)
    jsonl_body = "\n".join(
        json.dumps(asdict(ref), sort_keys=True, ensure_ascii=False)
        for ref in refs_list
    )
    if jsonl_body:
        jsonl_body += "\n"
    jsonl_path = target_dir / "context.jsonl"
    atomic_write_text(jsonl_path, jsonl_body)

    md_lines = [
        f"# Context Manifest · {task_id} · {dispatch_id}",
        "",
        "> projection only, not runtime truth",
        "",
    ]
    if not refs_list:
        md_lines.append(
            "_No context refs declared for this dispatch._"
        )
    else:
        # Group by kind for readability.
        by_kind: dict[str, list[ContextRef]] = {}
        for ref in refs_list:
            by_kind.setdefault(ref.kind, []).append(ref)
        for kind in sorted(by_kind):
            md_lines.append(f"## {kind}")
            md_lines.append("")
            for ref in by_kind[kind]:
                line = f"- `{ref.path}`"
                if ref.required:
                    line += " (required)"
                if ref.role:
                    line += f" · role: {ref.role}"
                if ref.summary:
                    line += f" — {ref.summary}"
                md_lines.append(line)
            md_lines.append("")
    md_path = target_dir / "context.md"
    atomic_write_text(md_path, "\n".join(md_lines))

    return jsonl_path, md_path


def read_context_manifest(
    *,
    state_dir: Path,
    task_id: str,
    dispatch_id: str,
) -> list[ContextRef]:
    """Read context.jsonl back into ContextRef list, tolerating bad
    lines (returns empty for missing file)."""
    jsonl_path = (
        state_dir / "briefings" / task_id / dispatch_id / "context.jsonl"
    )
    if not jsonl_path.exists():
        return []
    out: list[ContextRef] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            out.append(ContextRef(
                kind=str(obj.get("kind", "") or ""),
                path=str(obj.get("path", "") or ""),
                summary=str(obj.get("summary", "") or ""),
                required=bool(obj.get("required", True)),
                role=str(obj.get("role", "") or ""),
            ))
        except (ValueError, TypeError):
            continue
    return out


def required_refs_missing(
    refs: Iterable[ContextRef],
    *,
    project_root: Path,
) -> list[str]:
    """Return the file paths of required refs that don't exist on
    disk. Used by preflight to refuse dispatch when context is
    incomplete (doc 40 §6 candidate I54)."""
    missing: list[str] = []
    for ref in refs:
        if not ref.required:
            continue
        if not ref.path:
            continue
        path = Path(ref.path)
        if not path.is_absolute():
            path = project_root / path
        if not path.exists():
            missing.append(ref.path)
    return missing
