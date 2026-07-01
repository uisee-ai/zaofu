"""Tests for zf status command."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.main import main


def test_status_after_init(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])
    result = main(["status"])
    assert result == 0
    captured = capsys.readouterr()
    assert "session" in captured.out.lower()
    assert "event" in captured.out.lower()


def test_status_uninitalized(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = main(["status"])
    assert result == 1
    captured = capsys.readouterr()
    output = captured.out.lower() + captured.err.lower()
    assert "not initialized" in output or "zf init" in output
