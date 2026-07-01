"""Relocate reader fanout artifacts into kernel-readable state storage."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.safety import PathGuard, PathGuardError
from zf.runtime.workdirs import WorkdirManager


_SCALAR_REF_KEYS = (
    "plan_artifact_ref",
    "plan_ref",
    "task_map_ref",
    "source_index_ref",
    "backlog_ref",
    "prd_ref",
    "spec_ref",
    "research_ref",
    "scan_quality_audit_ref",
    "inventory_ref",
    "source_inventory_ref",
    "hermes_source_inventory_ref",
    "inventory_coverage_matrix_ref",
    "expected_module_parity_report_paths_ref",
)
_LIST_REF_KEYS = ("artifact_refs", "evidence_refs", "report_refs", "inventory_refs")
_REF_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def relocate_fanout_artifact_refs(
    *,
    payload: dict[str, Any],
    payload_sources: list[dict[str, Any]],
    manifest: dict[str, Any],
    state_dir: Path,
    project_root: Path,
    config: ZfConfig,
    roles: list[RoleConfig],
) -> dict[str, Any]:
    """Copy unreadable workdir-local refs to ``state_dir/artifacts``.

    Reader workers commonly emit repo-relative refs such as
    ``docs/plans/task_map.json`` from their detached workdir. Downstream kernel
    gates resolve refs from the main project root or state dir, so those refs
    must become kernel-readable before publishing aggregate handoff events.
    """

    rewritten = dict(payload)
    ref_sources = _build_ref_sources(payload_sources)
    replacements: dict[str, str] = {}
    fanout_id = str(manifest.get("fanout_id") or "fanout")
    for ref in _payload_refs(rewritten):
        relocated = _relocate_ref(
            ref=ref,
            ref_sources=ref_sources,
            fanout_id=fanout_id,
            state_dir=state_dir,
            project_root=project_root,
            config=config,
            roles=roles,
        )
        if relocated and relocated != ref:
            replacements[ref] = relocated
    if not replacements:
        return rewritten
    for key in _SCALAR_REF_KEYS:
        value = str(rewritten.get(key) or "")
        if value in replacements:
            rewritten[key] = replacements[value]
    for key in _LIST_REF_KEYS:
        raw = rewritten.get(key)
        if not isinstance(raw, list):
            continue
        rewritten[key] = [
            replacements.get(str(item), str(item))
            for item in raw
            if str(item)
        ]
    return rewritten


def _build_ref_sources(
    payload_sources: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for source in payload_sources:
        if not isinstance(source, dict):
            continue
        for ref in _payload_refs(source):
            out.setdefault(ref, []).append(source)
    return out


def _payload_refs(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in _SCALAR_REF_KEYS:
        value = payload.get(key)
        if value not in (None, ""):
            refs.append(str(value))
    for key in _LIST_REF_KEYS:
        raw = payload.get(key)
        if isinstance(raw, list):
            refs.extend(str(item) for item in raw if str(item))
    report = payload.get("report")
    if isinstance(report, dict):
        refs.extend(_payload_refs(report))
    return _dedupe(refs)


def _relocate_ref(
    *,
    ref: str,
    ref_sources: dict[str, list[dict[str, Any]]],
    fanout_id: str,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig,
    roles: list[RoleConfig],
) -> str:
    if _is_external_ref(ref):
        return ref
    if _resolve_existing(ref, state_dir=state_dir, project_root=project_root) is not None:
        return ref
    source = _find_workdir_source(
        ref=ref,
        source_payloads=ref_sources.get(ref, []),
        state_dir=state_dir,
        project_root=project_root,
        config=config,
        roles=roles,
    )
    if source is None:
        return ref
    source_path, source_root, source_label = source
    dest_rel = _destination_ref(
        ref=ref,
        source_path=source_path,
        source_root=source_root,
        fanout_id=fanout_id,
        source_label=source_label,
    )
    dest = state_dir / dest_rel
    try:
        PathGuard.assert_under(dest, state_dir / "artifacts" / "fanouts")
    except PathGuardError:
        return ref
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, dest)
    return dest_rel.as_posix()


def _resolve_existing(
    ref: str,
    *,
    state_dir: Path,
    project_root: Path,
) -> Path | None:
    path = Path(ref)
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    elif path.parts and path.parts[0] == ".zf":
        candidates.append(state_dir.joinpath(*path.parts[1:]))
    else:
        candidates.extend([state_dir / path, project_root / path])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _find_workdir_source(
    *,
    ref: str,
    source_payloads: list[dict[str, Any]],
    state_dir: Path,
    project_root: Path,
    config: ZfConfig,
    roles: list[RoleConfig],
) -> tuple[Path, Path, str] | None:
    path = Path(ref)
    for source_payload in source_payloads:
        for root, label in _candidate_roots(
            source_payload,
            state_dir=state_dir,
            project_root=project_root,
            config=config,
            roles=roles,
        ):
            candidate = path if path.is_absolute() else root / path
            try:
                resolved = candidate.resolve()
                PathGuard.assert_under(resolved, root.resolve())
            except (OSError, RuntimeError, PathGuardError):
                continue
            if resolved.exists() and resolved.is_file():
                return resolved, root.resolve(), label
    return None


def _candidate_roots(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig,
    roles: list[RoleConfig],
) -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []
    label = _payload_label(payload)
    workdir = str(payload.get("workdir") or "").strip()
    if workdir:
        root = Path(workdir)
        roots.append((root if root.name == "project" else root / "project", label))
    role_instance = str(
        payload.get("role_instance")
        or payload.get("actor")
        or payload.get("child_id")
        or ""
    ).strip()
    if role_instance:
        role = next(
            (
                role
                for role in roles
                if role.instance_id == role_instance or role.name == role_instance
            ),
            None,
        )
        if role is not None:
            try:
                plan = WorkdirManager(
                    state_dir=state_dir,
                    project_root=project_root,
                    config=config,
                ).plan(role)
                roots.append((Path(plan.project_path), label))
            except Exception:
                pass
        roots.append((state_dir / "workdirs" / role_instance / "project", label))
    return _dedupe_roots(roots)


def _destination_ref(
    *,
    ref: str,
    source_path: Path,
    source_root: Path,
    fanout_id: str,
    source_label: str,
) -> Path:
    try:
        rel = source_path.relative_to(source_root)
    except ValueError:
        rel = Path(Path(ref).name or source_path.name)
    return (
        Path("artifacts")
        / "fanouts"
        / _safe_part(fanout_id)
        / _safe_part(source_label or "source")
        / _safe_rel(rel)
    )


def _is_external_ref(ref: str) -> bool:
    text = ref.strip()
    return bool(_REF_SCHEME_RE.match(text)) or text.startswith("#")


def _payload_label(payload: dict[str, Any]) -> str:
    return str(
        payload.get("role_instance")
        or payload.get("child_id")
        or payload.get("actor")
        or "source"
    ).strip()


def _safe_part(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    return safe.strip("._") or "source"


def _safe_rel(path: Path) -> Path:
    raw_parts = list(path.parts)
    if raw_parts and raw_parts[0] == ".zf":
        raw_parts = raw_parts[1:]
    parts = [
        _safe_part(part)
        for part in raw_parts
        if part not in ("", ".", "..")
    ]
    return Path(*parts) if parts else Path("artifact")


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _dedupe_roots(values: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    seen: set[tuple[str, str]] = set()
    for root, label in values:
        key = (str(root), label)
        if key in seen:
            continue
        seen.add(key)
        out.append((root, label))
    return out
