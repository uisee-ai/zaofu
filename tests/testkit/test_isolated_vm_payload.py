"""Payload boundary contracts; real relocation is an explicit preparation probe."""

from pathlib import Path

import pytest

from tests.testkit.isolated_vm_payload import REPO, TEST_FILES, _copy_tree, build_payload


def test_interpreter_copy_keeps_internal_links_but_not_host_site_packages(tmp_path: Path) -> None:
    source = tmp_path / "python"
    source.mkdir()
    (source / "python3.11").write_bytes(b"stand-in interpreter")
    (source / "python3").symlink_to("python3.11")
    (source / "site-packages").mkdir()
    (source / "site-packages/host-hook.pth").write_text("import should_not_run")
    (source / "__pycache__").mkdir()
    (source / "__pycache__/stale.pyc").write_bytes(b"stale")
    destination = tmp_path / "copy"
    _copy_tree(source, destination, interpreter=True)
    assert (destination / "python3").is_symlink()
    assert (destination / "python3").resolve() == destination / "python3.11"
    assert (destination / "python3").read_bytes() == b"stand-in interpreter"
    assert not (destination / "site-packages").exists()
    assert not (destination / "__pycache__").exists()


def test_external_link_is_rejected_before_copying_anything(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "unrelated").write_text("unrelated")
    (source / "escape").symlink_to(tmp_path / "unrelated")
    destination = tmp_path / "destination"
    with pytest.raises(ValueError, match="external or absolute symlink"):
        _copy_tree(source, destination)
    assert not destination.exists()
    assert (tmp_path / "unrelated").read_text() == "unrelated"


def test_absolute_internal_link_cannot_retain_host_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "target").write_text("internal")
    (source / "link").symlink_to(source / "target")
    with pytest.raises(ValueError, match="absolute symlink"):
        _copy_tree(source, tmp_path / "copy")
    assert not (tmp_path / "copy").exists()


def test_builder_rejects_workspace_output_without_mutating_it(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="fresh /tmp"):
        build_payload(tmp_path / "payload")
    assert list(tmp_path.iterdir()) == before


def test_explicit_fixture_allowlist_does_not_include_runtime_or_auth() -> None:
    assert len(TEST_FILES) == len(set(TEST_FILES))
    for relative in TEST_FILES:
        assert relative.startswith("tests/") and relative.endswith(".py")
        assert (REPO / relative).is_file() and not (REPO / relative).is_symlink()
