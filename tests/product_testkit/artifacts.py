"""Bounded artifact references and digest verification for product runs."""

from __future__ import annotations

from pathlib import Path
import json

from tests.testkit.evidence import sha256_file, write_json_atomic


class ArtifactError(ValueError):
    """Artifact evidence is missing, outside the run root, or changed."""


def capture_artifact(path: Path, *, root: Path, output: Path | None = None) -> dict[str, str]:
    """Capture a regular file under ``root`` and optionally write a sidecar."""

    base = root.resolve()
    candidate = path if path.is_absolute() else base / path
    if candidate.is_symlink() or not candidate.is_file():
        raise ArtifactError(f"artifact is not a regular file: {candidate}")
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError as exc:
        raise ArtifactError(f"artifact escapes root: {candidate}") from exc
    record = {"ref": relative.as_posix(), "digest": sha256_file(resolved)}
    if output is not None:
        write_json_atomic(output, record)
    return record


def verify_artifact(record: dict[str, str], *, root: Path) -> None:
    """Verify both the path boundary and the immutable content digest."""

    if set(record) != {"ref", "digest"}:
        raise ArtifactError("artifact record must contain ref and digest only")
    capture = capture_artifact(Path(record["ref"]), root=root)
    if capture["digest"] != record["digest"]:
        raise ArtifactError("artifact digest mismatch")


def build_artifact_index(paths: list[Path] | tuple[Path, ...], *, root: Path) -> list[dict[str, str]]:
    """Return deterministic refs/digests for run evidence files."""

    return [capture_artifact(path, root=root) for path in paths]


def verify_artifact_index(index: list[dict[str, str]], *, root: Path) -> None:
    """Re-hash every indexed evidence file and fail on drift or malformed rows."""

    if not isinstance(index, list) or not index:
        raise ArtifactError("artifact index must be a non-empty list")
    for record in index:
        if not isinstance(record, dict):
            raise ArtifactError("artifact index rows must be objects")
        verify_artifact(record, root=root)


def write_artifact_index(index: list[dict[str, str]], path: Path) -> None:
    """Persist an already validated index using an atomic replacement."""

    if not isinstance(index, list) or not index:
        raise ArtifactError("artifact index must be a non-empty list")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)

