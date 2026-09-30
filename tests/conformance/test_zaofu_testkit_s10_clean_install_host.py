"""Explicit clean-venv installation checks for the ZaoFu release artifact."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.testkit.command_runner import run_command


pytestmark = pytest.mark.host
_RUN = os.environ.get("ZF_RUN_CLEAN_INSTALL_MATRIX", "").strip().lower() in {"1", "true", "yes"}


def _discover_interpreters() -> list[str]:
    """Return sys.executable plus available CPython 3.12 and 3.13 interpreters.

    Python 3.13 may be managed by uv and not exposed as ``python3.13`` on PATH;
    fall back to ``uv python find 3.13`` in that case.
    """
    interpreters = [sys.executable]
    for name in ("python3.12", "python3.13"):
        path = shutil.which(name)
        if path and path not in interpreters:
            interpreters.append(path)
    if not any("3.13" in p for p in interpreters):
        try:
            result = subprocess.run(
                ["uv", "python", "find", "3.13"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                if path and path not in interpreters:
                    interpreters.append(path)
        except Exception:
            pass
    return interpreters


@pytest.mark.skipif(not _RUN, reason="set ZF_RUN_CLEAN_INSTALL_MATRIX=1 for clean release installs")
@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-046: base wheel omits runtime httpx dependency; installed zf --help fails",
)
@pytest.mark.serial
def test_clean_venv_install_matrix_runs_cli_and_declared_extras(tmp_path: Path) -> None:
    """Install the wheel with each declared extra without source-path help."""

    repo_root = Path(__file__).resolve().parents[2]
    receipts = tmp_path / "receipts"
    dist = tmp_path / "dist"
    build = run_command(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        command_id="clean-wheel-build",
        cwd=repo_root,
        output_dir=receipts,
        timeout_seconds=180,
    )
    assert build.passed, build
    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    interpreters = _discover_interpreters()

    failures: list[str] = []
    for interpreter_index, interpreter in enumerate(interpreters):
        isolated_home = tmp_path / f"home-{interpreter_index}"
        isolated_home.mkdir()
        clean_env = {
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
            "HOME": str(isolated_home),
            "XDG_CONFIG_HOME": str(isolated_home / "config"),
            "XDG_CACHE_HOME": str(isolated_home / "cache"),
        }
        for extra in ("base", "dev", "web", "stream-json", "feishu"):
            env_dir = tmp_path / f"venv-{interpreter_index}-{extra}"
            # Keep system utilities available, but exclude user-managed
            # provider/browser shims (for example ~/.nvm/bin/codex).
            clean_env["PATH"] = os.pathsep.join((str(env_dir / "bin"), "/usr/bin", "/bin"))
            clean_env["BROWSER"] = ""
            venv = run_command(
                ["uv", "venv", "--python", interpreter, str(env_dir)],
                command_id=f"venv-{interpreter_index}-{extra}",
                cwd=tmp_path,
                output_dir=receipts,
                timeout_seconds=60,
            )
            assert venv.passed, venv
            python = env_dir / "bin" / "python"
            assert python.is_file(), python
            requirement = str(wheel) if extra == "base" else f"{wheel}[{extra}]"
            install = run_command(
                ["uv", "pip", "install", "--python", str(python), requirement],
                command_id=f"install-{interpreter_index}-{extra}",
                cwd=tmp_path,
                output_dir=receipts,
                timeout_seconds=300,
            )
            assert install.passed, install
            check = run_command(
                [
                    str(python), "-c",
                    "import sys, zf; from zf.cli import main; "
                    "assert str(__import__('zf').__file__).startswith(sys.prefix); "
                    "raise SystemExit(main(['--help']))",
                ],
                command_id=f"cli-{interpreter_index}-{extra}",
                cwd=tmp_path,
                output_dir=receipts,
                env=clean_env,
                timeout_seconds=60,
            )
            if not check.passed:
                failures.append(f"{interpreter} {extra}: exit={check.exit_code}")
                continue
            assert "usage" in (receipts / f"cli-{interpreter_index}-{extra}.stdout.log").read_text(
                encoding="utf-8"
            ).lower()
            assert not (tmp_path / ".zf").exists()
        assert not (isolated_home / ".zf").exists()
    assert not failures, "clean install CLI failures: " + ", ".join(failures)


@pytest.mark.skipif(not _RUN, reason="set ZF_RUN_CLEAN_INSTALL_MATRIX=1 for clean release installs")
@pytest.mark.serial
def test_clean_sdist_install_runs_validate_doctor_start_stop(tmp_path: Path) -> None:
    """Run the installed CLI lifecycle without importing from the source tree."""

    repo_root = Path(__file__).resolve().parents[2]
    receipts = tmp_path / "receipts-sdist-smoke"
    dist = tmp_path / "dist-sdist"
    build = run_command(
        ["uv", "build", "--sdist", "--out-dir", str(dist)],
        command_id="clean-sdist-build",
        cwd=repo_root,
        output_dir=receipts,
        timeout_seconds=180,
    )
    assert build.passed, build
    sdists = sorted(dist.glob("*.tar.gz"))
    assert len(sdists) == 1
    sdist = sdists[0]

    interpreters = _discover_interpreters()

    for interpreter_index, interpreter in enumerate(interpreters):
        isolated_home = tmp_path / f"sdist-home-{interpreter_index}"
        isolated_home.mkdir()
        clean_env = {
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
            "HOME": str(isolated_home),
            "XDG_CONFIG_HOME": str(isolated_home / "config"),
            "XDG_CACHE_HOME": str(isolated_home / "cache"),
        }
        env_dir = tmp_path / f"sdist-venv-{interpreter_index}"
        # The installed CLI lifecycle must not discover a user Provider
        # executable or browser launcher through inherited PATH state.
        clean_env["PATH"] = os.pathsep.join((str(env_dir / "bin"), "/usr/bin", "/bin"))
        clean_env["BROWSER"] = ""
        venv = run_command(
            ["uv", "venv", "--python", interpreter, str(env_dir)],
            command_id=f"sdist-venv-{interpreter_index}",
            cwd=tmp_path,
            output_dir=receipts,
            timeout_seconds=60,
        )
        assert venv.passed, venv
        python = env_dir / "bin" / "python"
        zf = env_dir / "bin" / "zf"
        assert python.is_file(), python
        install = run_command(
            ["uv", "pip", "install", "--python", str(python), f"{sdist}[dev]"],
            command_id=f"sdist-install-{interpreter_index}",
            cwd=tmp_path,
            output_dir=receipts,
            timeout_seconds=300,
        )
        assert install.passed, install
        assert zf.is_file(), zf

        project = tmp_path / f"installed-project-{interpreter_index}"
        project.mkdir()
        init = run_command(
            [
                str(zf),
                "init",
                "--no-workspace-register",
                "--no-git-hooks",
                "--skip-instruction-docs",
                "--preset",
                "minimal",
            ],
            command_id=f"sdist-init-{interpreter_index}",
            cwd=project,
            output_dir=receipts,
            env=clean_env,
            timeout_seconds=60,
        )
        assert init.passed, init

        session_name = f"zf-sdist-smoke-{interpreter_index}-{tmp_path.name}"
        (project / "zf.yaml").write_text(
            "version: '1.0'\n"
            "project:\n"
            "  name: zf-sdist-smoke\n"
            "  state_dir: .zf\n"
            "session:\n"
            f"  tmux_session: {session_name}\n"
            "orchestrator:\n"
            "  backend: mock\n"
            "roles:\n"
            "  - name: dev\n"
            "    backend: mock\n"
            "    permission_mode: bypass\n"
            "    triggers:\n"
            "      - task.assigned\n"
            "    publishes:\n"
            "      - dev.build.done\n",
            encoding="utf-8",
        )
        for filename in ("README.md", "AGENTS.md", "CLAUDE.md"):
            (project / filename).write_text("# release smoke\n", encoding="utf-8")
        (project / "src").mkdir()
        (project / "tests").mkdir()

        commands = (
            ("validate", [str(zf), "validate", "--cold-start"]),
            ("doctor", [str(zf), "doctor"]),
            (
                "start",
                [str(zf), "start", "--dry-run", "--skip-workflow-inspect"],
            ),
            ("stop", [str(zf), "stop", "--fast"]),
        )
        for label, command in commands:
            receipt = run_command(
                command,
                command_id=f"sdist-{label}-{interpreter_index}",
                cwd=project,
                output_dir=receipts,
                env=clean_env,
                timeout_seconds=120,
            )
            assert receipt.passed, receipt

        validate_stdout = (
            receipts / f"sdist-validate-{interpreter_index}.stdout.log"
        ).read_text(encoding="utf-8")
        assert "Cold-start score: 5/5" in validate_stdout
        assert (project / ".zf" / "events.jsonl").is_file()
        assert not (isolated_home / ".zf").exists()
