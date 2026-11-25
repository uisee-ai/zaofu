"""Observe rendered briefing size without making it a workflow gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


SCHEMA_VERSION = "briefing-metrics.v1"
_SOFT_BUDGETS = {"impl": 12 * 1024, "verify": 12 * 1024, "judge": 10 * 1024}


def write_briefing_with_metrics(
    path: Path,
    text: str,
    *,
    state_dir: Path,
    stage: str,
    role: str,
    payload: Mapping[str, Any] | None = None,
    declared_skills: Sequence[Any] | None = None,
    indexed_skills: Sequence[Any] = (),
    auto_injected_skills: Sequence[Any] = (),
) -> dict[str, Any]:
    """Atomically persist a briefing and its read-only metrics projection."""

    payload = payload or {}
    encoded = text.encode("utf-8")
    profile = _stage_profile(stage, role, payload)
    source_count = _source_count(Path(state_dir), payload)
    sections = _section_bytes(text)
    budget = _SOFT_BUDGETS.get(profile, 12 * 1024)
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "briefing_ref": str(path),
        "briefing_sha256": hashlib.sha256(encoded).hexdigest(),
        "briefing_bytes": len(encoded),
        "estimated_tokens": max(1, (len(encoded) + 3) // 4),
        "line_count": len(text.splitlines()),
        "section_bytes": sections,
        "stage": str(stage or ""),
        "role": str(role or ""),
        "stage_profile": profile,
        "output_profile_id": str(payload.get("output_profile_id") or ""),
        "output_profile_revision": str(payload.get("output_profile_revision") or ""),
        "required_source_count": source_count,
        "required_read_count": len(payload.get("required_reads") or []),
        "required_read_returned_bytes": "unknown",
        "indexed_skill_count": len(indexed_skills),
        "indexed_skills": _skill_names(indexed_skills),
        "auto_injected_skill_count": len(auto_injected_skills),
        "auto_injected_skills": _skill_names(auto_injected_skills),
        # Dynamic invocation evidence belongs to EventLog projection. A
        # briefing can only state that no provider evidence has been observed.
        "actually_invoked_skills": "unobserved",
        "soft_budget_bytes": budget,
        "soft_budget_exceeded": len(encoded) > budget,
    }
    if declared_skills is not None:
        names = _skill_names(declared_skills)
        metrics.update(declared_skills=names, declared_skill_count=len(names))
    atomic_write_text(path, text)
    metrics_path = path.with_suffix(path.suffix + ".metrics.json")
    atomic_write_text(
        metrics_path,
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return metrics


def refresh_briefing_metrics(path: Path) -> dict[str, Any]:
    """Refresh text-derived metrics after the final briefing envelope."""

    path = Path(path)
    metrics_path = path.with_suffix(path.suffix + ".metrics.json")
    if not path.is_file() or not metrics_path.is_file():
        return {}
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(metrics, dict):
        return {}
    text = path.read_text(encoding="utf-8")
    encoded = text.encode("utf-8")
    budget = int(metrics.get("soft_budget_bytes") or 12 * 1024)
    metrics.update({
        "briefing_sha256": hashlib.sha256(encoded).hexdigest(),
        "briefing_bytes": len(encoded),
        "estimated_tokens": max(1, (len(encoded) + 3) // 4),
        "line_count": len(text.splitlines()),
        "section_bytes": _section_bytes(text),
        "soft_budget_exceeded": len(encoded) > budget,
    })
    atomic_write_text(
        metrics_path,
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return metrics


def _section_bytes(text: str) -> dict[str, int]:
    sections: dict[str, int] = {}
    current = "preamble"
    chunks: dict[str, list[str]] = {current: []}
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            current = line[3:].strip() or "unnamed"
            chunks.setdefault(current, [])
        chunks[current].append(line)
    for name, lines in chunks.items():
        sections[name] = len("".join(lines).encode("utf-8"))
    return sections


def _source_count(state_dir: Path, payload: Mapping[str, Any]) -> int:
    descriptor = payload.get("attempt_source_manifest")
    if not isinstance(descriptor, Mapping):
        return 0
    try:
        manifest = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    except (OSError, ValueError):
        return 0
    sources = manifest.get("sources") if isinstance(manifest, Mapping) else None
    return len(sources) if isinstance(sources, list) else 0


def _stage_profile(stage: str, role: str, payload: Mapping[str, Any]) -> str:
    identity = " ".join((stage, role, str(payload.get("output_profile_id") or ""))).lower()
    if "judge" in identity or "closure" in identity:
        return "judge"
    if "verify" in identity or "review" in identity or "reader" in identity:
        return "verify"
    if "impl" in identity or "dev" in identity or "writer" in identity:
        return "impl"
    return "other"


def _skill_names(values: Sequence[Any]) -> list[str]:
    names: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            name = str(value.get("name") or value.get("skill") or "")
        else:
            name = str(getattr(value, "name", value) or "")
        if name and name not in names:
            names.append(name)
    return names


__all__ = [
    "SCHEMA_VERSION",
    "refresh_briefing_metrics",
    "write_briefing_with_metrics",
]
