"""Prod-flow catalog (doc 102 §6.2) — the validated archetype catalog.

The recommender's archetype catalog is the real, e2e-validated production
workflows in ``examples/prod/``, not synthetic presets. Each selectable flow is
registered by metadata inside the YAML file itself. This satisfies PB7's
"recommend only from a validated set" without requiring Python code changes for
every new prod flow. The flows are env-parameterised (ZF_STATE_DIR/...), so
materialising one is a file copy — no ``{project_name}`` templating.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class FlowCatalogEntry:
    id: str
    label: str
    description: str
    roles: int
    intent: str
    backend: str
    recommended_for: tuple[str, ...]
    preferred: bool
    order: int
    path: Path


_BACKEND_ORDER = {"claude": 0, "codex": 1}


def find_prod_dir() -> Path | None:
    """Locate examples/prod: env override > package-relative repo root > cwd."""
    candidates: list[Path] = []
    env = os.environ.get("ZF_EXAMPLES_DIR")
    if env:
        candidates.append(Path(env) / "prod")
    candidates.append(Path(__file__).resolve().parents[4] / "examples" / "prod")
    candidates.append(Path.cwd() / "examples" / "prod")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def flow_id(base: str, backend: str) -> str:
    backend = backend if backend in _BACKEND_ORDER else "claude"
    return f"{base}-{backend}"


def _catalog_entries() -> list[FlowCatalogEntry]:
    prod = find_prod_dir()
    if prod is None:
        return []
    entries: list[FlowCatalogEntry] = []
    paths = [*prod.glob("*.yaml"), *prod.glob("controller/*.yaml")]
    for path in sorted(paths):
        entry = _entry_from_yaml(path)
        if entry is not None:
            entries.append(entry)
    return sorted(
        entries,
        key=lambda e: (
            0 if e.preferred else 1,
            e.order,
            _BACKEND_ORDER.get(e.backend, 99),
            e.id,
        ),
    )


def _entry_from_yaml(path: Path) -> FlowCatalogEntry | None:
    try:
        documents = yaml.safe_load_all(path.read_text(encoding="utf-8"))
        for document in documents:
            if not isinstance(document, dict):
                continue
            metadata = document.get("metadata")
            if not isinstance(metadata, dict):
                continue
            zaofu_meta = metadata.get("zaofu")
            if not isinstance(zaofu_meta, dict):
                continue
            catalog = zaofu_meta.get("catalog")
            if isinstance(catalog, dict):
                return _entry_from_catalog(path, catalog)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return None
    return None


def _entry_from_catalog(path: Path, catalog: dict) -> FlowCatalogEntry | None:
    flow_id_value = str(catalog.get("id") or "").strip()
    backend = str(catalog.get("backend") or "").strip()
    intent = str(catalog.get("intent") or "").strip()
    description = str(catalog.get("description") or "").strip()
    if not flow_id_value or not backend or not intent or not description:
        return None
    try:
        roles = int(catalog.get("roles") or catalog.get("roleCount") or 0)
        order = int(catalog.get("order") or 1000)
    except (TypeError, ValueError):
        return None
    if roles <= 0:
        return None
    label = str(catalog.get("label") or "").strip() or flow_id_value
    recommended_raw = catalog.get("recommended_for") or catalog.get("recommendedFor")
    recommended_for: tuple[str, ...]
    if isinstance(recommended_raw, list):
        recommended_for = tuple(str(v).strip() for v in recommended_raw if str(v).strip())
    elif isinstance(recommended_raw, str) and recommended_raw.strip():
        recommended_for = (recommended_raw.strip(),)
    else:
        recommended_for = (intent,)
    if intent not in recommended_for:
        recommended_for = (intent, *recommended_for)
    preferred = bool(catalog.get("preferred") or catalog.get("default"))
    return FlowCatalogEntry(
        id=flow_id_value,
        label=label,
        description=description,
        roles=roles,
        intent=intent,
        backend=backend,
        recommended_for=recommended_for,
        preferred=preferred,
        order=order,
        path=path,
    )


def _entry_by_id(archetype: str) -> FlowCatalogEntry | None:
    return next((entry for entry in _catalog_entries() if entry.id == archetype), None)


def flow_id_for_intent(intent: str, backend: str = "claude") -> str | None:
    for entry in _catalog_entries():
        if intent in entry.recommended_for and entry.backend == backend:
            return entry.id
    return None


def is_flow_id(archetype: str) -> bool:
    return _entry_by_id(archetype) is not None


def flow_roles(archetype: str) -> int:
    entry = _entry_by_id(archetype)
    return entry.roles if entry else 0


def read_flow_yaml(archetype: str) -> str | None:
    """Return the prod yaml text for a flow id, or None if unavailable."""
    entry = _entry_by_id(archetype)
    if entry is None or not entry.path.is_file():
        return None
    return entry.path.read_text(encoding="utf-8")


def flow_path(archetype: str) -> Path | None:
    """Return the source YAML path for a flow id, or None if unavailable."""
    entry = _entry_by_id(archetype)
    return entry.path if entry is not None else None


def list_flows_detailed() -> list[dict]:
    """All YAML-registered flow archetypes for the wizard catalog."""
    return [
        {
            "id": entry.id,
            "label": entry.label,
            "description": entry.description,
            "roles": entry.roles,
            "kind": "flow",
            "intent": entry.intent,
            "backend": entry.backend,
            "preferred": entry.preferred,
            "available": True,
        }
        for entry in _catalog_entries()
    ]
