"""Deterministic project-type registry (doc 102 §4.1).

Table-driven so adding a new stack is one entry, not new detector logic. Each
entry is matched by file signatures; node entries refine frontend/backend by
dependency markers and prefer the project's actual ``package.json`` scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectType:
    type_id: str
    language: str
    key_files: tuple[str, ...]  # any-of presence marks a unit of this type
    surface: str  # default surface hint
    test_cmd: str
    gate_cmds: tuple[str, ...]
    critical_dirs: tuple[str, ...]
    # node-only refinement
    frontend_deps: tuple[str, ...] = ()
    backend_deps: tuple[str, ...] = ()
    frontend_configs: tuple[str, ...] = ()
    # monorepo workspace markers this type understands
    workspace_files: tuple[str, ...] = ()


PROJECT_TYPES: tuple[ProjectType, ...] = (
    ProjectType(
        type_id="python",
        language="python",
        key_files=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
        surface="backend",
        test_cmd="pytest",
        gate_cmds=("ruff check .", "pytest"),
        critical_dirs=("src", "tests"),
        workspace_files=("pyproject.toml",),  # [tool.uv.workspace]
    ),
    ProjectType(
        type_id="node",
        language="node",
        key_files=("package.json",),
        surface="backend",  # refined to frontend/backend at detect time
        test_cmd="npm test",
        gate_cmds=("npm run lint", "npm test"),
        critical_dirs=("src",),
        frontend_deps=(
            "react", "react-dom", "vue", "next", "vite", "svelte",
            "@angular/core", "solid-js",
        ),
        backend_deps=("express", "fastify", "@nestjs/core", "koa", "hapi"),
        frontend_configs=(
            "vite.config.ts", "vite.config.js", "next.config.js",
            "next.config.mjs", "svelte.config.js", "angular.json",
        ),
        workspace_files=("pnpm-workspace.yaml", "package.json"),  # workspaces[]
    ),
    ProjectType(
        type_id="go",
        language="go",
        key_files=("go.mod",),
        surface="backend",
        test_cmd="go test ./...",
        gate_cmds=("go vet ./...", "go test ./..."),
        critical_dirs=("cmd", "internal", "pkg"),
        workspace_files=("go.work",),
    ),
    ProjectType(
        type_id="rust",
        language="rust",
        key_files=("Cargo.toml",),
        surface="backend",
        test_cmd="cargo test",
        gate_cmds=("cargo clippy", "cargo test"),
        critical_dirs=("src",),
        workspace_files=("Cargo.toml",),  # [workspace]
    ),
)


def type_for_key_file(name: str) -> ProjectType | None:
    """Return the project type whose key_files include ``name`` (first match)."""
    for pt in PROJECT_TYPES:
        if name in pt.key_files:
            return pt
    return None
