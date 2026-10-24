"""Tests for CLI entry point."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
import zf

from zf.cli.main import build_parser, main


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("command", ["hook-recv", "artifact", "result", "guard"])
def test_hot_command_help_does_not_import_unrelated_services(command):
    code = """
import sys
from zf.cli.main import main
try:
    main()
except SystemExit as exc:
    assert exc.code == 0
assert 'zf.cli.start' not in sys.modules
assert 'zf.cli.web' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code, command, "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert f"zf {command}" in result.stdout


@pytest.mark.parametrize("argv", [
    ["artifact", "read", "--attempt", "attempt-1", "--required"],
    ["result", "validate", "--operation", "op-1", "--scratch"],
    ["guard", "ownership", "--task", "T1", "--actor", "dev"],
])
def test_hot_command_parser_preserves_public_arguments(argv):
    assert vars(build_parser(command=argv[0]).parse_args(argv)) == vars(
        build_parser().parse_args(argv)
    )


def test_hot_command_dispatch_and_argument_errors(monkeypatch):
    from zf.cli import guard

    monkeypatch.setattr(guard, "_run_ownership", lambda args: 17)
    assert main(["guard", "ownership", "--task", "T1", "--actor", "dev"]) == 17
    with pytest.raises(SystemExit) as error:
        main(["guard", "ownership", "--unknown"])
    assert error.value.code == 2


def test_version_flag(capsys):
    """zf --version prints version string."""
    try:
        main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    captured = capsys.readouterr()
    assert zf.__version__ in captured.out


def test_no_args_prints_help(capsys):
    """zf with no args prints help and exits 0."""
    result = main([])
    assert result == 0
    captured = capsys.readouterr()
    assert "zf" in captured.out


def test_version_importable():
    """Package version is importable."""
    assert zf.__version__ == "0.0.7"


def test_public_version_metadata_is_aligned():
    """Python and Web public package metadata expose the same version."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    web_package = json.loads(
        (ROOT / "web" / "package.json").read_text(encoding="utf-8")
    )
    web_lock = json.loads(
        (ROOT / "web" / "package-lock.json").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["version"] == zf.__version__
    assert web_package["version"] == zf.__version__
    assert web_lock["version"] == zf.__version__
    assert web_lock["packages"][""]["version"] == zf.__version__
