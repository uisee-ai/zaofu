"""Atomic, read-only failure evidence bundles for TestKit cases."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from .evidence import sha256_file, write_json_atomic


def write_failure_bundle(
    bundle_dir: Path,
    *,
    metadata: Mapping[str, Any],
    evidence_paths: Iterable[Path],
) -> str:
    """Copy immutable evidence into a new bundle and write its manifest.

    Sources are never modified or removed. The destination must not already
    exist: reusing a bundle would make a later failure indistinguishable from
    the original one. A sibling temporary directory is atomically renamed
    only after every copy and the manifest have succeeded.
    """

    destination = bundle_dir.resolve()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"failure bundle already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        evidence_dir = temporary / "evidence"
        evidence_dir.mkdir()
        entries: list[dict[str, Any]] = []
        for index, source in enumerate(evidence_paths):
            source_path = Path(source)
            if source_path.is_symlink() or not source_path.is_file():
                raise ValueError(f"failure evidence must be a regular file: {source_path}")
            resolved = source_path.resolve()
            target = evidence_dir / f"{index:03d}-{resolved.name}"
            shutil.copyfile(resolved, target)
            entries.append(
                {
                    "source": str(resolved),
                    "ref": target.relative_to(temporary).as_posix(),
                    "digest": sha256_file(target),
                    "bytes": target.stat().st_size,
                }
            )
        manifest = {
            "schema_version": "zf-testkit-failure-bundle.v1",
            "metadata": dict(metadata),
            "evidence": entries,
        }
        write_json_atomic(temporary / "manifest.json", manifest)
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return sha256_file(destination / "manifest.json")


__all__ = ["write_failure_bundle"]
