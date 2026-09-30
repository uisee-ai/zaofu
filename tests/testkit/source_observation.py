"""Read-only source inventory shared by native and deterministic runners."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any


SOURCE_SCOPE = (
    "src",
    "tests",
    "scripts",
    "skills",
    ".claude/skills",
    ".codex/skills",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/rules",
    "pyproject.toml",
    "uv.lock",
    "zf.yaml",
)

ISSUE_LEDGER = "awesome-zaofu/New_function/Test ZaoFu_Project/ZaoFu_issue.md"
SOURCE_PROFILES = {
    "backend": SOURCE_SCOPE,
    "acceptance": (*SOURCE_SCOPE, ISSUE_LEDGER),
    "browser": (*SOURCE_SCOPE, ISSUE_LEDGER, "web"),
}


def scope_observation(source: dict, scope: tuple[str, ...]) -> dict:
    """Compare a declared shared scope without discarding the full inventory."""
    files = [row for row in source["files"] if any(
        row["path"] == prefix or row["path"].startswith(prefix + "/") for prefix in scope
    )]
    return {
        "git_head": source["git_head"], "scope": list(scope),
        "files_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest(),
    }


def scope_digests(source: dict) -> dict[str, str]:
    """Keep overlapping web/input scopes comparable across report formats."""
    return {prefix: scope_observation(source, (prefix,))["files_sha256"]
            for prefix in source["scope"]}


def capture_source(
    root: Path, *, scope: tuple[str, ...] = SOURCE_SCOPE
) -> dict[str, Any]:
    """Hash tracked and nonignored candidate sources; never copy their bodies."""
    root = root.resolve()

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=root, timeout=30)

    head = git("rev-parse", "HEAD").decode().strip()
    names = git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *scope,
    )
    files = []
    for name in sorted(set(os.fsdecode(item) for item in names.split(b"\0") if item)):
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"source escapes repository: {name}")
        if not path.exists():
            files.append({"path": name, "kind": "missing"})
            continue
        row = {
            "path": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": path.stat().st_mode & 0o777,
        }
        if path.is_symlink():
            row["link"] = os.readlink(path)
        files.append(row)
    if not files:
        raise ValueError("empty source inventory")
    encoded = json.dumps(files, sort_keys=True, ensure_ascii=True).encode()
    return {
        "schema_version": "zf-testkit-source-observation.v1",
        "root": str(root),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "git_head": head,
        "scope": list(scope),
        "files": files,
        "files_sha256": hashlib.sha256(encoded).hexdigest(),
    }
