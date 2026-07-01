"""Tests for actionable error messages across CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


class TestErrorMessages:
    """Every error path should include 'to fix:' or actionable guidance."""

    def test_validate_error_has_fix_hint(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text("version: '1.0'\n")
        main(["validate"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "to fix:" in output.lower() or "check" in output.lower()

    def test_status_not_init_has_fix(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(["status"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "zf init" in output.lower()

    def test_start_no_yaml_has_fix(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(["start", "--dry-run"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "zf init" in output.lower() or "not found" in output.lower()

    def test_stop_no_state_has_fix(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(["stop"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "zf init" in output.lower() or "not found" in output.lower()

    def test_kanban_move_invalid_has_valid_targets(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        config = {"version": "1.0", "project": {"name": "t"}}
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        main(["init"])
        main(["kanban", "add", "Task A"])

        import json
        kanban = json.loads((tmp_path / ".zf" / "kanban.json").read_text())
        task_id = kanban[0]["id"]

        main(["kanban", "move", task_id, "done"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        # Should show valid transitions
        assert "valid" in output.lower() or "in_progress" in output.lower()

    def test_kanban_show_missing_has_fix(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        config = {"version": "1.0", "project": {"name": "t"}}
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        main(["init"])
        main(["kanban", "show", "TASK-FAKE"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "to fix" in output.lower() or "list" in output.lower()

    def test_restart_unknown_role_has_available(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        config = {"version": "1.0", "project": {"name": "t"}, "roles": [{"name": "dev", "backend": "mock"}]}
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        main(["init"])
        main(["restart", "nonexistent", "--dry-run"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "dev" in output  # Should list available roles

    def test_cold_start_fail_has_fix(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
        main(["validate", "--cold-start"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "FAIL" in output
        assert "to fix" in output.lower() or "address" in output.lower()
