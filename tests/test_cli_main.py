"""Tests for CLI entry point."""

from __future__ import annotations

from zf.cli.main import main


def test_version_flag(capsys):
    """zf --version prints version string."""
    try:
        main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    captured = capsys.readouterr()
    assert "0.1.0" in captured.out


def test_no_args_prints_help(capsys):
    """zf with no args prints help and exits 0."""
    result = main([])
    assert result == 0
    captured = capsys.readouterr()
    assert "zf" in captured.out


def test_version_importable():
    """Package version is importable."""
    import zf
    assert zf.__version__ == "0.1.0"
