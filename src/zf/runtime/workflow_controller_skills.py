"""Freeze only missing controller skills as required config-candidate assets."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.skills.provenance import (
    _normalize_dependency_name, read_skill_metadata, resolve_builtin_skill_source, resolve_skill,
)
from zf.core.state.locks import locked_path


SOURCE_NAME = "registered-controller-skills"


def validate_controller_skill_packages(config: ZfConfig, root: Path) -> None:
    asset_root = (root / config.project.state_dir / "artifacts/workflow/controller-skills").resolve()
    for source in config.skill_sources:
        if source.name != SOURCE_NAME:
            continue
        bundle = Path(source.path)
        if bundle.is_symlink() or bundle.resolve().parent != asset_root:
            raise ValueError("controller skill package is outside the bound asset directory")
        manifest_path = bundle / "manifest.json"
        if manifest_path.is_symlink():
            raise ValueError("controller skill manifest must not be a symlink")
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict) or hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest() != bundle.name:
            raise ValueError("controller skill package manifest digest mismatch")
        actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
        if actual != {*manifest, "manifest.json"} or any(p.is_symlink() for p in bundle.rglob("*")):
            raise ValueError("controller skill package file set changed")
        for name, metadata in manifest.items():
            path = bundle / name
            if (not path.resolve().is_relative_to(bundle.resolve())
                    or hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]
                    or path.stat().st_mode & 0o777 != metadata["mode"]):
                raise ValueError("controller skill package content changed")


def controller_skill_source(*, root: Path, base: ZfConfig, template: ZfConfig) -> dict | None:
    # This name is owned by controller assembly. Replacing it is included in
    # the config diff; project-owned sources and skill copies remain intact.
    base.skill_sources = [s for s in base.skill_sources if s.name != SOURCE_NAME]
    state = (root / base.project.state_dir).resolve()
    queue = [skill for role in template.roles for skill in role.skills]
    seen: set[str] = set()
    missing: dict[str, Path] = {}
    while queue:
        name = _normalize_dependency_name(queue.pop(0))
        if name in seen:
            continue
        seen.add(name)
        resolved = resolve_skill(project_root=root, state_dir=state, name=name, config=base)
        source = resolved.path
        if source is None:
            source = resolve_builtin_skill_source(name)
            if source is None:
                raise ValueError(f"registered controller skill is unavailable: {name}")
            missing[name] = source.parent
        queue.extend(read_skill_metadata(source, expected_name=name).dependencies)
    if not missing:
        return None
    files: dict[str, tuple[bytes, int]] = {}
    for name, directory in sorted(missing.items()):
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("controller skill package must not contain symlinks")
            if path.is_file():
                files[f"{name}/{path.relative_to(directory).as_posix()}"] = (path.read_bytes(), path.stat().st_mode & 0o777)
    manifest = {name: {"sha256": hashlib.sha256(body).hexdigest(), "mode": mode}
                for name, (body, mode) in files.items()}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    bundle = state / "artifacts" / "workflow" / "controller-skills" / digest
    with locked_path(bundle):
        if not bundle.exists():
            staging = Path(tempfile.mkdtemp(prefix=".skill-package-", dir=bundle.parent))
            try:
                for name, (body, mode) in files.items():
                    target = staging / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    target.chmod(mode)
                (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
                os.rename(staging, bundle)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        if bundle.is_symlink() or (bundle / "manifest.json").is_symlink():
            raise ValueError("controller skill package must not be a symlink")
        if json.loads((bundle / "manifest.json").read_text()) != manifest:
            raise ValueError("controller skill package manifest changed")
        for name, (body, mode) in files.items():
            target = bundle / name
            if (target.is_symlink() or target.resolve() != target or not target.is_file()
                    or target.read_bytes() != body or target.stat().st_mode & 0o777 != mode):
                raise ValueError("controller skill package content changed")
    return {"name": SOURCE_NAME, "path": str(bundle), "mode": "readonly"}
