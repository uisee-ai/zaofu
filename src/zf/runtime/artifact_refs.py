"""Canonical runtime artifact reference resolution.

``.zf`` in worker output is a logical alias for ``project.state_dir``.  Keep
that compatibility in one place so admission, Goal closure, and worker
handoff cannot resolve the same reference to different files.
"""

from __future__ import annotations

from pathlib import Path


def resolve_runtime_artifact_ref(
    raw_ref: str | Path,
    *,
    project_root: Path,
    state_dir: Path,
    search_workdirs: bool = True,
) -> Path:
    """Resolve one artifact ref with deterministic state-dir precedence.

    Existing project-root and worker-worktree fallbacks remain readable for
    legacy runs, but new ``.zf`` and ``artifacts`` refs resolve to the
    configured state directory first.
    """

    ref = Path(raw_ref)
    if ref.is_absolute():
        return ref

    project_root = Path(project_root)
    state_dir = Path(state_dir)
    if ref.parts and ref.parts[0] == ".zf":
        canonical = state_dir.joinpath(*ref.parts[1:])
        candidates = [canonical, project_root / ref]
    elif ref.parts and ref.parts[0] == "artifacts":
        canonical = state_dir / ref
        candidates = [canonical, project_root / ref]
    else:
        canonical = project_root / ref
        candidates = [canonical, state_dir / ref]

    for candidate in _dedupe_paths(candidates):
        if candidate.exists():
            return candidate

    if search_workdirs:
        worktree_match = _unique_worktree_match(state_dir, ref)
        if worktree_match is not None:
            return worktree_match
    return canonical


def _unique_worktree_match(state_dir: Path, ref: Path) -> Path | None:
    workdirs = state_dir / "workdirs"
    if not workdirs.is_dir():
        return None
    matches = [
        candidate / "project" / ref
        for candidate in sorted(workdirs.iterdir())
        if candidate.is_dir() and (candidate / "project" / ref).exists()
    ]
    return matches[0] if len(matches) == 1 else None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


__all__ = ["resolve_runtime_artifact_ref"]
