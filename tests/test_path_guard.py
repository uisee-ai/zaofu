"""Tests for PathGuard and workdir ownership markers."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.safety import (
    PathGuard,
    PathGuardError,
    assert_owned_workdir,
    write_workdir_owner_marker,
)


def test_assert_under_rejects_outside_path(tmp_path: Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    with pytest.raises(PathGuardError):
        PathGuard.assert_under(outside, root)


def test_assert_under_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathGuardError):
        PathGuard.assert_under(link, root)


def test_assert_safe_symlink_rejects_escape(tmp_path: Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    with pytest.raises(PathGuardError):
        PathGuard.assert_safe_symlink(root / "link", outside)


def test_assert_not_truth_file_rejects_events_jsonl(tmp_path: Path):
    with pytest.raises(PathGuardError):
        PathGuard.assert_not_truth_file(tmp_path / ".zf" / "events.jsonl")


def test_missing_ownership_marker_rejected(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    workdir = state_dir / "workdirs" / "dev-1"
    workdir.mkdir(parents=True)

    with pytest.raises(PathGuardError):
        assert_owned_workdir(workdir, state_dir=state_dir)


def test_valid_workdir_ownership_marker_accepted(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    workdir = state_dir / "workdirs" / "dev-1"
    write_workdir_owner_marker(
        workdir,
        project_name="test",
        instance_id="dev-1",
        project_root=tmp_path,
    )

    marker = assert_owned_workdir(workdir, state_dir=state_dir)

    assert marker.project_name == "test"
    assert marker.instance_id == "dev-1"


def test_assert_disjoint_rejects_equal_and_nested_roots(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    PathGuard.assert_disjoint(source, target)

    with pytest.raises(PathGuardError):
        PathGuard.assert_disjoint(source, source)
    with pytest.raises(PathGuardError):
        PathGuard.assert_disjoint(source, source / "candidate")
    with pytest.raises(PathGuardError):
        PathGuard.assert_disjoint(target / "vendor" / "old", target)
