"""Immutable Channel method packages and explicit dependency bindings."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from zf.core.skills.provenance import (
    read_skill_metadata, resolve_builtin_skill_source, resolve_skill_source,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path

def _materialize_channel_skill_refs(
    skill_refs: list[str],
    *,
    project_root: Path,
    state_dir: Path,
    config: Any,
) -> tuple[list[str], list[dict[str, str]]]:
    """Resolve logical refs without mutating the target project's source."""
    unresolved: list[str] = []
    resolved: list[dict[str, str]] = []
    pending = list(skill_refs)
    seen: set[str] = set()
    while pending:
        ref = pending.pop(0)
        if ref in seen:
            continue
        seen.add(ref)
        if len(seen) > 64:
            raise ValueError("channel skill dependency closure exceeds 64 methods")
        parts = ref.split("/")
        if (len(parts) != 3 or parts[0] != "skills" or parts[2] != "SKILL.md"
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", parts[1])):
            unresolved.append(ref)
            continue
        name = parts[1]
        project_target = project_root / "skills" / name / "SKILL.md"
        if project_target.exists():
            resolved.append(_resolved_channel_skill(
                logical_ref=ref,
                path=project_target,
                source="project",
            ))
            pending.extend(_dependency_refs(project_target, name))
            continue
        source = resolve_skill_source(
            project_root=project_root,
            state_dir=state_dir,
            name=name,
            config=config,
        )
        if source is None:
            source = resolve_builtin_skill_source(name)
        if source is None or not source.is_file():
            unresolved.append(ref)
            continue
        body = source.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        target = state_dir / "runtime-skills" / name / digest / "SKILL.md"
        target.parent.parent.mkdir(parents=True, exist_ok=True)
        # An invitation must not replace a method pinned by existing members.
        # Publish the complete bundle before emitting its member binding.
        with locked_path(target.parent):
            if not target.parent.exists():
                with tempfile.TemporaryDirectory(dir=target.parent.parent) as staging:
                    bundle = Path(staging) / "bundle"
                    shutil.copytree(source.parent, bundle)
                    (bundle / "SKILL.md").write_bytes(body)
                    bundle.replace(target.parent)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError(f"channel skill snapshot digest mismatch: {ref}")
        resolved.append(_resolved_channel_skill(
            logical_ref=ref,
            path=target,
            source=str(source),
        ))
        pending.extend(_dependency_refs(target, name))
    if resolved:
        _write_runtime_skill_manifest(state_dir, resolved)
    return unresolved, resolved


def _resolved_channel_skill(
    *,
    logical_ref: str,
    path: Path,
    source: str,
) -> dict[str, str]:
    return {
        "logical_ref": logical_ref,
        "resolved_path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": source,
    }


def _write_runtime_skill_manifest(
    state_dir: Path,
    resolved: list[dict[str, str]],
) -> None:
    manifest_path = state_dir / "runtime-skills" / "manifest.json"
    existing: list[dict[str, str]] = []
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = [
                item
                for item in raw.get("skills") or []
                if isinstance(item, dict)
            ]
        except (OSError, ValueError):
            existing = []
    by_ref = {
        str(item.get("logical_ref") or ""): item
        for item in [*existing, *resolved]
        if str(item.get("logical_ref") or "")
    }
    atomic_write_text(
        manifest_path,
        json.dumps(
            {
                "schema_version": "channel.runtime_skills.v1",
                "skills": [
                    by_ref[key] for key in sorted(by_ref)
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _dependency_refs(path: Path, name: str) -> list[str]:
    # Read the pinned method, never its possibly upgraded upstream source.
    return [f"skills/{dependency}/SKILL.md"
            for dependency in read_skill_metadata(path, expected_name=name).dependencies]
