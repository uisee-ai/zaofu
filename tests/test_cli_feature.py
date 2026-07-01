"""Tests for `zf feature` CLI (E0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main


def _init(tmp_path: Path):
    (tmp_path / ".zf").mkdir()


def test_feature_add(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    result = main(["feature", "add", "OAuth login", "--message", "implement OAuth"])
    assert result == 0
    out = capsys.readouterr().out
    assert "F-" in out
    # File written
    data = json.loads((tmp_path / ".zf" / "feature_list.json").read_text())
    assert len(data) == 1
    assert data[0]["title"] == "OAuth login"
    assert data[0]["user_message"] == "implement OAuth"


def test_feature_add_uses_project_state_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n',
        encoding="utf-8",
    )
    (tmp_path / "runtime-state").mkdir()

    result = main(["feature", "add", "Runtime feature"])

    assert result == 0
    data = json.loads((tmp_path / "runtime-state" / "feature_list.json").read_text())
    assert data[0]["title"] == "Runtime feature"
    assert not (tmp_path / ".zf").exists()


def test_feature_add_with_priority(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    main(["feature", "add", "Critical bug", "--priority", "1"])
    data = json.loads((tmp_path / ".zf" / "feature_list.json").read_text())
    assert data[0]["priority"] == 1


def test_feature_add_id_only_is_machine_readable(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)

    result = main(["feature", "add", "Machine feature", "--id-only"])

    assert result == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("F-")
    assert "feature_id=" not in out
    assert "Added" not in out


def test_feature_add_json_is_machine_readable(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)

    result = main(["feature", "add", "JSON feature", "--json"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["feature_id"].startswith("F-")
    assert payload["id"] == payload["feature_id"]
    assert payload["title"] == "JSON feature"


def test_feature_list_empty(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    result = main(["feature", "list"])
    assert result == 0


def test_feature_list_shows_features(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    main(["feature", "add", "OAuth"])
    main(["feature", "add", "Profile page"])
    capsys.readouterr()  # clear
    result = main(["feature", "list"])
    out = capsys.readouterr().out
    assert "OAuth" in out
    assert "Profile page" in out


def test_feature_list_filter_by_status(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    main(["feature", "add", "A"])
    main(["feature", "add", "B"])
    capsys.readouterr()
    main(["feature", "list", "--status", "planning"])
    out = capsys.readouterr().out
    assert "A" in out
    assert "B" in out


def test_feature_show(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    main(["feature", "add", "OAuth", "--message", "implement OAuth"])
    out = capsys.readouterr().out
    feature_id = [w.rstrip(":") for w in out.split() if w.startswith("F-")][0]
    capsys.readouterr()
    result = main(["feature", "show", feature_id])
    assert result == 0
    out = capsys.readouterr().out
    assert "OAuth" in out
    assert "implement OAuth" in out
    assert feature_id in out


def test_feature_show_missing_returns_error(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    result = main(["feature", "show", "F-doesnt-exist"])
    assert result != 0


def test_feature_update_status(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    main(["feature", "add", "OAuth"])
    out = capsys.readouterr().out
    feature_id = [w.rstrip(":") for w in out.split() if w.startswith("F-")][0]
    main(["feature", "update", feature_id, "--status", "active"])
    data = json.loads((tmp_path / ".zf" / "feature_list.json").read_text())
    assert data[0]["status"] == "active"


def test_feature_update_invalid_status_rejected(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path)
    main(["feature", "add", "A"])
    out = capsys.readouterr().out
    feature_id = [w.rstrip(":") for w in out.split() if w.startswith("F-")][0]
    result = main(["feature", "update", feature_id, "--status", "bogus"])
    assert result != 0
