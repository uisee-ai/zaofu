"""Tests for zf gate CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "test"},
        "quality_gates": {
            "check_true": {"enabled": True, "required_checks": ["command_exit_zero"]},
            "check_disabled": {"enabled": False},
        },
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


class TestGateList:
    def test_list_gates(self, project_dir: Path, capsys):
        result = main(["gate", "list"])
        assert result == 0
        captured = capsys.readouterr()
        assert "check_true" in captured.out

    def test_list_shows_enabled_status(self, project_dir: Path, capsys):
        main(["gate", "list"])
        captured = capsys.readouterr()
        assert "enabled" in captured.out.lower() or "disabled" in captured.out.lower()


class TestGateRun:
    def test_run_passing_gate(self, project_dir: Path):
        """Run a gate with 'true' command should pass."""
        result = main(["gate", "run", "check_true", "--command", "true"])
        assert result == 0

    def test_run_failing_gate(self, project_dir: Path):
        result = main(["gate", "run", "check_true", "--command", "false"])
        assert result != 0

    def test_run_emits_event(self, project_dir: Path):
        import json
        main(["gate", "run", "check_true", "--command", "true"])
        events = (project_dir / ".zf" / "events.jsonl").read_text()
        assert "gate.passed" in events or "gate.failed" in events

    def test_run_nonexistent_gate(self, project_dir: Path):
        result = main(["gate", "run", "nonexistent"])
        assert result != 0

    def test_run_all_gates(self, project_dir: Path, capsys):
        result = main(["gate", "run", "all", "--command", "true"])
        assert result == 0
        captured = capsys.readouterr()
        assert "PASS" in captured.out
        assert "SKIP" in captured.out  # check_disabled should be skipped
        assert "Results:" in captured.out

    def test_run_all_with_failure(self, project_dir: Path, capsys):
        result = main(["gate", "run", "all", "--command", "false"])
        assert result != 0
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "FAIL" in output

    def test_nonexistent_gate_shows_available(self, project_dir: Path, capsys):
        main(["gate", "run", "nonexistent"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "check_true" in output  # shows available gates
