"""Bound repeated, byte-equivalent Plan bodies without summarizing their meaning."""
from __future__ import annotations

import json
import re

from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def partition_repeated_candidate_bodies(state_dir, sources):
    """Only new rework manifests change; immutable original artifacts stay intact.

    Keep the first full body and every distinct field. Later large identical
    fields refer to that first required source. Partial grants and non-JSON
    artifacts keep their original scope. This is equality, not relevance.
    """
    known = {}
    result = []
    overlaps = []
    for source in sources:
        if (not source.get("source_id", "").startswith("previous-plan-candidate-")
                or source.get("allowed_paths") != ["$"]):
            result.append(source)
            continue
        try:
            body = hydrate_sidecar_ref(state_dir, source).payload
        except (ValueError, OSError):
            result.append(source)
            continue
        if not isinstance(body, dict) or not all(re.fullmatch(r"[A-Za-z0-9_-]+", k) for k in body):
            result.append(source)
            continue
        aliases, distinct = [], []
        for key, value in body.items():
            path = f"$.{key}"
            # Small scalars/metadata remain visible even when repeated.
            signature = (canonical_json_sha256(value)
                         if isinstance(value, (dict, list)) and len(json.dumps(value).encode()) >= 1024 else "")
            previous = known.get(signature) if signature else None
            if previous is not None and previous["body"] == value:
                aliases.append({"json_path": path, "source_id": previous["source_id"],
                                "source_sha256": previous["sha256"],
                                "source_json_path": previous["json_path"], "content_sha256": signature})
            else:
                distinct.append(path)
                if signature:
                    known[signature] = {"body": value, "source_id": source["source_id"],
                                        "sha256": source["sha256"], "json_path": path}
        if aliases and distinct:
            source = {**source, "kind": "plan_rework_candidate_partition",
                      "allowed_paths": distinct,
                      "access_scope": {**source.get("access_scope", {}), "read_equivalents": aliases}}
            overlaps.append({"source_id": source["source_id"], "sha256": source["sha256"],
                             "read_equivalents": aliases})
        result.append(source)
    # Expose the navigation in the existing mandatory rework context, not only
    # in manifest metadata that a worker's input index might omit.
    if overlaps:
        for index, source in enumerate(result):
            if source.get("source_id") == "plan-rework-context":
                body = hydrate_sidecar_ref(state_dir, source).payload
                descriptor = write_immutable_json_sidecar(state_dir, {**body, "read_equivalents": overlaps},
                    root="plan-synth/rework-context", kind="plan_rework_context",
                    schema_version="plan-rework-context.v1", created_by="plan-rework-input-overlap")
                result[index] = {**source, **descriptor}
    return result
