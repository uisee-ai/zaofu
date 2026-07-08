"""Suggest a `project.scripts.setup` declaration for a freshly-initialized project.

CLI 与 Web 的 project init 共用:未声明 setup 且检测到依赖清单时,给出
建议命令;两个入口打通,避免只有 CLI 能看到 onboarding 提示。
"""

from __future__ import annotations

from pathlib import Path


def suggest_setup_script(project_root: Path) -> str:
    """Return a suggested setup command, or "" when none applies.

    已声明 project.scripts.setup(zf.yaml 同时含 scripts:/setup: 键)或
    无依赖清单时返回空串。
    """
    zf_yaml = project_root / "zf.yaml"
    try:
        text = zf_yaml.read_text(encoding="utf-8") if zf_yaml.exists() else ""
    except OSError:
        return ""
    if "scripts:" in text and "setup:" in text:
        return ""
    if (project_root / "package.json").exists():
        if (project_root / "pnpm-lock.yaml").exists():
            return "pnpm install"
        return "npm install"
    if (project_root / "pyproject.toml").exists():
        return "uv sync --extra dev"
    return ""
