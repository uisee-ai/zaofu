"""Deterministic stack detector (doc 102 §4.2).

Zero LLM, zero wall-clock (I3-safe). File-signature matching + monorepo subdir
walk + node ``package.json`` actual-scripts-preferred. ``detect()`` returns a
:class:`ProjectProfile`; ``detected_at`` is left blank for the caller to stamp.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from zf.core.profile.project_types import (
    PROJECT_TYPES,
    ProjectType,
    type_for_key_file,
)
from zf.core.profile.schema import ProjectProfile, StackUnit


def declared_profile(stack: str, surface: str = "") -> ProjectProfile:
    """Build a ``confidence=declared`` profile from a declared stack id (from-0).

    ``surface`` (backend/frontend/fullstack/library) overrides the type default,
    so an operator can declare a from-0 fullstack project before any code exists.
    """
    match = next((pt for pt in PROJECT_TYPES if pt.type_id == stack), None)
    if match is None:
        ids = [pt.type_id for pt in PROJECT_TYPES]
        raise ValueError(f"unknown stack {stack!r}; known: {ids}")
    unit = StackUnit(
        root=".", language=match.language, surface=surface or match.surface,
        test_cmd=match.test_cmd, gate_cmds=match.gate_cmds, has_tests=False,
    )
    return ProjectProfile(units=(unit,), layout="single", confidence="declared",
                          source_signals=(f"declared:{stack}",))


def detect(project_root: str | Path) -> ProjectProfile:
    root = Path(project_root).resolve()
    signals: list[str] = []

    member_dirs = _find_workspace_members(root, signals)
    layout = "monorepo" if member_dirs else "single"

    if member_dirs:
        units = [u for d in member_dirs if (u := _detect_unit(root, d, signals))]
    else:
        unit = _detect_unit(root, root, signals)
        units = [unit] if unit else []
        if units:
            # polyglot single-repo (e.g. python backend + web/ frontend): probe
            # conventional companion dirs for a second stack the root manifest hides.
            units += _probe_companions(root, units, signals)
        else:
            # cangjie-style: root has no manifest — scan one level of subdirs (PB4)
            sub_units = _scan_subdirs(root, signals)
            if sub_units:
                units = sub_units
                layout = "monorepo"

    if not units:
        return ProjectProfile(
            units=(StackUnit(),), layout="single", confidence="low",
            source_signals=tuple(signals),
        )
    return ProjectProfile(
        units=tuple(units), layout=layout, confidence="high",
        source_signals=tuple(signals),
    )


# ---------------------------------------------------------------- unit detect


def _detect_unit(root: Path, unit_dir: Path, signals: list[str]) -> StackUnit | None:
    pt = _match_type(unit_dir)
    if pt is None:
        return None
    rel = _rel(root, unit_dir)
    signals.append(f"{rel}:{pt.type_id}")
    if pt.type_id == "node":
        return _node_unit(unit_dir, rel, pt)
    return StackUnit(
        root=rel,
        language=pt.language,
        surface=pt.surface,
        test_cmd=pt.test_cmd,
        gate_cmds=pt.gate_cmds,
        has_tests=_has_tests(unit_dir, pt.language),
    )


def _match_type(unit_dir: Path) -> ProjectType | None:
    for pt in PROJECT_TYPES:
        if any((unit_dir / kf).exists() for kf in pt.key_files):
            return pt
    return None


def _node_unit(unit_dir: Path, rel: str, pt: ProjectType) -> StackUnit:
    pkg = _read_json(unit_dir / "package.json") or {}
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        d = pkg.get(key)
        if isinstance(d, dict):
            deps.update(d)
    dep_names = set(deps)
    frameworks = tuple(d for d in pt.frontend_deps if d in dep_names)
    has_fe = bool(frameworks) or any(
        (unit_dir / c).exists() for c in pt.frontend_configs
    )
    has_be = any(d in dep_names for d in pt.backend_deps)
    if has_fe and has_be:
        surface = "fullstack"
    elif has_fe:
        surface = "frontend"
    elif has_be:
        surface = "backend"
    else:
        surface = "library" if pkg.get("main") or pkg.get("exports") else "backend"

    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    gate_cmds: list[str] = []
    for name in ("lint", "typecheck", "check"):
        if name in scripts:
            gate_cmds.append(f"npm run {name}")
    test_cmd = "npm test" if "test" in scripts else ""
    if test_cmd:
        gate_cmds.append(test_cmd)
    if not gate_cmds:  # fall back to type defaults
        gate_cmds = list(pt.gate_cmds)
        test_cmd = test_cmd or pt.test_cmd
    build_cmd = "npm run build" if "build" in scripts else ""
    has_tests = "test" in scripts or (unit_dir / "__tests__").is_dir()
    return StackUnit(
        root=rel,
        language="node",
        frameworks=frameworks,
        surface=surface,
        build_cmd=build_cmd,
        test_cmd=test_cmd,
        gate_cmds=tuple(gate_cmds),
        has_tests=has_tests,
    )


# ------------------------------------------------------------- monorepo walk


def _find_workspace_members(root: Path, signals: list[str]) -> list[Path]:
    members: list[Path] = []

    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.exists():
        signals.append("workspace:pnpm-workspace.yaml")
        members += _expand_globs(root, _yaml_packages(pnpm))

    pkg = _read_json(root / "package.json") or {}
    ws = pkg.get("workspaces")
    globs = ws if isinstance(ws, list) else (
        ws.get("packages") if isinstance(ws, dict) else None
    )
    if globs:
        signals.append("workspace:package.json#workspaces")
        members += _expand_globs(root, [g for g in globs if isinstance(g, str)])

    gowork = root / "go.work"
    if gowork.exists():
        signals.append("workspace:go.work")
        for line in gowork.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("use ") or line.startswith("\tuse"):
                p = line.split("use", 1)[1].strip().strip("()").strip()
                if p:
                    members += _expand_globs(root, [p])

    pyproject = _read_toml(root / "pyproject.toml")
    uv_members = (
        pyproject.get("tool", {}).get("uv", {}).get("workspace", {}).get("members")
        if pyproject else None
    )
    if uv_members:
        signals.append("workspace:pyproject[tool.uv.workspace]")
        members += _expand_globs(root, [m for m in uv_members if isinstance(m, str)])

    cargo = _read_toml(root / "Cargo.toml")
    cargo_members = cargo.get("workspace", {}).get("members") if cargo else None
    if cargo_members:
        signals.append("workspace:Cargo.toml[workspace]")
        members += _expand_globs(root, [m for m in cargo_members if isinstance(m, str)])

    # dedupe, keep dirs that actually have a key file
    seen: list[Path] = []
    for m in members:
        if m not in seen and _match_type(m) is not None:
            seen.append(m)
    return seen


_COMPANION_DIRS = (
    "web", "frontend", "ui", "client", "webapp", "app",
    "server", "backend", "api",
)


def _probe_companions(
    root: Path, existing: list[StackUnit], signals: list[str]
) -> list[StackUnit]:
    """Catch a polyglot second stack in a conventional sibling dir."""
    have = {u.root for u in existing}
    extra: list[StackUnit] = []
    for name in _COMPANION_DIRS:
        child = root / name
        if not child.is_dir() or name in have:
            continue
        if _match_type(child) is not None:
            u = _detect_unit(root, child, signals)
            if u and u.root not in have:
                have.add(u.root)
                extra.append(u)
    if extra:
        signals.append("polyglot:companion-dirs")
    return extra


def _scan_subdirs(root: Path, signals: list[str]) -> list[StackUnit]:
    """Fallback for a manifest-less monorepo root (cangjie-style): scan depth-1."""
    units: list[StackUnit] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if _match_type(child) is not None:
            u = _detect_unit(root, child, signals)
            if u:
                units.append(u)
    if units:
        signals.append("monorepo:subdir-scan")
    return units


def _expand_globs(root: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        pat = pat.strip().strip("'\"")
        if not pat:
            continue
        if any(ch in pat for ch in "*?[]"):
            out += [p for p in root.glob(pat) if p.is_dir()]
        else:
            cand = (root / pat).resolve()
            if cand.is_dir():
                out.append(cand)
    return out


# ------------------------------------------------------------------- helpers


def _yaml_packages(path: Path) -> list[str]:
    """Tiny packages-list reader for pnpm-workspace.yaml (no yaml dep needed)."""
    pkgs: list[str] = []
    in_block = False
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("packages:"):
            in_block = True
            inline = s.split("packages:", 1)[1].strip()
            if inline.startswith("["):
                return [p.strip().strip("'\"") for p in inline.strip("[]").split(",") if p.strip()]
            continue
        if in_block:
            if s.startswith("- "):
                pkgs.append(s[2:].strip().strip("'\""))
            elif s and not s.startswith("#"):
                break
    return pkgs


def _has_tests(unit_dir: Path, language: str) -> bool:
    if (unit_dir / "tests").is_dir() or (unit_dir / "test").is_dir():
        return True
    if language == "go":
        return any(unit_dir.glob("*_test.go")) or any(unit_dir.glob("*/*_test.go"))
    if language == "python":
        return any(unit_dir.glob("test_*.py")) or any(unit_dir.glob("*/test_*.py"))
    return False


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_toml(path: Path) -> dict | None:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return None


def _rel(root: Path, unit_dir: Path) -> str:
    try:
        r = unit_dir.resolve().relative_to(root.resolve())
        return str(r) if str(r) != "." else "."
    except ValueError:
        return str(unit_dir)
