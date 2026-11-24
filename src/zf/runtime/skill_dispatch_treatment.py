"""Freeze the Skill treatment that a provider dispatch can actually read."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar


SKILL_DISPATCH_TREATMENT_SCHEMA = "skill-dispatch-treatment.v1"


class SkillDispatchTreatmentError(ValueError):
    """The selected overlay and materialized provider Skill set diverged."""


def freeze_skill_dispatch_treatment(
    *,
    state_dir: Path,
    role_instance: str,
    task_id: str,
    run_id: str,
    selected_overlays: Iterable[Mapping[str, Any]],
    lock_entries: Iterable[Any],
    manifest_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate selected overlays against both lock and provider manifest."""

    selected = sorted(
        [
            {
                "skill_name": str(row.get("skill_name") or "").strip(),
                "asset_id": str(row.get("asset_id") or "").strip(),
                "version": int(row.get("version") or 0),
                "candidate_digest": str(row.get("digest") or "").strip(),
            }
            for row in selected_overlays
        ],
        key=lambda row: row["skill_name"],
    )
    if selected and not task_id:
        raise SkillDispatchTreatmentError(
            "task_id is required for a scoped Skill overlay dispatch"
        )
    if any(not row["skill_name"] or not row["candidate_digest"] for row in selected):
        raise SkillDispatchTreatmentError("selected Skill overlay identity is incomplete")

    locked = {
        str(getattr(entry, "name", "") or ""): {
            "skill_name": str(getattr(entry, "name", "") or ""),
            "digest": str(getattr(entry, "sha256", "") or ""),
            "source": str(getattr(entry, "source", "") or ""),
            "status": str(getattr(entry, "status", "") or ""),
            **{k: getattr(entry, k) for k in ("auto_inject", "load_on_demand") if hasattr(entry, k)},
        }
        for entry in lock_entries
        if str(getattr(entry, "name", "") or "")
    }
    manifest_rows = manifest_payload.get("skills")
    manifest_rows = manifest_rows if isinstance(manifest_rows, list) else []
    materialized = {
        str(row.get("name") or ""): {
            "skill_name": str(row.get("name") or ""),
            "digest": str(row.get("sha256") or ""),
            "source": str(row.get("source") or ""),
            "status": str(row.get("status") or ""),
            "materialized_to": str(row.get("materialized_to") or ""),
            **{k: row[k] for k in ("auto_inject", "load_on_demand") if k in row},
        }
        for row in manifest_rows
        if isinstance(row, Mapping) and str(row.get("name") or "")
    }

    for name, member in materialized.items():
        for field in ("auto_inject", "load_on_demand"):
            if member.get(field) != locked.get(name, {}).get(field):
                raise SkillDispatchTreatmentError(f"Skill loading policy mismatch for {name!r}")

    for overlay in selected:
        name = overlay["skill_name"]
        expected = overlay["candidate_digest"]
        lock = locked.get(name)
        manifest = materialized.get(name)
        if lock is None or manifest is None:
            raise SkillDispatchTreatmentError(
                f"selected Skill overlay {name!r} was not materialized"
            )
        if lock["source"] != f"evolution-overlay://{name}":
            raise SkillDispatchTreatmentError(
                f"selected Skill overlay {name!r} resolved from canonical source"
            )
        if manifest["source"] != f"evolution-overlay://{name}":
            raise SkillDispatchTreatmentError(
                f"provider manifest for {name!r} does not identify the overlay"
            )
        if lock["digest"] != expected or manifest["digest"] != expected:
            raise SkillDispatchTreatmentError(
                f"selected Skill overlay {name!r} digest drift"
            )

    body = {
        "schema_version": SKILL_DISPATCH_TREATMENT_SCHEMA,
        "role_instance": role_instance,
        "task_id": task_id,
        "run_id": run_id,
        "selected_overlays": selected,
        "materialized_skills": [materialized[name] for name in sorted(materialized)],
    }
    try:
        return write_immutable_json_sidecar(
            state_dir,
            body,
            root="evolution/skill-dispatch-treatments",
            kind="skill_dispatch_treatment",
            schema_version=SKILL_DISPATCH_TREATMENT_SCHEMA,
            created_by="skill-dispatch",
        )
    except Exception as exc:
        raise SkillDispatchTreatmentError(
            f"failed to freeze Skill dispatch treatment: {exc}"
        ) from exc


__all__ = [
    "SKILL_DISPATCH_TREATMENT_SCHEMA",
    "SkillDispatchTreatmentError",
    "freeze_skill_dispatch_treatment",
]
