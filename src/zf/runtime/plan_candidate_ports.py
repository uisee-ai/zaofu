"""Read Plan candidate ports under the same byte limit as Package consumers."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.call_result_envelope import canonical_json_bytes
from zf.runtime.plan_artifact_ports import canonical_plan_port_name, coerce_plan_port_descriptors
from zf.runtime.sidecar_refs import DEFAULT_HYDRATE_LIMIT_BYTES, SidecarRefError, sidecar_path
from zf.runtime.task_map import resolve_artifact_file


class PlanPortTooLarge(ValueError):
    """A producer port cannot be hydrated by downstream Package consumers."""


def check_plan_port_size(name: str, size: int) -> None:
    if size > DEFAULT_HYDRATE_LIMIT_BYTES:
        raise PlanPortTooLarge(
            f"plan port {name!r} exceeds sidecar hydration limit: "
            f"{size} > {DEFAULT_HYDRATE_LIMIT_BYTES} bytes"
        )


def read_plan_port_bytes(path: Path, name: str) -> bytes:
    check_plan_port_size(name, path.stat().st_size)
    with path.open("rb") as handle:
        raw = handle.read(DEFAULT_HYDRATE_LIMIT_BYTES + 1)
    check_plan_port_size(name, len(raw))
    return raw


def prepared_plan_port_path_errors(
    candidates: list[dict[str, Any]], *, state_dir: Path,
) -> list[dict[str, str]]:
    """After producer relocation, explicit refs must satisfy Package paths.

    Raw producer-local files are still valid before relocation. Do not copy
    arbitrary input paths or relax the sidecar boundary to make a port pass.
    Content, digest and size checks remain in the common candidate preflight.
    """
    errors: list[dict[str, str]] = []
    for candidate_index, candidate in enumerate(candidates):
        for index, port in enumerate(coerce_plan_port_descriptors(candidate.get("plan_ports"))):
            if isinstance(port.get("body"), Mapping):
                continue
            ref = str(port.get("ref") or "").strip()
            if not ref:
                continue  # Existing descriptor validation owns missing refs.
            try:
                sidecar_path(state_dir, ref)
            except (SidecarRefError, OSError, RuntimeError) as exc:
                name = str(port.get("logical_name") or port.get("artifact_kind") or "")
                _error(errors, "plan_port_not_materialized",
                       f"report[{candidate_index}].plan_ports[{index}].ref",
                       f"plan port {name!r} ref {ref!r} is not a Package sidecar: {exc}. "
                       "Use a digest-bound controlled sidecar or declare a producer-local "
                       "artifact for normal relocation; preserve the original content and digest.")
    return errors


def plan_port_bodies(
    candidates: list[dict[str, Any]],
    *,
    trigger: Mapping[str, Any],
    state_dir: Path,
    project_root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, tuple[dict[str, Any], str, bool]] = {}
    sources = [(dict(trigger), "trigger_payload"), *(
        (candidate, f"report[{index}]")
        for index, candidate in enumerate(candidates)
    )]
    direct_refs = {
        "source_inventory": ("source_inventory_ref",),
        "capability_matrix": ("capability_matrix_ref",),
        "acceptance_matrix": ("acceptance_matrix_ref",),
        "test_matrix": ("test_matrix_ref", "regression_test_matrix_ref"),
        "real_e2e_matrix": ("real_e2e_matrix_ref",),
        "source_index": ("source_index_ref",),
    }
    for source, label in sources:
        for index, descriptor in enumerate(
            coerce_plan_port_descriptors(source.get("plan_ports"))
        ):
            name = canonical_plan_port_name(str(
                descriptor.get("logical_name")
                or descriptor.get("artifact_kind")
                or descriptor.get("kind")
                or ""
            ))
            if name:
                selected[name] = (descriptor, f"{label}.plan_ports[{index}]", True)
        for name, keys in direct_refs.items():
            if name in selected and label == "trigger_payload":
                continue
            inline = source.get(name)
            if isinstance(inline, Mapping) and inline:
                selected[name] = (
                    {"logical_name": name, "body": dict(inline)},
                    f"{label}.{name}",
                    False,
                )
                continue
            ref = next(
                (str(source.get(key) or "").strip() for key in keys if str(source.get(key) or "").strip()),
                "",
            )
            if ref and not any(
                canonical_plan_port_name(str(item.get("logical_name") or "")) == name
                for item in coerce_plan_port_descriptors(source.get("plan_ports"))
            ):
                selected[name] = ({"logical_name": name, "ref": ref}, f"{label}.{keys[0]}", False)

    bodies: dict[str, Mapping[str, Any]] = {}
    for name, (descriptor, field, explicit) in selected.items():
        body = descriptor.get("body")
        if isinstance(body, Mapping):
            try:
                check_plan_port_size(name, len(canonical_json_bytes(body)))
            except PlanPortTooLarge as exc:
                _error(errors, "plan_port_too_large", field, str(exc))
                continue
            bodies[name] = dict(body)
            continue
        ref = str(descriptor.get("ref") or "").strip()
        if not ref:
            if explicit:
                _error(errors, "plan_port_unreadable", field,
                       "explicit plan port requires ref/sha256 or an inline body")
            continue
        try:
            expected = str(
                descriptor.get("sha256") or descriptor.get("digest") or ""
            ).strip()
            if explicit:
                # Package admission hydrates explicit descriptors as sidecars;
                # only direct *_ref inputs are materialized from project paths.
                if not expected:
                    raise ValueError("explicit plan port requires ref/sha256 or an inline body")
                path = sidecar_path(state_dir, ref)
            else:
                path = resolve_artifact_file(ref, project_root=project_root, state_dir=state_dir)
            raw = read_plan_port_bytes(path, name)
            actual = hashlib.sha256(raw).hexdigest()
            if expected and expected != actual:
                raise ValueError(
                    f"sha256 mismatch: expected {expected}, got {actual}"
                )
            value = json.loads(raw)
            if not isinstance(value, Mapping):
                raise ValueError("plan port must contain a JSON object")
            bodies[name] = dict(value)
        except PlanPortTooLarge as exc:
            _error(errors, "plan_port_too_large", field, str(exc))
        except Exception as exc:
            _error(errors, "plan_port_unreadable", field, str(exc))
    from zf.runtime.task_scoped_plan_inputs import SCHEMA_VERSION

    for name, body in bodies.items():
        descriptor, field, _explicit = selected[name]
        if (descriptor.get("schema_version") == SCHEMA_VERSION
                and body.get("schema_version") != SCHEMA_VERSION):
            _error(errors, "plan_port_schema_mismatch", field,
                   f"declared {SCHEMA_VERSION!r} requires the same body.schema_version; "
                   "put schema_version, shared, and tasks[] in the actual port body")
    return bodies



def _error(errors: list[dict[str, str]], code: str, field: str, message: str) -> None:
    errors.append({"code": code, "field": field, "message": message})
