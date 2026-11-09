"""Keep admitted plan identity and its source references in one generation."""

from collections.abc import Mapping, Sequence
from typing import Any
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    hydrate_plan_artifact_package,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def bind_admitted_plan_source_refs(
    payload: dict[str, Any],
    current: Mapping[str, Any],
    events: Sequence[ZfEvent | Mapping[str, Any]],
    *,
    state_dir: Path | None = None,
) -> None:
    if state_dir is not None:
        if not current.get("package_ref") or not current.get("package_digest"):
            raise PlanArtifactPackageError("admitted plan package ref and digest are required")
        package = hydrate_plan_artifact_package(
            state_dir,
            {"ref": current.get("package_ref"), "sha256": current.get("package_digest")},
            validate_ports=False,
        )
        for key in ("workflow_run_id", "task_map_generation"):
            if package[key] != current.get(key):
                raise PlanArtifactPackageError(f"admitted plan {key} mismatch")
        ports = {
            port["logical_name"]: port
            for port in [*package["produced"], *package["inherited"]]
        }
        if "task_map" not in ports:
            raise PlanArtifactPackageError("admitted plan task_map port is missing")
        binding = {}
        for name in ("task_map", "source_index"):
            if name not in ports:
                continue
            port = ports[name]
            hydrate_sidecar_ref(state_dir, {"ref": port["ref"], "sha256": port["sha256"]})
            binding[f"{name}_ref"] = port["ref"]
            binding[f"{name}_digest"] = port["sha256"]
        for name in ("task_map", "source_index"):
            payload.pop(f"{name}_ref", None)
            payload.pop(f"{name}_digest", None)
        payload.update(binding)
        return
    # Pure event-only callers retain the legacy projection. Runtime entrypoints
    # must supply state_dir: admitted synth bodies can be stored only in sidecars.
    source_id = current.get("source_event_id")
    if not source_id:
        return
    for event in events:
        event_id = event.get("id") if isinstance(event, Mapping) else event.id
        if event_id != source_id:
            continue
        source = event.get("payload", {}) if isinstance(event, Mapping) else event.payload
        for ref_key, digest_key in (
            ("task_map_ref", "task_map_digest"),
            ("source_index_ref", "source_index_digest"),
        ):
            if ref_key in source:
                payload[ref_key] = source[ref_key]
                payload.pop(digest_key, None)
                if source.get(digest_key):
                    payload[digest_key] = source[digest_key]
        return
