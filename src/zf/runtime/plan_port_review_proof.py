"""Recognize exact reviewed bytes and one kernel-owned identity derivation."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def port_has_prior_review(
    state_dir: Path, *, port: Mapping[str, Any], package: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> bool:
    from zf.runtime.plan_input_read_selection import complete_partition_read

    required = previous.get("_critic_required_reads") or []
    observed = previous.get("_critic_observed_reads") or []
    bindings = previous.get("_critic_source_bindings") or []
    reviewed = [source for source in previous.get("sources", [])
                if isinstance(source, Mapping) and any(
                    isinstance(binding, Mapping) and all(binding.get(k) == source.get(k)
                    for k in ("source_id", "artifact_id", "ref", "sha256")) for binding in bindings)
                and complete_partition_read(state_dir, source, required, observed)]
    if any(source.get("sha256") == port.get("sha256") for source in reviewed):
        return True
    if port.get("logical_name") != "product_acceptance_spec":
        return False

    from zf.runtime.plan_artifact_package_ports import bind_product_acceptance_identity
    from zf.runtime.product_acceptance import PRODUCT_ACCEPTANCE_SPEC_SCHEMA

    admitted = hydrate_sidecar_ref(state_dir, dict(port)).payload
    for source in reviewed:
        # Only JSON bodies can prove this transformation; Markdown/config
        # sources and merely cited refs cannot substitute for a full read.
        try:
            body = hydrate_sidecar_ref(state_dir, dict(source)).payload
        except ValueError:
            continue
        if not isinstance(body, Mapping):
            continue
        candidates = [body]
        if str(source.get("source_id") or "").startswith("child-result-"):
            entries = [row for row in body.get("plan_ports", []) if isinstance(row, Mapping)
                       and row.get("logical_name") == "product_acceptance_spec"]
            if len(entries) == 1 and isinstance(entries[0].get("body"), Mapping):
                candidates.append(entries[0]["body"])
        for candidate in candidates:
            if candidate.get("schema_version") != PRODUCT_ACCEPTANCE_SPEC_SCHEMA:
                continue
            bound = bind_product_acceptance_identity(candidate,
                workflow_run_id=package["workflow_run_id"], flow_kind=package["flow_kind"],
                plan_revision=package["plan_revision"], task_map_generation=package["task_map_generation"])
            if bound == admitted:
                return True
    return False
