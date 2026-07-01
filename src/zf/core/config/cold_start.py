"""Cold-start validation — 5-question readiness check."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zf.core.config.schema import ZfConfig


@dataclass
class ColdStartResult:
    score: int  # 0-5
    checks: list[tuple[str, bool, str]]  # (name, passed, detail)


def cold_start_check(workspace: Path, config: ZfConfig) -> ColdStartResult:
    """Run 5 cold-start readiness checks."""
    checks: list[tuple[str, bool, str]] = []

    # 1. Required doc files exist
    required_docs = ["README.md", "AGENTS.md", "CLAUDE.md"]
    docs_exist = all((workspace / d).exists() for d in required_docs)
    missing = [d for d in required_docs if not (workspace / d).exists()]
    checks.append(("docs_exist", docs_exist, f"Missing: {missing}" if missing else "OK"))

    # 2. Project structure comprehensible
    key_dirs = ["src", "tests"]
    structure_ok = all((workspace / d).exists() for d in key_dirs)
    checks.append(("project_structure", structure_ok,
                    "OK" if structure_ok else f"Missing dirs: {[d for d in key_dirs if not (workspace / d).exists()]}"))

    # 3. Role CLAUDE.md files present (optional — roles/ may not exist yet)
    roles_dir = workspace / "roles"
    if roles_dir.exists():
        role_docs_ok = all(
            (roles_dir / role.name / "CLAUDE.md").exists()
            for role in config.roles
        )
    else:
        role_docs_ok = True  # skip if no roles/ dir
    checks.append(("role_docs", role_docs_ok, "OK" if role_docs_ok else "Missing role CLAUDE.md files"))

    # 4. State directory initialized
    state_dir = workspace / config.project.state_dir
    state_ok = state_dir.exists() and (state_dir / "events.jsonl").exists()
    checks.append(("state_init", state_ok, "OK" if state_ok else f"{state_dir} not initialized"))

    # 5. Config valid
    config_ok = bool(config.project.name)
    checks.append(("config_valid", config_ok, "OK" if config_ok else "project.name missing"))

    score = sum(1 for _, passed, _ in checks if passed)
    return ColdStartResult(score=score, checks=checks)
