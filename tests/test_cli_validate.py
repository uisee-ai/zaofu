"""Tests for zf validate command."""

from __future__ import annotations

from pathlib import Path
from unittest import mock
import json
import os

from zf.cli.main import main


def test_validate_valid_config(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\nroles:\n  - name: dev\n    backend: python\n    model: x\n'
    )
    result = main(["validate"])
    assert result == 0
    captured = capsys.readouterr()
    assert "valid" in captured.out.lower() or "ok" in captured.out.lower()


def test_validate_warns_when_owner_visible_feishu_target_missing(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZF_OWNER_VISIBLE_CHAT", raising=False)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\nroles:\n  - name: dev\n    backend: python\n'
    )

    result = main(["validate"])

    assert result == 0
    captured = capsys.readouterr()
    assert "owner-visible delivery warnings" in captured.err.lower()
    assert "ZF_OWNER_VISIBLE_CHAT" in captured.err


def test_validate_owner_visible_warning_uses_config_path_env(
    tmp_path: Path, monkeypatch, capsys
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\nroles:\n  - name: dev\n    backend: python\n'
    )
    (project / ".env").write_text("ZF_OWNER_VISIBLE_CHAT=oc_owner\n")

    # validate loads project/.env via _load_env_file, which writes os.environ
    # directly. monkeypatch.delenv on a missing key registers NO undo, so the
    # loaded ZF_OWNER_VISIBLE_CHAT=oc_owner leaked process-wide and flipped
    # owner-visible delivery to "configured" in later tests (test_tick_services /
    # test_web_headless owner-visible no-target assertions — 2026-07-03 triage,
    # same trap test_cli_web.py already documents). Snapshot os.environ so the
    # .env load is undone at test exit.
    with mock.patch.dict(os.environ):
        os.environ.pop("ZF_OWNER_VISIBLE_CHAT", None)
        result = main(["validate", "--path", str(project / "zf.yaml")])

        assert result == 0
        captured = capsys.readouterr()
        assert "owner-visible delivery warnings" not in captured.err.lower()


def test_validate_invalid_config(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\norchestrator:\n  backend: python\n')
    result = main(["validate"])
    assert result == 1
    captured = capsys.readouterr()
    output = captured.out.lower() + captured.err.lower()
    assert "project" in output or "error" in output


def test_validate_custom_path(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "custom.yaml"
    custom.write_text('version: "1.0"\nproject:\n  name: custom\n')
    result = main(["validate", "--path", str(custom)])
    assert result == 0


def test_validate_required_remote_policy_requires_origin(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  git:\n"
        "    remote_policy: required\n"
    )

    result = main(["validate"])

    assert result == 1
    captured = capsys.readouterr()
    assert "remote policy errors" in captured.err.lower()
    assert "remote_policy=required" in captured.err
    assert "origin" in captured.err


def test_validate_cold_start_required_remote_policy_requires_origin(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  git:\n"
        "    remote_policy: required\n"
    )

    result = main(["validate", "--cold-start"])

    assert result == 1
    captured = capsys.readouterr()
    assert "remote policy errors" in captured.err.lower()
    assert "origin" in captured.err


def test_validate_missing_file(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = main(["validate"])
    assert result == 1


def test_validate_warns_for_missing_enabled_skill(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: python\n"
        "    skills: [missing]\n"
    )

    result = main(["validate"])

    assert result == 0
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert "missing" in captured.err


def test_validate_fails_for_missing_enabled_skill_in_strict_mode(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  skills:\n"
        "    strict: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: python\n"
        "    skills: [missing]\n"
    )

    result = main(["validate"])

    assert result == 1
    captured = capsys.readouterr()
    assert "skill validation errors" in captured.err.lower()
    assert "missing" in captured.err


def test_validate_strict_skills_flag_fails_for_missing_enabled_skill(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: python\n"
        "    skills: [missing]\n"
    )

    result = main(["validate", "--strict-skills"])

    assert result == 1
    captured = capsys.readouterr()
    assert "skill validation errors" in captured.err.lower()
    assert "missing" in captured.err


def test_validate_strict_contracts_fails_for_incomplete_task(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "  state_dir: .zf\n"
        "verification:\n"
        "  contract:\n"
        "    required: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: python\n"
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text(
        json.dumps([
            {
                "id": "T1",
                "title": "bad",
                "status": "backlog",
                "priority": 3,
                "assigned_to": None,
                "skills_required": [],
                "blocked_by": [],
                "contract": {
                    "behavior": "do it",
                    "verification": "",
                    "verification_tiers": [],
                    "scope": [],
                    "exclusions": [],
                    "acceptance": "exit_code=0",
                    "owner_role": "",
                    "owner_instance": "",
                    "wave": 0,
                    "shared_files": [],
                    "exclusive_files": [],
                    "handoff_artifacts": [],
                    "rework_to": "",
                },
                "evidence": None,
                "created_at": "2026-05-11T00:00:00+00:00",
                "dispatched_at": None,
                "started_at": None,
                "completed_at": None,
                "cancelled_at": None,
                "active_dispatch_id": "",
                "retry_count": 0,
                "blocked_reason": "",
            }
        ]),
        encoding="utf-8",
    )

    result = main(["validate", "--strict-contracts"])

    assert result == 1
    captured = capsys.readouterr()
    assert "strict contract errors" in captured.err.lower()
    assert "verification is required" in captured.err


def test_validate_cold_start_passing(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    (tmp_path / "CLAUDE.md").write_text("# Project")
    (tmp_path / "README.md").write_text("# README")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    main(["init"])
    result = main(["validate", "--cold-start"])
    assert result == 0
    captured = capsys.readouterr()
    assert "Cold-start score" in captured.out
    assert "PASS" in captured.out


def test_validate_cold_start_failing(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    # No CLAUDE.md, no src/, no tests/, no init
    result = main(["validate", "--cold-start"])
    assert result == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
