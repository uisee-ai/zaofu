"""Apply modern matrix closure at the shared new-package admission boundary."""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.runtime.plan_artifact_package_ports import PlanArtifactPackageError
from zf.runtime.plan_matrix_admission import plan_matrix_admission_errors
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_REGISTRY_PORTS = frozenset({"capability_matrix", "acceptance_matrix", "test_matrix"})
_MATRIX_PORTS = _REGISTRY_PORTS | {"real_e2e_matrix"}


def validate_package_matrix_closure(
    *,
    state_dir: Path,
    task_map: Mapping[str, Any],
    ports: Iterable[Mapping[str, Any]],
    required_ports: Iterable[str],
) -> None:
    """Check declared complete registries without upgrading legacy contracts.

    Readiness and descriptor validation remain the caller's existing gates.
    Partial/un-enriched matrices and legacy textual acceptance rows retain
    their prior admission behavior; they do not claim modern matrix closure.
    """
    required = set(required_ports)
    if not _REGISTRY_PORTS <= required:
        return
    descriptors = {str(port.get("logical_name") or ""): port for port in ports}
    bodies: dict[str, Mapping[str, Any]] = {}
    for name in sorted(_MATRIX_PORTS & required):
        descriptor = descriptors.get(name)
        if descriptor is None:
            continue  # The package shape gate rejects missing required ports.
        hydrated = hydrate_sidecar_ref(state_dir, {
            "ref": str(descriptor.get("ref") or ""),
            "sha256": str(descriptor.get("sha256") or ""),
        })
        body = hydrated.payload
        if not isinstance(body, Mapping):
            continue
        metadata = body.get("metadata")
        enrichment = metadata.get("enrichment_contract") if isinstance(metadata, Mapping) else None
        if (str(body.get("status") or "").strip().lower() == "ready"
                and isinstance(enrichment, Mapping)
                and str(enrichment.get("status") or "").strip().lower() == "fulfilled"):
            bodies[name] = body
    if not _REGISTRY_PORTS <= bodies.keys():
        return
    if _legacy_acceptance_rows(task_map):
        return
    source_index: Mapping[str, Any] = {}
    if descriptor := descriptors.get("source_index"):
        hydrated = hydrate_sidecar_ref(state_dir, {
            "ref": str(descriptor.get("ref") or ""),
            "sha256": str(descriptor.get("sha256") or ""),
        })
        if isinstance(hydrated.payload, Mapping):
            source_index = hydrated.payload
    errors = plan_matrix_admission_errors(
        task_map=task_map, source_index=source_index,
        ports=bodies, required_ports=sorted(bodies),
    )
    if errors:
        details = "; ".join(f"{row['code']} at {row['field']}: {row['message']}" for row in errors)
        raise PlanArtifactPackageError("plan matrix admission failed: " + details)


def _legacy_acceptance_rows(task_map: Mapping[str, Any]) -> bool:
    """Only an entirely textual legacy dialect may retain legacy admission."""
    textual = structured = False
    canonical_commands = False
    for task in task_map.get("tasks", []):
        if not isinstance(task, Mapping):
            raise PlanArtifactPackageError("matrix acceptance requires structured task rows")
        validation = task.get("validation")
        canonical_commands |= isinstance(validation, Mapping) and "commands" in validation
        criteria = task.get("acceptance_criteria", task.get("acceptance", []))
        if not isinstance(criteria, list):
            raise PlanArtifactPackageError("matrix acceptance_criteria must be a list")
        for row in criteria:
            if isinstance(row, str):
                textual = True
            elif isinstance(row, Mapping):
                structured = True
            else:
                raise PlanArtifactPackageError("matrix acceptance row must be an object or legacy string")
    if textual and (structured or canonical_commands):
        raise PlanArtifactPackageError("matrix acceptance cannot mix legacy strings and structured rows")
    return textual
