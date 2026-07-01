"""Tests for remaining CLI commands: attach, logs, rules, check, cleanup, agents, validate flags."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "test", "state_dir": ".zf"},
        "session": {"tmux_session": "test-zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "quality_gates": {"static": {"enabled": True}},
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    (tmp_path / "CLAUDE.md").write_text("# ZaoFu\n\nProject instructions.\n\nDetails here.\nMore lines.\n")
    (tmp_path / "README.md").write_text("# README\n\nProject description here.\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    main(["init"])
    return tmp_path


class TestAttach:
    def test_registered(self):
        with pytest.raises(SystemExit) as exc:
            main(["attach", "--help"])
        assert exc.value.code == 0


class TestLogs:
    def test_no_logs(self, project: Path, capsys):
        result = main(["logs"])
        assert result == 0
        captured = capsys.readouterr()
        assert "no log" in captured.out.lower()

    def test_with_log_file(self, project: Path, capsys):
        log_dir = project / ".zf" / "logs"
        (log_dir / "dev.log").write_text("line 1\nline 2\nline 3\n")
        result = main(["logs"])
        assert result == 0
        captured = capsys.readouterr()
        assert "dev" in captured.out

    def test_logs_uses_project_state_dir(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        config = {
            "version": "1.0",
            "project": {"name": "test", "state_dir": "runtime-state"},
            "roles": [{"name": "dev", "backend": "mock"}],
        }
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        log_dir = tmp_path / "runtime-state" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "dev.log").write_text("runtime log\n")

        result = main(["logs", "dev"])

        assert result == 0
        assert "runtime log" in capsys.readouterr().out
        assert not (tmp_path / ".zf").exists()

    def test_specific_role_log(self, project: Path, capsys):
        log_dir = project / ".zf" / "logs"
        (log_dir / "dev.log").write_text("dev output here\n")
        result = main(["logs", "dev"])
        assert result == 0
        captured = capsys.readouterr()
        assert "dev output" in captured.out

    def test_unknown_role_log(self, project: Path):
        result = main(["logs", "nonexistent"])
        assert result != 0


class TestOperatorDiagnostics:
    def test_doctor_workdirs_ok(self, project: Path, capsys):
        result = main(["doctor", "workdirs"])
        assert result == 0
        captured = capsys.readouterr()
        assert "OK: workdirs" in captured.out

    def test_refs_verify_ok_without_refs(self, project: Path, capsys):
        result = main(["refs", "verify"])
        assert result == 0
        captured = capsys.readouterr()
        assert "OK: refs" in captured.out


class TestSkillsCli:
    def test_skills_doctor_ok_for_local_skill(self, project: Path, capsys):
        skill_dir = project / "skills" / "scan"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: scan\n"
            "description: scan repo\n"
            "---\n"
            "# Scan\n"
        )
        data = yaml.safe_load((project / "zf.yaml").read_text())
        data["roles"][0]["skills"] = ["scan"]
        (project / "zf.yaml").write_text(yaml.dump(data))

        result = main(["skills", "doctor"])

        assert result == 0
        captured = capsys.readouterr()
        assert "OK: skills" in captured.out

    def test_skills_doctor_reports_missing_skill(self, project: Path, capsys):
        data = yaml.safe_load((project / "zf.yaml").read_text())
        data["roles"][0]["skills"] = ["missing"]
        (project / "zf.yaml").write_text(yaml.dump(data))

        result = main(["skills", "doctor"])

        assert result == 1
        captured = capsys.readouterr()
        assert "missing" in captured.out

    def test_skills_list_json(self, project: Path, capsys):
        result = main(["skills", "list", "--json"])

        assert result == 0
        captured = capsys.readouterr()
        assert '"enabled"' in captured.out


class TestRules:
    def test_rules_list(self, project: Path, capsys):
        result = main(["rules"])
        assert result == 0
        captured = capsys.readouterr()
        assert "static" in captured.out

    def test_rules_promoted_empty(self, project: Path, capsys):
        result = main(["rules", "promoted"])
        assert result == 0
        captured = capsys.readouterr()
        assert "no promoted" in captured.out.lower()

    def test_rules_promoted_with_data(self, project: Path, capsys):
        from zf.core.verification.promoted_rules import PromotedRulesStore
        store = PromotedRulesStore(project / ".zf" / "promoted_rules.jsonl")
        store.add("style", "grep -r TODO src/", "Remove TODOs")
        result = main(["rules", "promoted"])
        assert result == 0
        captured = capsys.readouterr()
        assert "style" in captured.out


class TestCheck:
    def test_doc_sync_passing(self, project: Path, capsys):
        result = main(["check", "doc-sync"])
        assert result == 0
        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_doc_sync_missing(self, project: Path, capsys):
        (project / "README.md").unlink()
        result = main(["check", "doc-sync"])
        assert result == 1
        captured = capsys.readouterr()
        assert "MISSING" in captured.out

    def test_clean_state(self, project: Path, capsys):
        result = main(["check", "clean-state"])
        captured = capsys.readouterr()
        assert "PASS" in captured.out or "FAIL" in captured.out

    def test_clean_state_uses_project_state_dir(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ):
        monkeypatch.chdir(tmp_path)
        config = {
            "version": "1.0",
            "project": {"name": "test", "state_dir": "runtime-state"},
            "roles": [{"name": "dev", "backend": "mock"}],
        }
        (tmp_path / "zf.yaml").write_text(yaml.dump(config), encoding="utf-8")
        (tmp_path / "runtime-state").mkdir()

        result = main(["check", "clean-state"])

        captured = capsys.readouterr()
        assert result in {0, 1}
        assert "state_initialized: OK" in captured.out
        assert not (tmp_path / ".zf").exists()


class TestCleanup:
    def test_cleanup_nothing(self, project: Path, capsys):
        result = main(["cleanup"])
        assert result == 0
        captured = capsys.readouterr()
        assert "nothing" in captured.out.lower()

    def test_cleanup_periodic(self, project: Path, capsys):
        result = main(["cleanup", "--periodic"])
        assert result == 0

    def test_cleanup_rejects_checkpoints_symlink_escape(
        self, project: Path, tmp_path: Path
    ):
        outside = tmp_path / "outside-checkpoints"
        outside.mkdir()
        checkpoints = project / ".zf" / "checkpoints"
        checkpoints.symlink_to(outside, target_is_directory=True)

        result = main(["cleanup"])

        assert result == 1
        assert outside.exists()


class TestStateClean:
    def test_state_clean_dry_run_respects_configured_state_dir(
        self, tmp_path: Path, monkeypatch, capsys,
    ):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "runtime" / "zf-state"
        (state_dir / "logs").mkdir(parents=True)
        (state_dir / "events.jsonl").write_text('{"type":"loop.started"}\n')
        (tmp_path / "zf.yaml").write_text(yaml.dump({
            "version": "1.0",
            "project": {"name": "test", "state_dir": "runtime/zf-state"},
            "roles": [{"name": "dev", "backend": "mock"}],
        }))

        result = main(["state", "clean", "--dry-run"])

        assert result == 0
        captured = capsys.readouterr()
        assert str(state_dir) in captured.out
        assert str(state_dir / "logs") in captured.out


class TestAgents:
    def test_agents_list(self, capsys):
        result = main(["agents"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Claude Code" in captured.out
        assert "Codex" in captured.out


class TestValidateFlags:
    def test_validate_architecture(self, project: Path, capsys):
        result = main(["validate", "--architecture"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        # No ARCHITECTURE_RULES.md, should skip gracefully
        assert "no" in output.lower() or "skip" in output.lower()

    def test_validate_architecture_with_rules(self, project: Path, capsys):
        (project / "ARCHITECTURE_RULES.md").write_text(
            "## Rule: check-true\n- check: `true`\n"
        )
        result = main(["validate", "--architecture"])
        assert result == 0
        captured = capsys.readouterr()
        assert "PASS" in captured.out

    def test_validate_instructions(self, project: Path, capsys):
        result = main(["validate", "--instructions"])
        assert result == 0
        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_validate_instructions_missing(self, project: Path, capsys):
        (project / "CLAUDE.md").unlink()
        result = main(["validate", "--instructions"])
        assert result == 1

    def test_validate_instructions_missing_agents_md(self, project: Path, capsys):
        (project / "AGENTS.md").unlink()
        result = main(["validate", "--instructions"])
        assert result == 1
