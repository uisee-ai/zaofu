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
    (source / "assets" / "readme").mkdir(parents=True)
    (source / "docs" / "manual").mkdir(parents=True)
    (source / ".claude" / "rules").mkdir(parents=True)
    (source / ".claude" / "commands").mkdir(parents=True)
    (source / ".claude" / "worktrees" / "private").mkdir(parents=True)
    (source / "yoke" / "context-hygiene").mkdir(parents=True)
    shutil.copy2(SCRIPT, source / "tools" / "export-public.sh")
    (source / "AGENTS.md").write_text("# Public agent rules\n", encoding="utf-8")
    (source / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (source / "zf.yaml").write_text("version: 1\n", encoding="utf-8")
    (source / "feishu.yaml").write_text(
        "feishu_identity:\n"
        "  verification_token_env: FEISHU_TOKEN\n"
        "  users:\n"
        '    "${FEISHU_OPENID:-ou_private_identity}":\n'
        "      operator: true\n"
        "feishu_routing:\n"
        '  "${FEISHU_CHAT_ID:-oc_private_chat}":\n'
        "    target: channel\n",
        encoding="utf-8",
    )
    (source / ".claude" / "rules" / "code.md").write_text(
        "# Public code rules\n", encoding="utf-8"
    )
    (source / ".claude" / "commands" / "audit-backlogs.md").write_text(
        "# Public audit command\n", encoding="utf-8"
    )
    (source / ".claude" / "settings.local.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (source / ".claude" / "worktrees" / "private" / "secret.txt").write_text(
        "private state\n", encoding="utf-8"
    )
    (source / "yoke" / "context-hygiene" / "SKILL.md").write_text(
        "---\nname: context-hygiene\n---\n", encoding="utf-8"
    )
    (source / "README.md").write_text("# Public fixture\n", encoding="utf-8")
    (source / "README.zh-CN.md").write_text("# Public fixture\n", encoding="utf-8")
    (source / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    (source / "DISCLAIMER.md").write_text("fixture disclaimer\n", encoding="utf-8")
    (source / "assets" / "readme" / "fixture.txt").write_text(
        "public readme asset\n", encoding="utf-8"
    )
    (source / "docs" / "manual" / manual_name).write_text(manual_text, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "add",
            "--",
            "AGENTS.md",
            "CLAUDE.md",
            "zf.yaml",
            "feishu.yaml",
            "README.md",
            "README.zh-CN.md",
            "LICENSE",
            "DISCLAIMER.md",
            "assets/readme/fixture.txt",
            ".claude/rules/code.md",
            ".claude/commands/audit-backlogs.md",
            ".claude/settings.local.json",
            ".claude/worktrees/private/secret.txt",
            "yoke/context-hygiene/SKILL.md",
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
        expected_disclaimer = subprocess.run(
            ["git", "show", "HEAD:DISCLAIMER.md"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert (target / "DISCLAIMER.md").read_text(
            encoding="utf-8"
        ) == expected_disclaimer
        for readme in ("README.md", "README.zh-CN.md"):
            expected_readme = subprocess.run(
                ["git", "show", f"HEAD:{readme}"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            assert (target / readme).read_text(encoding="utf-8") == expected_readme
        assert (target / "docs" / "manual" / "00-index.en.md").is_file()
        assert (target / "AGENTS.md").is_file()
        assert (target / "CLAUDE.md").is_file()
        assert (target / "zf.yaml").is_file()
        exported_feishu = (target / "feishu.yaml").read_text(encoding="utf-8")
        active_feishu = "\n".join(
            line.split("#", 1)[0] for line in exported_feishu.splitlines()
        )
        assert "${FEISHU_" in exported_feishu
        assert ":-" not in active_feishu
        for literal_prefix in ("ou_", "oc_", "cli_"):
            assert literal_prefix not in exported_feishu
        assert (target / "yoke" / "context-hygiene" / "SKILL.md").is_file()
        exported_skill_index = (target / "skills" / "INDEX.md").read_text(
            encoding="utf-8"
        )
        assert "`cangjie-" not in exported_skill_index
        assert (target / ".claude" / "rules" / "code.md").is_file()
        assert (
            target / ".claude" / "commands" / "audit-backlogs.md"
        ).is_file()
        assert not (target / ".claude" / "settings.local.json").exists()
        assert not (target / ".claude" / "worktrees").exists()


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
    assert (target / "assets" / "readme" / "fixture.txt").read_text(
        encoding="utf-8"
    ) == "public readme asset\n"
    assert (target / "AGENTS.md").is_file()
    assert (target / "CLAUDE.md").is_file()
    assert (target / "zf.yaml").is_file()
    exported_feishu = (target / "feishu.yaml").read_text(encoding="utf-8")
    assert "${FEISHU_OPENID}" in exported_feishu
    assert "${FEISHU_CHAT_ID}" in exported_feishu
    assert "ou_private_identity" not in exported_feishu
    assert "oc_private_chat" not in exported_feishu
    assert (target / "yoke" / "context-hygiene" / "SKILL.md").is_file()
    assert (target / ".claude" / "rules" / "code.md").is_file()
    assert not (target / ".claude" / "settings.local.json").exists()
    assert not (target / ".claude" / "worktrees").exists()


def test_grep_fallback_fails_closed_on_scan_error(tmp_path: Path) -> None:
    source = _init_fixture_repo(tmp_path, "manual.md", "clean content\n")
    target = tmp_path / "target"
    env = os.environ.copy()
    env["PATH"] = _restricted_path(tmp_path / "restricted")
    env["ZF_EXPORT_PRIVATE_RG_PATTERN"] = "["

    result = _run_export(source / "tools" / "export-public.sh", source, target, env=env)

    assert result.returncode != 0
    assert "private sanitization scan failed" in result.stderr


def test_export_rejects_literal_feishu_credentials(tmp_path: Path) -> None:
    source = _init_fixture_repo(tmp_path, "manual.md", "clean content\n")
    (source / "feishu.yaml").write_text(
        "app_secret: must-not-be-public\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "--", "feishu.yaml"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "unsafe fixture",
        ],
        cwd=source,
        check=True,
    )
    target = tmp_path / "target"
    env = os.environ.copy()

    result = _run_export(source / "tools" / "export-public.sh", source, target, env=env)

    assert result.returncode != 0
    assert "literal Feishu credential field exported" in result.stderr
