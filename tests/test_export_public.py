from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "export-public.sh"
NO_RG_COMMANDS = (
    "cat",
    "date",
    "dirname",
    "find",
    "git",
    "grep",
    "mkdir",
    "mktemp",
    "perl",
    "realpath",
    "rm",
    "sed",
    "tar",
    "xargs",
)


def _restricted_path(root: Path) -> str:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    for command in NO_RG_COMMANDS:
        executable = shutil.which(command)
        assert executable is not None, f"required test command is missing: {command}"
        (bin_dir / command).symlink_to(executable)
    return str(bin_dir)


def _run_export(
    script: Path,
    source_root: Path,
    target: Path,
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), "--target", str(target), "--ref", "HEAD"],
        cwd=source_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _init_fixture_repo(root: Path, manual_name: str, manual_text: str) -> Path:
    source = root / "source"
    (source / "tools").mkdir(parents=True)
    (source / "docs" / "manual").mkdir(parents=True)
    shutil.copy2(SCRIPT, source / "tools" / "export-public.sh")
    (source / "README.md").write_text("# Public fixture\n", encoding="utf-8")
    (source / "README.zh-CN.md").write_text("# Public fixture\n", encoding="utf-8")
    (source / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    (source / "DISCLAIMER.md").write_text("fixture disclaimer\n", encoding="utf-8")
    (source / "docs" / "manual" / manual_name).write_text(manual_text, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "add",
            "--",
            "README.md",
            "README.zh-CN.md",
            "LICENSE",
            "DISCLAIMER.md",
            "tools/export-public.sh",
            f"docs/manual/{manual_name}",
        ],
        cwd=source,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=source,
        check=True,
    )
    return source


def test_export_includes_disclaimer_and_accepts_no_private_matches(tmp_path: Path) -> None:
    for name, path_value in (
        ("default", os.environ["PATH"]),
        ("grep", _restricted_path(tmp_path / "restricted")),
    ):
        target = tmp_path / f"target-{name}"
        env = os.environ.copy()
        env["PATH"] = path_value
        env["ZF_EXPORT_PRIVATE_RG_PATTERN"] = f"__ZF_ABSENT_{tmp_path.name}_{name}__"
        result = _run_export(SCRIPT, ROOT, target, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (target / "LICENSE").is_file()
        expected_disclaimer = (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8")
        assert (target / "DISCLAIMER.md").read_text(
            encoding="utf-8"
        ) == expected_disclaimer
        assert (target / "docs" / "manual" / "00-index.en.md").is_file()


def test_grep_fallback_sanitizes_path_with_spaces(tmp_path: Path) -> None:
    source = _init_fixture_repo(
        tmp_path,
        "manual with spaces.md",
        "Use /path/to/zaofu for local development.\n",
    )
    target = tmp_path / "target"
    env = os.environ.copy()
    env["PATH"] = _restricted_path(tmp_path / "restricted")

    result = _run_export(source / "tools" / "export-public.sh", source, target, env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    exported = (target / "docs" / "manual" / "manual with spaces.md").read_text(
        encoding="utf-8"
    )
    assert "/home/user/" not in exported
    assert "/path/to/zaofu" in exported


def test_grep_fallback_fails_closed_on_scan_error(tmp_path: Path) -> None:
    source = _init_fixture_repo(tmp_path, "manual.md", "clean content\n")
    target = tmp_path / "target"
    env = os.environ.copy()
    env["PATH"] = _restricted_path(tmp_path / "restricted")
    env["ZF_EXPORT_PRIVATE_RG_PATTERN"] = "["

    result = _run_export(source / "tools" / "export-public.sh", source, target, env=env)

    assert result.returncode != 0
    assert "private sanitization scan failed" in result.stderr
