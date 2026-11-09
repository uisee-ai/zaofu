"""Retain configured skill packages and fence method drift in the D0 pilot."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from zf.core.skills.provenance import build_skill_lock_entries
from zf.runtime.run_contract import stable_json_sha256


def _package(path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    total = 0
    try:
        for item in sorted(path.rglob("*")):
            relative = item.relative_to(path)
            if {".git", "__pycache__"}.intersection(relative.parts) or item.suffix == ".pyc":
                continue
            if not item.is_file():
                continue
            if not item.resolve().is_relative_to(path.resolve()):
                raise ValueError(f"stage method dependency escapes package: {item}")
            data = item.read_bytes()
            total += len(data)
            if total > 16 * 1024 * 1024:
                raise ValueError(f"stage method package exceeds snapshot limit: {path}")
            files[relative.as_posix()] = base64.b64encode(data).decode("ascii")
    except OSError as exc:
        raise ValueError(f"stage method dependency unreadable: {path}") from exc
    if "SKILL.md" not in files:
        raise ValueError(f"stage method dependency missing: {path}")
    return files


def pin_stage_methods(
    *, state_dir: Path, project_root: Path | None, config: Any, roles: list,
) -> dict:
    packages = {}
    for role in roles:
        # External agent/plugin definitions need their own immutable adapter;
        # admitting an opaque mutable method would silently expand this pilot.
        if role.agent or role.plugins:
            raise ValueError("stage method binding does not support external agent/plugin definitions")
        if not role.skills:
            continue
        if project_root is None:
            raise ValueError("stage method binding requires explicit project_root")
        entries = build_skill_lock_entries(
            project_root=project_root, state_dir=state_dir, role=role, config=config,
        )
        for entry in entries:
            if entry.status != "resolved" or not entry.source:
                raise ValueError(f"stage method dependency unresolved: {entry.name}")
            source = Path(entry.source)
            if not source.is_absolute():
                source = project_root / source
            path = source.resolve().parent
            if str(path) not in packages:
                files = _package(path)
                packages[str(path)] = {"files": files, "sha256": stable_json_sha256(files)}
    return packages


def check_stage_methods(packages: dict) -> None:
    for path, package in packages.items():
        expected = package["sha256"]
        if stable_json_sha256(package["files"]) != expected:
            raise ValueError(f"stage method snapshot digest mismatch: {path}")
        if stable_json_sha256(_package(Path(path))) != expected:
            raise ValueError(f"stage method dependency changed; controlled revision required: {path}")
