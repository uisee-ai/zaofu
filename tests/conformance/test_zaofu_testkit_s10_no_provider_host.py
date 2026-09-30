"""Host-tier checks for a ZaoFu install with no Provider executable visible."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.testkit.command_runner import run_command


pytestmark = [pytest.mark.host, pytest.mark.serial]
_RUN = os.environ.get("ZF_RUN_NO_PROVIDER_MATRIX", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    return {
        "PATH": os.pathsep.join((str(tmp_path / "bin"), "/usr/bin", "/bin")),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "BROWSER": "",
    }


def _write_minimal_project(tmp_path: Path) -> Path:
    """Create a complete cold-start fixture without any Provider role."""

    project = tmp_path / "project"
    (project / ".zf").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "tests").mkdir()
    (project / "README.md").write_text("# no-provider\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    (project / "CLAUDE.md").write_text("# test\n", encoding="utf-8")
    (project / ".zf" / "events.jsonl").touch()
    (project / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: no-provider-cold-start\n"
        "  state_dir: .zf\n"
        "roles: []\n",
        encoding="utf-8",
    )
    return project


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_cli_help_remains_available_without_provider_executable(tmp_path: Path) -> None:
    """CLI discovery must not require a configured Provider executable."""

    receipts = tmp_path / "receipts"
    receipt = run_command(
        [
            sys.executable,
            "-c",
            "from zf.cli.main import main; raise SystemExit(main(['--help']))",
        ],
        command_id="no-provider-cli-help",
        cwd=Path(__file__).resolve().parents[2],
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=30,
    )

    assert receipt.passed, receipt
    assert "usage" in (receipts / "no-provider-cli-help.stdout.log").read_text(
        encoding="utf-8",
    ).lower()
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_provider_doctor_fails_closed_without_provider_and_without_state_side_effect(
    tmp_path: Path,
) -> None:
    """A missing Provider is an explicit preflight failure, not a false green."""

    receipts = tmp_path / "receipts"
    receipt = run_command(
        [
            sys.executable,
            "-c",
            "from zf.cli.main import main; "
            "raise SystemExit(main(['doctor', 'provider', '--backend', 'codex', '--json']))",
        ],
        command_id="no-provider-doctor",
        cwd=Path(__file__).resolve().parents[2],
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=30,
    )

    assert not receipt.passed
    assert receipt.exit_code == 1
    report = json.loads(
        (receipts / "no-provider-doctor.stdout.log").read_text(encoding="utf-8")
    )
    assert report["ok"] is False
    assert report["backend"] == "codex"
    assert report["codex"]["available"] is False
    assert any("command not found" in issue for issue in report["issues"])
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_validate_cold_start_runs_without_provider_and_uses_declared_state_dir(
    tmp_path: Path,
) -> None:
    """Cold-start validation must remain usable before a Provider is installed."""

    project = _write_minimal_project(tmp_path)
    receipts = tmp_path / "receipts"
    receipt = run_command(
        [
            sys.executable,
            "-c",
            "from zf.cli.main import main; raise SystemExit(main(__import__('sys').argv[1:]))",
            "validate",
            "--path",
            str(project / "zf.yaml"),
            "--cold-start",
            "--json",
        ],
        command_id="no-provider-validate-cold-start",
        cwd=Path(__file__).resolve().parents[2],
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=30,
    )

    assert receipt.passed, receipt
    report = json.loads(
        (receipts / "no-provider-validate-cold-start.stdout.log").read_text(
            encoding="utf-8",
        )
    )
    assert report["ok"] is True
    assert report["identity"]["project_root"] == str(project)
    assert report["identity"]["state_dir"] == str(project / ".zf")
    diagnostics = "\n".join(report["data"]["diagnostics"]["stdout"])
    assert "Cold-start score: 5/5" in diagnostics
    assert "Backend Isolation:" in diagnostics
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_skills_doctor_is_empty_and_successful_without_provider_or_skill_sources(
    tmp_path: Path,
) -> None:
    """An empty project must get a deterministic, side-effect-free skills report."""

    project = _write_minimal_project(tmp_path)
    receipts = tmp_path / "receipts"
    receipt = run_command(
        [
            sys.executable,
            "-c",
            "from zf.cli.main import main; raise SystemExit(main(__import__('sys').argv[1:]))",
            "skills",
            "doctor",
            "--json",
        ],
        command_id="no-provider-skills-doctor",
        cwd=project,
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=30,
    )

    assert receipt.passed, receipt
    report = json.loads(
        (receipts / "no-provider-skills-doctor.stdout.log").read_text(encoding="utf-8")
    )
    assert report["project_root"] == str(project)
    assert report["state_dir"] == str(project / ".zf")
    assert report["skill_sources"] == []
    assert report["enabled"] == []
    assert report["issues"] == []
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_dry_run_start_and_fast_stop_need_no_provider_executable(tmp_path: Path) -> None:
    """Lifecycle smoke must be available for an initialized, provider-less project."""

    project = _write_minimal_project(tmp_path)
    # init owns creation of the runtime state; keep the project config and source
    # fixture, but remove only the pre-created state directory before invoking it.
    for child in (project / ".zf").iterdir():
        child.unlink()
    (project / ".zf").rmdir()
    receipts = tmp_path / "receipts"
    script = (
        "from zf.cli.main import main; "
        "steps=[['init','--no-workspace-register','--skip-instruction-docs'],"
        "['start','--dry-run','--skip-workflow-inspect'],['stop','--fast']]; "
        "raise SystemExit(next((rc for args in steps if (rc:=main(args)) != 0), 0))"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="no-provider-lifecycle-dry-run",
        cwd=project,
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=60,
    )

    assert receipt.passed, receipt
    output = (receipts / "no-provider-lifecycle-dry-run.stdout.log").read_text(
        encoding="utf-8"
    )
    assert "Started harness (dry-run)" in output
    assert "Fast-stopped harness session" in output
    assert (project / ".zf" / "session.yaml").exists()
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_no_watch_start_and_fast_stop_complete_without_provider_workers(
    tmp_path: Path,
) -> None:
    """A provider-less project with no roles can exercise the real tmux lifecycle."""

    project = _write_minimal_project(tmp_path)
    for child in (project / ".zf").iterdir():
        child.unlink()
    (project / ".zf").rmdir()
    receipts = tmp_path / "receipts"
    script = (
        "from zf.cli.main import main; "
        "steps=[['init','--no-workspace-register','--skip-instruction-docs'],"
        "['start','--no-watch','--skip-workflow-inspect'],['stop','--fast']]; "
        "raise SystemExit(next((rc for args in steps if (rc:=main(args)) != 0), 0))"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="no-provider-lifecycle-real-tmux",
        cwd=project,
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=60,
    )

    assert receipt.passed, receipt
    output = (receipts / "no-provider-lifecycle-real-tmux.stdout.log").read_text(
        encoding="utf-8"
    )
    assert "Started harness. Session:" in output
    assert "Watcher: skipped (--no-watch)" in output
    assert "Fast-stopped harness session" in output
    assert (project / ".zf" / "session.yaml").exists()
    assert subprocess.run(
        ["tmux", "has-session", "-t", "zf"],
        check=False,
        capture_output=True,
        text=True,
    ).returncode != 0
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_read_only_status_events_and_restart_dry_run_need_no_provider(
    tmp_path: Path,
) -> None:
    """Read projections and restart planning must work before Provider setup."""

    project = _write_minimal_project(tmp_path)
    for child in (project / ".zf").iterdir():
        child.unlink()
    (project / ".zf").rmdir()
    receipts = tmp_path / "receipts"
    script = (
        "from zf.cli.main import main; "
        "steps=[['init','--no-workspace-register','--skip-instruction-docs'],"
        "['status','--json'],['events','--json','--last','5'],['restart','--dry-run']]; "
        "raise SystemExit(next((rc for args in steps if (rc:=main(args)) != 0), 0))"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="no-provider-read-only-commands",
        cwd=project,
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=60,
    )

    assert receipt.passed, receipt
    output = (receipts / "no-provider-read-only-commands.stdout.log").read_text(
        encoding="utf-8"
    )
    assert '"command": "status"' in output
    assert '"event_count": 1' in output
    assert '"command": "events"' in output
    assert "Full restart completed." in output
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_cli_read_only_help_surface_needs_no_provider_or_browser(tmp_path: Path) -> None:
    """Every read-only command surface remains discoverable before setup."""

    commands = (
        "config",
        "workflow",
        "rules",
        "presets",
        "project",
        "trace",
        "doctor",
        "skills",
        "kanban",
        "status",
        "events",
        "profile",
        "check",
        "agents",
        "state",
        "projection",
        "report",
        "metrics",
        "workspace",
    )
    receipts = tmp_path / "receipts"
    script = (
        "import json; from zf.cli.main import main; "
        f"commands={commands!r}; results=[]; "
        "\nfor command in commands:\n"
        "    try:\n"
        "        code=main([command, '--help'])\n"
        "    except SystemExit as exc:\n"
        "        code=exc.code if isinstance(exc.code, int) else 1\n"
        "    results.append({'command': command, 'code': code})\n"
        "print('RESULTS='+json.dumps(results)); raise SystemExit(1 if any(item['code'] != 0 for item in results) else 0)"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="no-provider-cli-help-surface",
        cwd=Path(__file__).resolve().parents[2],
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=60,
    )

    assert receipt.passed, receipt
    output = (receipts / "no-provider-cli-help-surface.stdout.log").read_text(
        encoding="utf-8"
    )
    report_line = next(
        line for line in reversed(output.splitlines()) if line.startswith("RESULTS=")
    )
    report = json.loads(report_line.removeprefix("RESULTS="))
    assert {item["command"] for item in report} == set(commands)
    assert all(item["code"] == 0 for item in report)
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_no_provider_discovery_commands_have_deterministic_success_and_no_escape(
    tmp_path: Path,
) -> None:
    """Project discovery/read-only commands must work before Provider setup."""

    project = _write_minimal_project(tmp_path)
    receipts = tmp_path / "receipts"
    commands = (
        ("profile-detect", ["profile", "detect", ".", "--json"]),
        ("profile-recommend", ["profile", "recommend", ".", "--json"]),
        (
            "config-inspect",
            ["config", "inspect", "--config", "zf.yaml", "--format", "json"],
        ),
        ("rules-list", ["rules"]),
        ("presets-list", ["presets"]),
        ("skills-list", ["skills", "list", "--json"]),
    )
    script = (
        "import json; from zf.cli.main import main; "
        f"commands={commands!r}; results=[]; "
        "\nfor command_id, args in commands:\n"
        "    try:\n"
        "        code=main(args)\n"
        "    except SystemExit as exc:\n"
        "        code=exc.code if isinstance(exc.code, int) else 1\n"
        "    results.append({'id': command_id, 'code': code})\n"
        "print('RESULTS='+json.dumps(results)); "
        "raise SystemExit(1 if any(item['code'] != 0 for item in results) else 0)"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="no-provider-discovery-command-matrix",
        cwd=project,
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=60,
    )

    assert receipt.passed, receipt
    output = (receipts / "no-provider-discovery-command-matrix.stdout.log").read_text(
        encoding="utf-8",
    )
    report_line = next(
        line for line in reversed(output.splitlines()) if line.startswith("RESULTS=")
    )
    report = json.loads(report_line.removeprefix("RESULTS="))
    assert {item["id"] for item in report} == {
        "profile-detect",
        "profile-recommend",
        "config-inspect",
        "rules-list",
        "presets-list",
        "skills-list",
    }
    assert all(item["code"] == 0 for item in report)
    assert '"schema": "project-profile.v1"' in output
    assert '"recommendation"' in output
    assert '"skill_sources": []' in output
    assert not (tmp_path / ".zf").exists()


@pytest.mark.skipif(
    not _RUN,
    reason="set ZF_RUN_NO_PROVIDER_MATRIX=1 for the no-Provider host tier",
)
def test_no_provider_initialized_projection_command_matrix_is_read_only(
    tmp_path: Path,
) -> None:
    """Initialized projections and diagnostics must remain usable without workers."""

    project = _write_minimal_project(tmp_path)
    for child in (project / ".zf").iterdir():
        child.unlink()
    (project / ".zf").rmdir()
    receipts = tmp_path / "receipts"
    script = (
        "from zf.cli.main import main; "
        "steps=[['init','--no-workspace-register','--skip-instruction-docs'],"
        "['status','--json'],['events','--json','--last','5'],"
        "['kanban','--board','--json'],['projection','status','--json'],"
        "['projection','doctor','--json'],['state','clean','--dry-run'],"
        "['metrics','snapshot','--format','json']]; "
        "raise SystemExit(next((rc for args in steps if (rc:=main(args)) != 0), 0))"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="no-provider-projection-command-matrix",
        cwd=project,
        output_dir=receipts,
        env=_isolated_env(tmp_path),
        timeout_seconds=60,
    )

    assert receipt.passed, receipt
    output = (
        receipts / "no-provider-projection-command-matrix.stdout.log"
    ).read_text(encoding="utf-8")
    for marker in (
        '"command": "status"',
        '"command": "events"',
        '"command": "kanban.board"',
        '"schema_version": "projection-status.v2"',
        '"schema_version": "projection-doctor.v1"',
        "Rebuildable delete candidates:",
        '"events_considered": 1',
    ):
        assert marker in output
    assert (project / ".zf" / "session.yaml").exists()
    assert (project / ".zf" / "events.jsonl").exists()
    assert not (tmp_path / ".zf").exists()
