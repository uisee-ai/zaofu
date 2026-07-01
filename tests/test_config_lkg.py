"""Tests for last-known-good config snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.config.lkg import promote_last_known_good


VALID = (
    'version: "1.0"\n'
    "project:\n"
    "  name: test\n"
    "roles:\n"
    "  - name: dev\n"
    "    backend: mock\n"
)


def test_promote_last_known_good_writes_snapshot_hash_and_report(tmp_path: Path):
    config_path = tmp_path / "zf.yaml"
    state_dir = tmp_path / ".zf"
    config_path.write_text(VALID, encoding="utf-8")

    snapshot = promote_last_known_good(
        config_path=config_path,
        state_dir=state_dir,
    )

    assert snapshot.read_text(encoding="utf-8") == VALID
    assert (state_dir / "config" / "last-known-good.hash").read_text().strip()
    report = json.loads(
        (state_dir / "config" / "validation-report.json").read_text()
    )
    assert report["status"] == "valid"
    assert report["last_known_good"] == str(snapshot)


def test_invalid_validate_does_not_overwrite_lkg(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(VALID, encoding="utf-8")
    assert main(["validate"]) == 0
    snapshot = tmp_path / ".zf" / "config" / "last-known-good.yaml"
    before = snapshot.read_text(encoding="utf-8")

    config_path.write_text('version: "1.0"\nroles: []\n', encoding="utf-8")
    assert main(["validate"]) == 1

    assert snapshot.read_text(encoding="utf-8") == before
    report = json.loads(
        (tmp_path / ".zf" / "config" / "validation-report.json").read_text()
    )
    assert report["status"] == "invalid"
    assert report["errors"]


def test_start_invalid_config_does_not_use_lkg_automatically(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(VALID, encoding="utf-8")
    assert main(["init"]) == 0
    assert main(["validate"]) == 0

    config_path.write_text('version: "1.0"\nroles: []\n', encoding="utf-8")
    result = main(["start", "--dry-run"])

    assert result == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Started harness" not in output
    assert "Last-known-good" in output
    assert "not used automatically" in output
