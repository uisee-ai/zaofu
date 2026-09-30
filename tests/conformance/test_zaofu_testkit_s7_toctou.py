"""S7 deterministic TOCTOU checks for file-artifact sinks.

The test swaps a validated destination with a symlink in a controlled seam,
then observes the real binary copy sink.  It only uses files below pytest's
temporary directory and never touches a repository or runtime state file.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from zf.core.safety import PathGuardError
import zf.runtime.fanout_artifact_refs as fanout_artifact_refs
import zf.runtime.run_archive as run_archive


class ProductionPathTOCTOUBug(AssertionError):
    """Destination changed after validation and the sink still accepted it."""


class ProductionFanoutPathTOCTOUBug(AssertionError):
    """Fanout relocation followed a destination symlink after validation."""


class ProductionDirectoryPathTOCTOUBug(AssertionError):
    """Directory artifact copy followed a swapped parent symlink."""


@pytest.mark.xfail(
    strict=True,
    raises=ProductionPathTOCTOUBug,
    reason=(
        "ZF-TEST-014: binary run-archive copy does not recheck destination "
        "after PathGuard validation"
    ),
)
def test_binary_artifact_sink_rechecks_destination_after_path_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    source = tmp_path / "source.bin"
    source.write_bytes(b"candidate-bytes")
    destination = root / "artifact.bin"
    destination.write_bytes(b"safe-placeholder")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"must-remain-outside")

    original_assert_under = run_archive.PathGuard.assert_under

    def validate_then_swap(path: Path, allowed_root: Path) -> Path:
        resolved = original_assert_under(path, allowed_root)
        if Path(path) == destination:
            destination.unlink()
            destination.symlink_to(outside)
        return resolved

    monkeypatch.setattr(
        run_archive.PathGuard,
        "assert_under",
        staticmethod(validate_then_swap),
    )

    records: list[object] = []
    try:
        run_archive._copy_file_artifact(
            records,
            source,
            destination,
            artifact_root=root,
            default="missing",
            kind="binary",
        )
    except PathGuardError:
        # A fixed implementation must reject the swapped destination before
        # reaching the copy sink.  Strict xfail turns that future pass into an
        # XPASS so the issue can be explicitly retired.
        assert outside.read_bytes() == b"must-remain-outside"
        return

    if outside.read_bytes() != b"must-remain-outside":
        raise ProductionPathTOCTOUBug(
            "binary copy followed a destination symlink after PathGuard validation"
        )
    raise ProductionPathTOCTOUBug(
        "destination replacement was accepted without a post-validation guard"
    )


@pytest.mark.xfail(
    strict=True,
    raises=ProductionFanoutPathTOCTOUBug,
    reason=(
        "ZF-TEST-015: fanout artifact relocation does not recheck destination "
        "after PathGuard validation"
    ),
)
def test_fanout_artifact_sink_rechecks_destination_after_path_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "report.json"
    source.write_bytes(b'{"status":"candidate"}')
    destination = (
        state_dir / "artifacts" / "fanouts" / "fanout-1" / "reader" / "report.json"
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"safe-placeholder")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"must-remain-outside")

    original_assert_under = fanout_artifact_refs.PathGuard.assert_under

    def validate_then_swap(path: Path, allowed_root: Path) -> Path:
        resolved = original_assert_under(path, allowed_root)
        if Path(path) == destination:
            destination.unlink()
            destination.symlink_to(outside)
        return resolved

    monkeypatch.setattr(
        fanout_artifact_refs.PathGuard,
        "assert_under",
        staticmethod(validate_then_swap),
    )

    try:
        fanout_artifact_refs._copy_relocated_source(
            ref="reports/report.json",
            source=(source, source_root, "reader"),
            fanout_id="fanout-1",
            state_dir=state_dir,
        )
    except PathGuardError:
        # A fixed implementation must reject the swapped destination before
        # reaching copyfile.  Strict xfail turns that future pass into XPASS.
        assert outside.read_bytes() == b"must-remain-outside"
        return

    if outside.read_bytes() != b"must-remain-outside":
        raise ProductionFanoutPathTOCTOUBug(
            "fanout copy followed a destination symlink after PathGuard validation"
        )
    raise ProductionFanoutPathTOCTOUBug(
        "fanout destination replacement was accepted without a post-validation guard"
    )


@pytest.mark.xfail(
    strict=True,
    raises=ProductionDirectoryPathTOCTOUBug,
    reason=(
        "ZF-TEST-016: run-archive directory copy does not recheck parent "
        "directories after PathGuard validation"
    ),
)
def test_directory_artifact_sink_rechecks_parent_after_path_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    source = tmp_path / "source-dir"
    source.mkdir()
    (source / "report.txt").write_text("candidate", encoding="utf-8")
    parent = root / "bundle"
    parent.mkdir()
    destination = parent / "artifact"
    outside = tmp_path / "outside-dir"
    outside.mkdir()

    original_assert_under = run_archive.PathGuard.assert_under

    def validate_then_swap(path: Path, allowed_root: Path) -> Path:
        resolved = original_assert_under(path, allowed_root)
        if Path(path) == destination:
            shutil.rmtree(parent)
            parent.symlink_to(outside, target_is_directory=True)
        return resolved

    monkeypatch.setattr(
        run_archive.PathGuard,
        "assert_under",
        staticmethod(validate_then_swap),
    )

    records: list[object] = []
    try:
        run_archive._copy_dir_artifact(
            records,
            source,
            destination,
            artifact_root=root,
            kind="directory",
        )
    except PathGuardError:
        # A fixed implementation must reject the swapped parent before copytree.
        assert not (outside / "artifact").exists()
        return

    if (outside / "artifact" / "report.txt").exists():
        raise ProductionDirectoryPathTOCTOUBug(
            "directory copy followed a parent symlink after PathGuard validation"
        )
    raise ProductionDirectoryPathTOCTOUBug(
        "directory destination replacement was accepted without a post-validation guard"
    )
