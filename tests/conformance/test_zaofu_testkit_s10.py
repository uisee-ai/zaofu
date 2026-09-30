"""S10 deterministic release-entry smoke tests."""

from __future__ import annotations

import sys
import tarfile
import zipfile
import shutil
from pathlib import Path

from tests.testkit.cleanup import assert_no_child_process_leaks, child_processes
from tests.testkit.command_runner import run_command


def test_zf_help_smoke_has_success_receipt_without_state_side_effect(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    receipts = tmp_path / "receipts"

    receipt = run_command(
        ["uv", "run", "zf", "--help"],
        command_id="zf-help",
        cwd=repo_root,
        output_dir=receipts,
        timeout_seconds=30,
    )

    assert receipt.passed
    assert receipt.exit_code == 0
    assert "usage" in (receipts / "zf-help.stdout.log").read_text(
        encoding="utf-8",
    ).lower()
    assert receipt.command_digest
    assert receipt.cwd == str(repo_root.resolve())
    assert not (tmp_path / ".zf").exists()


def test_repeated_release_entry_invocations_leave_no_child_processes(
    tmp_path: Path,
) -> None:
    """Repeated CLI smoke must not leak runner/provider child processes."""
    repo_root = Path(__file__).resolve().parents[2]
    receipts = tmp_path / "receipts"
    baseline = child_processes()

    for index in range(3):
        receipt = run_command(
            ["uv", "run", "zf", "--help"],
            command_id=f"zf-help-repeat-{index}",
            cwd=repo_root,
            output_dir=receipts,
            timeout_seconds=30,
        )
        assert receipt.passed
        assert receipt.exit_code == 0

    assert len(list(receipts.glob("zf-help-repeat-*.receipt.json"))) == 3
    assert_no_child_process_leaks(baseline)
    assert not (tmp_path / ".zf").exists()


def test_command_receipt_distinguishes_release_failure_from_harness_timeout(
    tmp_path: Path,
) -> None:
    receipts = tmp_path / "receipts"
    failed = run_command(
        [sys.executable, "-c", "raise SystemExit(7)"],
        command_id="release-failed",
        cwd=tmp_path,
        output_dir=receipts,
    )
    timed_out = run_command(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        command_id="release-timeout",
        cwd=tmp_path,
        output_dir=receipts,
        timeout_seconds=0.01,
    )

    assert failed.exit_code == 7
    assert not failed.passed and not failed.timed_out
    assert timed_out.exit_code is None
    assert timed_out.timed_out and not timed_out.passed
    assert (receipts / "release-failed.receipt.json").exists()
    assert (receipts / "release-timeout.receipt.json").exists()


def test_wheel_and_sdist_build_then_import_from_clean_target(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    receipts = tmp_path / "receipts"
    dist_dir = tmp_path / "dist"
    build = run_command(
        ["uv", "build", "--out-dir", str(dist_dir)],
        command_id="package-build",
        cwd=repo_root,
        output_dir=receipts,
        timeout_seconds=120,
    )

    assert build.passed
    wheel = next(dist_dir.glob("*.whl"))
    sdist = next(dist_dir.glob("*.tar.gz"))
    assert wheel.stat().st_size > 0
    assert sdist.stat().st_size > 0

    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
        entry_points_name = next(
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode("utf-8")
    assert "Requires-Python: >=3.11" in metadata
    for extra in ("dev", "web", "feishu", "stream-json"):
        assert f"Provides-Extra: {extra}" in metadata
    assert "zf = zf.cli:main" in entry_points

    with tarfile.open(sdist) as archive:
        pkg_info_name = next(
            name for name in archive.getnames() if name.endswith("/PKG-INFO")
        )
        pkg_info = archive.extractfile(pkg_info_name)
        assert pkg_info is not None
        sdist_metadata = pkg_info.read().decode("utf-8")
    assert "Requires-Python: >=3.11" in sdist_metadata
    assert "Provides-Extra: web" in sdist_metadata

    for artifact, label in ((wheel, "wheel"), (sdist, "sdist")):
        install_dir = tmp_path / f"clean-install-{label}"
        install = run_command(
            [
                "uv",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(install_dir),
                str(artifact),
            ],
            command_id=f"package-install-{label}",
            cwd=repo_root,
            output_dir=receipts,
            timeout_seconds=120,
        )
        assert install.passed

        imported = run_command(
            [
                sys.executable,
                "-c",
                "import zf; print(zf.__version__); print(zf.__file__)",
            ],
            command_id=f"package-import-{label}",
            cwd=tmp_path,
            output_dir=receipts,
            env={"PYTHONPATH": str(install_dir)},
            timeout_seconds=30,
        )
        assert imported.passed
        assert imported.exit_code == 0
        imported_stdout = (receipts / f"package-import-{label}.stdout.log").read_text(
            encoding="utf-8",
        )
        imported_path = Path(imported_stdout.splitlines()[-1]).resolve()
        assert install_dir.resolve() in imported_path.parents
        cli = run_command(
            [
                sys.executable,
                "-c",
                "from zf.cli import main; raise SystemExit(main(['--help']))",
            ],
            command_id=f"package-cli-help-{label}",
            cwd=tmp_path,
            output_dir=receipts,
            env={"PYTHONPATH": str(install_dir)},
            timeout_seconds=30,
        )
        assert cli.passed
        assert "usage" in (receipts / f"package-cli-help-{label}.stdout.log").read_text(
            encoding="utf-8",
        ).lower()
    assert (receipts / "package-build.receipt.json").exists()


def test_wheel_clean_install_matrix_covers_available_supported_pythons(
    tmp_path: Path,
) -> None:
    """The declared Python floor is exercised outside the source checkout."""
    repo_root = Path(__file__).resolve().parents[2]
    receipts = tmp_path / "receipts"
    dist_dir = tmp_path / "dist"
    build = run_command(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        command_id="matrix-wheel-build",
        cwd=repo_root,
        output_dir=receipts,
        timeout_seconds=120,
    )
    assert build.passed
    wheel = next(dist_dir.glob("*.whl"))

    interpreters = [sys.executable]
    python312 = shutil.which("python3.12")
    assert python312 is not None, "Python 3.12 is required by the declared >=3.11 matrix"
    if python312 not in interpreters:
        interpreters.append(python312)

    for index, interpreter in enumerate(interpreters):
        target = tmp_path / f"matrix-target-{index}"
        install = run_command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                interpreter,
                "--no-deps",
                "--target",
                str(target),
                str(wheel),
            ],
            command_id=f"matrix-install-{index}",
            cwd=repo_root,
            output_dir=receipts,
            timeout_seconds=120,
        )
        assert install.passed
        imported = run_command(
            [
                interpreter,
                "-c",
                "import sys, zf; print(sys.version_info[:2]); print(zf.__version__); print(zf.__file__)",
            ],
            command_id=f"matrix-import-{index}",
            cwd=tmp_path,
            output_dir=receipts,
            env={"PYTHONPATH": str(target)},
            timeout_seconds=30,
        )
        assert imported.passed
        lines = (receipts / f"matrix-import-{index}.stdout.log").read_text(
            encoding="utf-8",
        ).splitlines()
        assert lines[1] == "0.0.7"
        assert target.resolve() in Path(lines[-1]).resolve().parents
